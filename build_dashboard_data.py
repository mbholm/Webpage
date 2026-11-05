"""
Fetch the data behind the Norway dashboard (dashboard.html) and write data/dashboard.json.

Run:  python build_dashboard_data.py
The GitHub Action in .github/workflows/dashboard-data.yml runs it every morning and commits the result.

Sources
  - Statistics Norway (SSB), PxWebApi v2:  https://data.ssb.no/api/pxwebapi/v2
  - Norges Bank, SDMX REST API:             https://data.norges-bank.no/api
  - NBIM, the fund's value page (year-end market values, scraped from the page's own chart data)

Design
  - Each indicator is fetched independently. If one source fails, the previous data for that
    indicator is kept (read from the existing data/dashboard.json) and the error is recorded, so a
    single outage never blanks the whole dashboard.
  - The JSON holds data and source metadata only. Titles, explanations and chart layout live in
    dashboard.js, keyed by the same indicator ids.
  - Derived series (TFP, labour productivity, current account in per cent of GDP, year-on-year
    changes) are computed here and marked "derived": true with a note on the formula.

Series schema (inside each indicator's "series" object):
  { "label": str, "unit": str, "freq": "D"|"M"|"Q"|"A", "decimals": int,
    "periods": ["2026-09", ...], "values": [number|null, ...], "derived": bool (optional), "note": str (optional) }
Period formats: daily "YYYY-MM-DD", monthly "YYYY-MM", quarterly "YYYY-Qn", annual "YYYY".
"""
from __future__ import annotations

import csv
import io
import json
import math
import re
import sys
import time
import traceback
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent
OUT = ROOT / "data" / "dashboard.json"
SSB = "https://data.ssb.no/api/pxwebapi/v2"
NB = "https://data.norges-bank.no/api"
UA = {"User-Agent": "martinbholm.com dashboard builder (python-urllib)"}

# --------------------------------------------------------------------------- HTTP helpers

def http_get(url: str, headers: dict | None = None, timeout: int = 90) -> bytes:
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def ssb_data(table: str, selection: list[tuple[str, list[str]]], lang: str = "en") -> dict:
    """POST a selection to SSB and return the json-stat2 dataset."""
    body = json.dumps({"selection": [{"variableCode": k, "valueCodes": v} for k, v in selection]}).encode()
    req = urllib.request.Request(f"{SSB}/tables/{table}/data?outputFormat=json-stat2&lang={lang}",
                                 data=body, headers={**UA, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read().decode("utf-8"))
    time.sleep(0.6)  # stay well inside SSB's rate limit
    return data


def ssb_period(p: str) -> str:
    """SSB period codes -> ISO-like: 2026M09 -> 2026-09, 2026K2 -> 2026-Q2, 2026 -> 2026."""
    m = re.fullmatch(r"(\d{4})M(\d{2})", p)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    m = re.fullmatch(r"(\d{4})K(\d)", p)
    if m:
        return f"{m.group(1)}-Q{m.group(2)}"
    return p


def ssb_series(ds: dict) -> tuple[list[str], dict[tuple, list], dict]:
    """
    Unpack a json-stat2 dataset.
    Returns (periods, {(code, code, ...) for all non-time dims: [values]}, meta)
    """
    dims = ds["id"]
    sizes = ds["size"]
    values = ds["value"]
    tpos = dims.index("Tid")
    assert tpos == len(dims) - 1, "time is expected to be the last dimension"
    periods = [ssb_period(p) for p in ds["dimension"]["Tid"]["category"]["index"]]
    codes = [list(ds["dimension"][d]["category"]["index"].keys()) for d in dims[:-1]]
    out: dict[tuple, list] = {}
    import itertools
    for combo in itertools.product(*[range(n) for n in sizes[:-1]]):
        offset = 0
        for i, c in enumerate(combo):
            offset = offset * sizes[i] + c
        base = offset * sizes[-1]
        out[tuple(codes[i][c] for i, c in enumerate(combo))] = values[base:base + sizes[-1]]
    units = ds["dimension"].get("ContentsCode", {}).get("category", {}).get("unit", {})
    meta = {"updated": ds.get("updated", "")[:10], "label": ds.get("label", ""), "units": units,
            "notes": [n for n in (ds.get("note") or [])][:3]}
    return periods, out, meta


def nb_csv(flow: str, key: str, **params) -> list[dict]:
    """Fetch a Norges Bank dataflow as sdmx-csv and return a list of row dicts (keys without labels)."""
    q = {"format": "sdmx-csv", "locale": "en", **params}
    url = f"{NB}/data/{flow}/{key}?{urllib.parse.urlencode(q)}"
    raw = http_get(url).decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(raw))
    rows = []
    for row in reader:
        clean = {}
        for k, v in row.items():
            k2 = k.split(":")[0]
            clean[k2] = v.split(": ")[0] if (v and k2 not in ("TIME_PERIOD", "OBS_VALUE")) else v
        rows.append(clean)
    time.sleep(0.3)
    return rows


def nb_series(flow: str, key: str, split_by: str | None = None, **params) -> dict[str, tuple[list, list]]:
    """Return {dim_value: (periods, values)}; one entry (key '') if split_by is None."""
    rows = nb_csv(flow, key, **params)
    out: dict[str, tuple[list, list]] = {}
    for r in rows:
        k = r.get(split_by, "") if split_by else ""
        p, v = out.setdefault(k, ([], []))
        p.append(r["TIME_PERIOD"])
        try:
            v.append(float(r["OBS_VALUE"]))
        except (TypeError, ValueError):
            v.append(None)
    for k, (p, v) in out.items():
        order = sorted(range(len(p)), key=lambda i: p[i])
        out[k] = ([p[i] for i in order], [v[i] for i in order])
    return out

# --------------------------------------------------------------------------- small maths

def pct_change(values: list, lag: int) -> list:
    out = [None] * len(values)
    for i in range(lag, len(values)):
        a, b = values[i - lag], values[i]
        out[i] = None if (a in (None, 0) or b is None) else round((b / a - 1) * 100, 2)
    return out


def index_to(values: list, periods: list, base_period: str) -> list:
    if base_period in periods and values[periods.index(base_period)]:
        b = values[periods.index(base_period)]
    else:
        b = next((v for v in values if v), None)
    return [None if v is None or not b else round(v / b * 100, 2) for v in values]


def rolling_sum(values: list, n: int) -> list:
    out = [None] * len(values)
    for i in range(n - 1, len(values)):
        window = values[i - n + 1:i + 1]
        out[i] = None if any(v is None for v in window) else sum(window)
    return out


def S(label, unit, freq, periods, values, decimals=1, **extra):
    d = {"label": label, "unit": unit, "freq": freq, "decimals": decimals, "periods": list(periods), "values": list(values)}
    d.update(extra)
    return d


def src(name, table=None, url=None, updated=None, note=None):
    d = {"name": name}
    if table: d["table"] = table
    if url: d["url"] = url
    if updated: d["updated"] = updated
    if note: d["note"] = note
    return d

# --------------------------------------------------------------------------- indicators

def ind_gdp() -> dict:
    per, s, m = ssb_series(ssb_data("09190", [("Makrost", ["bnpb.nr23_9fn", "bnpb.nr23_9"]),
                                               ("ContentsCode", ["FastePriserSesJust", "VolumEndrSesJust", "Volum", "PriserSesJust"]),
                                               ("Tid", ["from(1995K1)"])]))
    pa, sa, ma = ssb_series(ssb_data("09189", [("Makrost", ["bnpb.nr23_9fn", "bnpb.nr23_9"]),
                                                ("ContentsCode", ["Volum", "Priser"]), ("Tid", ["from(1980)"])]))
    return {
        "series": {
            "level": S("GDP Mainland Norway, volume (constant 2023 prices, seasonally adjusted)", "NOK million", "Q", per, s[("bnpb.nr23_9fn", "FastePriserSesJust")], 0),
            "qoq": S("GDP Mainland Norway, quarter-on-quarter volume growth (seasonally adjusted)", "per cent", "Q", per, s[("bnpb.nr23_9fn", "VolumEndrSesJust")], 1),
            "yoy": S("GDP Mainland Norway, volume growth from same quarter a year earlier", "per cent", "Q", per, s[("bnpb.nr23_9fn", "Volum")], 1),
            "total_qoq": S("GDP total, quarter-on-quarter volume growth (seasonally adjusted)", "per cent", "Q", per, s[("bnpb.nr23_9", "VolumEndrSesJust")], 1),
            "annual": S("GDP Mainland Norway, annual volume growth", "per cent", "A", pa, sa[("bnpb.nr23_9fn", "Volum")], 1),
            "annual_total": S("GDP total, annual volume growth", "per cent", "A", pa, sa[("bnpb.nr23_9", "Volum")], 1),
            "nominal_sa": S("GDP Mainland Norway, current prices, seasonally adjusted", "NOK million", "Q", per, s[("bnpb.nr23_9fn", "PriserSesJust")], 0),
            "nominal_total_sa": S("GDP total, current prices, seasonally adjusted", "NOK million", "Q", per, s[("bnpb.nr23_9", "PriserSesJust")], 0),
            "nominal_annual": S("GDP Mainland Norway, current prices", "NOK million", "A", pa, sa[("bnpb.nr23_9fn", "Priser")], 0),
            "nominal_annual_total": S("GDP total, current prices", "NOK million", "A", pa, sa[("bnpb.nr23_9", "Priser")], 0),
        },
        "sources": [src("Statistics Norway, quarterly national accounts", "09190", "https://www.ssb.no/en/statbank/table/09190", m["updated"], "Figures after the base year are preliminary."),
                    src("Statistics Norway, annual national accounts", "09189", "https://www.ssb.no/en/statbank/table/09189", ma["updated"])],
    }


def ind_inflation() -> dict:
    per, s, m = ssb_series(ssb_data("14700", [("VareTjenesteGrp", ["00"]), ("ContentsCode", ["KpiIndMnd", "Tolvmanedersendring", "Manedsendring"]), ("Tid", ["from(2000M01)"])]))
    per2, s2, m2 = ssb_series(ssb_data("14704", [("KPIavledetSerie", ["KPI-JAE", "KPI-JA"]), ("VareTjenesteGrp", ["00"]),
                                                  ("ContentsCode", ["Tolvmanedersendring", "KPIJustIndMnd"]), ("Tid", ["from(2000M07)"])]))
    # Main CPI groups, 12-month change (latest 10 years) for the breakdown view
    per3, s3, m3 = ssb_series(ssb_data("14704", [("KPIavledetSerie", ["KPI"]), ("VareTjenesteGrp", ["01", "03", "04", "05", "07", "08", "09", "11"]),
                                                  ("ContentsCode", ["Tolvmanedersendring"]), ("Tid", ["top(121)"])]))
    labels3 = {"01": "Food and non-alcoholic beverages", "03": "Clothing and footwear", "04": "Housing, electricity and fuels",
               "05": "Furnishings and household equipment", "07": "Transport", "08": "Information and communication",
               "09": "Recreation and culture", "11": "Restaurants and hotels"}
    series = {
        "cpi_yoy": S("CPI, 12-month change", "per cent", "M", per, s[("00", "Tolvmanedersendring")], 1),
        "cpiate_yoy": S("CPI-ATE (adjusted for tax changes, excluding energy), 12-month change", "per cent", "M", per2, s2[("KPI-JAE", "00", "Tolvmanedersendring")], 1),
        "cpiat_yoy": S("CPI-AT (adjusted for tax changes), 12-month change", "per cent", "M", per2, s2[("KPI-JA", "00", "Tolvmanedersendring")], 1),
        "cpi_mom": S("CPI, monthly change", "per cent", "M", per, s[("00", "Manedsendring")], 1),
        "cpi_index": S("CPI (2025=100)", "index", "M", per, s[("00", "KpiIndMnd")], 1),
    }
    for code, lab in labels3.items():
        series["grp_" + code] = S(lab + ", 12-month change", "per cent", "M", per3, s3[("KPI", code, "Tolvmanedersendring")], 1)
    return {"series": series,
            "sources": [src("Statistics Norway, consumer price index", "14700", "https://www.ssb.no/en/statbank/table/14700", m["updated"]),
                        src("Statistics Norway, CPI adjusted for tax changes and excluding energy products", "14704", "https://www.ssb.no/en/statbank/table/14704", m2["updated"])],
            "target": 2.0}


def ind_labour() -> dict:
    per, s, m = ssb_series(ssb_data("13760", [("Kjonn", ["0"]), ("Alder", ["15-74"]), ("Justering", ["3MGG"]),
                                               ("ContentsCode", ["ArbledProsArbstyrk", "SysselProsBefolkn", "ArbStyrkProsBefolkn"]), ("Tid", ["from(2006M01)"])]))
    # drop the trailing month that is always empty for the 3-month moving average
    while per and s[("0", "15-74", "3MGG", "ArbledProsArbstyrk")][-1] is None:
        per = per[:-1]
        for k in s: s[k] = s[k][:-1]
    return {"series": {
                "unemployment": S("Unemployment rate, LFS (seasonally adjusted, 3-month moving average)", "per cent of labour force", "M", per, s[("0", "15-74", "3MGG", "ArbledProsArbstyrk")], 1),
                "employment_rate": S("Employment rate, LFS, 15-74 years (seasonally adjusted, 3-month moving average)", "per cent of population", "M", per, s[("0", "15-74", "3MGG", "SysselProsBefolkn")], 1),
                "participation": S("Labour force participation, 15-74 years (seasonally adjusted, 3-month moving average)", "per cent of population", "M", per, s[("0", "15-74", "3MGG", "ArbStyrkProsBefolkn")], 1)},
            "sources": [src("Statistics Norway, Labour force survey", "13760", "https://www.ssb.no/en/statbank/table/13760", m["updated"],
                            "Three-month moving averages are dated by the middle month.")]}


def ind_rates() -> dict:
    pol_m = nb_series("IR", "M.KPRA.SD.R", startPeriod="1991-01-01")[""]
    pol_d = nb_series("IR", "B.KPRA.SD.R", startPeriod="2019-01-01")[""]
    nowa_m = nb_series("SHORT_RATES", "M.NOWA.ON.R", startPeriod="2000-01-01")[""]
    y10_m = nb_series("GOVT_GENERIC_RATES", "M.10Y.GBON", startPeriod="1990-01-01")[""]
    y10_d = nb_series("GOVT_GENERIC_RATES", "B.10Y.GBON", startPeriod="2019-01-01")[""]
    y3_m = nb_series("GOVT_GENERIC_RATES", "M.3Y.GBON", startPeriod="1990-01-01")[""]
    tb3_m = nb_series("GOVT_GENERIC_RATES", "M.3M.TBIL", startPeriod="1990-01-01")[""]
    ann = nb_csv("ANN_KPRA", "KPRA", startPeriod="2000-01-01")
    decisions = [{"date": r["TIME_PERIOD"], "rate": float(r["OBS_VALUE"]), "change": float(r["DELTA_VALUE"] or 0)} for r in ann if r.get("OBS_VALUE")]
    return {"series": {
                "policy_rate": S("Policy rate (sight deposit rate), monthly average", "per cent", "M", *pol_m, 2),
                "policy_rate_daily": S("Policy rate (sight deposit rate), daily", "per cent", "D", *pol_d, 2),
                "nowa": S("NOWA overnight rate, monthly average", "per cent", "M", *nowa_m, 2),
                "gov10y": S("10-year government bond yield, monthly average", "per cent", "M", *y10_m, 2),
                "gov10y_daily": S("10-year government bond yield, daily", "per cent", "D", *y10_d, 2),
                "gov3y": S("3-year government bond yield, monthly average", "per cent", "M", *y3_m, 2),
                "tbill3m": S("3-month treasury bill yield, monthly average", "per cent", "M", *tb3_m, 2)},
            "decisions": decisions[-40:],
            "sources": [src("Norges Bank, policy rate", "IR", "https://data.norges-bank.no/", None),
                        src("Norges Bank, NOWA", "SHORT_RATES"), src("Norges Bank, generic government yields", "GOVT_GENERIC_RATES"),
                        src("Norges Bank, policy rate decisions", "ANN_KPRA")]}


def ind_fx() -> dict:
    m = nb_series("EXR", "M.I44+EUR+USD+SEK+GBP.NOK.SP", split_by="BASE_CUR", startPeriod="1990-01-01")
    d = nb_series("EXR", "B.I44+EUR+USD.NOK.SP", split_by="BASE_CUR", startPeriod="2019-01-01")
    return {"series": {
                "i44": S("Import-weighted krone exchange rate, I-44 (1995=100, higher = weaker krone)", "index", "M", *m["I44"], 2),
                "i44_daily": S("I-44, daily", "index", "D", *d["I44"], 2),
                "eurnok": S("EUR/NOK, monthly average", "NOK per EUR", "M", *m["EUR"], 4),
                "eurnok_daily": S("EUR/NOK, daily", "NOK per EUR", "D", *d["EUR"], 4),
                "usdnok": S("USD/NOK, monthly average", "NOK per USD", "M", *m["USD"], 4),
                "usdnok_daily": S("USD/NOK, daily", "NOK per USD", "D", *d["USD"], 4),
                "seknok": S("SEK/NOK (per 100 SEK), monthly average", "NOK per 100 SEK", "M", *m["SEK"], 4),
                "gbpnok": S("GBP/NOK, monthly average", "NOK per GBP", "M", *m["GBP"], 4)},
            "sources": [src("Norges Bank, exchange rates", "EXR", "https://data.norges-bank.no/")]}


def ind_trade() -> dict:
    per, s, m = ssb_series(ssb_data("08864", [("HovedVareStrommer", ["Etot", "Itot", "Hbtot", "Etotusorn", "Eoljegass", "Itotuso", "Hbtusorn"]),
                                               ("ContentsCode", ["JusterteTall"]), ("Tid", ["from(2005M01)"])]))
    g = lambda c: s[(c, "JusterteTall")]
    return {"series": {
                "balance": S("Trade balance, goods (seasonally adjusted)", "NOK million", "M", per, g("Hbtot"), 0),
                "exports": S("Exports of goods (seasonally adjusted)", "NOK million", "M", per, g("Etot"), 0),
                "imports": S("Imports of goods (seasonally adjusted)", "NOK million", "M", per, g("Itot"), 0),
                "oilgas_exports": S("Exports of crude oil, natural gas and condensates (seasonally adjusted)", "NOK million", "M", per, g("Eoljegass"), 0),
                "mainland_exports": S("Mainland exports (excl. oil, gas, ships and platforms; seasonally adjusted)", "NOK million", "M", per, g("Etotusorn"), 0),
                "imports_ex_ships": S("Imports excl. ships and oil platforms (seasonally adjusted)", "NOK million", "M", per, g("Itotuso"), 0),
                "mainland_balance": S("Mainland trade balance (mainland exports minus imports excl. ships and platforms)", "NOK million", "M", per, g("Hbtusorn"), 0)},
            "sources": [src("Statistics Norway, external trade in goods", "08864", "https://www.ssb.no/en/statbank/table/08864", m["updated"])]}


def ind_current_account(gdp: dict | None) -> dict:
    per, s, m = ssb_series(ssb_data("09672", [("DriftKapita", ["sal.saldodrift", "sal.eksover", "sal.vareover", "sal.tjenover", "sal.ntoinnt"]),
                                               ("ContentsCode", ["DriftsKapital"]), ("Tid", ["from(1981K1)"])]))
    g = lambda c: s[(c, "DriftsKapital")]
    series = {
        "balance": S("Current account balance", "NOK million", "Q", per, g("sal.saldodrift"), 0),
        "goods_services": S("Balance of goods and services", "NOK million", "Q", per, g("sal.eksover"), 0),
        "goods": S("Balance of goods", "NOK million", "Q", per, g("sal.vareover"), 0),
        "services": S("Balance of services", "NOK million", "Q", per, g("sal.tjenover"), 0),
        "income": S("Balance of income and current transfers", "NOK million", "Q", per, g("sal.ntoinnt"), 0),
        "balance_4q": S("Current account balance, sum of last four quarters", "NOK million", "Q", per, rolling_sum(g("sal.saldodrift"), 4), 0, derived=True),
    }
    if gdp:
        ng = gdp["series"]["nominal_total_sa"]
        lookup = dict(zip(ng["periods"], ng["values"]))
        gdp4 = rolling_sum([lookup.get(p) for p in per], 4)
        ca4 = series["balance_4q"]["values"]
        pct = [None if (a is None or b in (None, 0)) else round(a / b * 100, 1) for a, b in zip(ca4, gdp4)]
        series["pct_gdp"] = S("Current account balance, per cent of GDP (four-quarter sums)", "per cent of GDP", "Q", per, pct, 1, derived=True,
                              note="Four-quarter sum of the current account divided by the four-quarter sum of nominal GDP (table 09190, seasonally adjusted).")
    return {"series": series,
            "sources": [src("Statistics Norway, balance of payments", "09672", "https://www.ssb.no/en/statbank/table/09672", m["updated"])]}


def ind_oil_fund(gdp: dict | None) -> dict:
    url = "https://www.nbim.no/en/investments/the-funds-value/"
    html = http_get(url, headers={"User-Agent": "Mozilla/5.0"}).decode("utf-8", "ignore")
    blocks = [b for b in re.findall(r"<script[^>]*>(.*?)</script>", html, re.S) if "Date.UTC" in b]

    def parse_series(block: str) -> dict[str, tuple[list, list]]:
        out = {}
        for name, arr in re.findall(r"name:\s*['\"]([^'\"]*)['\"][^{}]*?data:\s*(\[[^\]]*(?:\][^\]]*)*?\]\s*\])", block, re.S):
            pts = re.findall(r"Date\.UTC\((\d+),\s*(\d+),\s*(\d+)\)\s*,\s*(-?[\d.]+)", arr)
            if pts and name not in out:
                out[name] = ([p[0] for p in pts], [float(p[3]) for p in pts])
        return out

    comp = None
    for b in blocks:
        ser = parse_series(b)
        if "Total market value" in ser and "Equity" in ser:
            comp = ser
            break
    if comp is None:
        raise RuntimeError("could not find the market-value chart on the NBIM page")
    years, total = comp["Total market value"]
    series = {"value": S("Market value at year-end", "NOK billion", "A", years, total, 0)}
    for key, name in (("equity", "Equity"), ("fixed_income", "Fixed income"), ("real_estate", "Real estate"), ("renewables", "Renewable energy infrastructure")):
        if name in comp:
            series[key] = S(name + ", market value at year-end", "NOK billion", "A", *comp[name], 0)
    if gdp:
        ng = gdp["series"]["nominal_annual_total"]
        lookup = dict(zip(ng["periods"], ng["values"]))
        ratio = [None if (v is None or not lookup.get(y)) else round(v * 1000 / lookup[y] * 100, 0) for y, v in zip(years, total)]
        series["pct_gdp"] = S("Market value in per cent of GDP", "per cent of GDP", "A", years, ratio, 0, derived=True,
                              note="Year-end market value divided by nominal GDP for the same year (SSB table 09189).")
    return {"series": series,
            "sources": [src("Norges Bank Investment Management, the fund's value", None, url, None,
                            "Year-end values read from the chart on NBIM's page; NBIM publishes no open data API.")]}


def ind_house_prices() -> dict:
    per, s, m = ssb_series(ssb_data("07221", [("Region", ["TOTAL", "001", "003", "004", "002"]), ("Boligtype", ["00"]),
                                               ("ContentsCode", ["SesJustBoligindeks", "Boligindeks"]), ("Tid", ["from(1992K1)"])]))
    idx = s[("TOTAL", "00", "SesJustBoligindeks")]
    nsa = s[("TOTAL", "00", "Boligindeks")]
    series = {
        "index": S("House price index, whole country (2015=100, seasonally adjusted)", "index", "Q", per, idx, 1),
        "yoy": S("House prices, change from same quarter a year earlier", "per cent", "Q", per, pct_change(nsa, 4), 1, derived=True),
        "qoq": S("House prices, change from previous quarter (seasonally adjusted)", "per cent", "Q", per, pct_change(idx, 1), 1, derived=True),
    }
    for code, lab in (("001", "Oslo incl. Bærum"), ("003", "Bergen"), ("004", "Trondheim"), ("002", "Stavanger")):
        series["yoy_" + code] = S(lab + ", change from same quarter a year earlier", "per cent", "Q", per, pct_change(s[(code, "00", "Boligindeks")], 4), 1, derived=True)
    return {"series": series,
            "sources": [src("Statistics Norway, price index for existing dwellings", "07221", "https://www.ssb.no/en/statbank/table/07221", m["updated"])]}


def ind_productivity() -> dict:
    alpha = 1 / 3
    py, sy, my = ssb_series(ssb_data("09189", [("Makrost", ["bnpb.nr23_6fn"]), ("ContentsCode", ["Faste"]), ("Tid", ["from(1970)"])]))
    pl, sl, ml = ssb_series(ssb_data("09174", [("NACE", ["nr23_6fn"]), ("ContentsCode", ["Timeverk"]), ("Tid", ["from(1970)"])]))
    pk, sk, mk = ssb_series(ssb_data("09181", [("NACE", ["nr23_6fn"]), ("ContentsCode", ["KAPN4"]), ("Tid", ["from(1970)"])]))
    Y = dict(zip(py, sy[("bnpb.nr23_6fn", "Faste")]))
    L = dict(zip(pl, sl[("nr23_6fn", "Timeverk")]))
    K = dict(zip(pk, sk[("nr23_6fn", "KAPN4")]))
    years = [y for y in py if Y.get(y) and L.get(y) and K.get(y)]
    lp = [Y[y] / L[y] for y in years]                                   # value added per hour, NOK (2023 prices)
    tfp = [math.exp(math.log(Y[y]) - alpha * math.log(K[y]) - (1 - alpha) * math.log(L[y])) for y in years]
    kl = [K[y] / L[y] for y in years]
    base = "2000"
    series = {
        "labour_productivity": S("Labour productivity, Mainland Norway: value added per hour worked (2000=100)", "index", "A", years, index_to(lp, years, base), 1, derived=True),
        "tfp": S("Total factor productivity, Mainland Norway (2000=100)", "index", "A", years, index_to(tfp, years, base), 1, derived=True),
        "capital_per_hour": S("Capital per hour worked, Mainland Norway (2000=100)", "index", "A", years, index_to(kl, years, base), 1, derived=True),
        "labour_productivity_growth": S("Labour productivity growth", "per cent", "A", years, pct_change(lp, 1), 1, derived=True),
        "tfp_growth": S("TFP growth", "per cent", "A", years, pct_change(tfp, 1), 1, derived=True),
        "output_growth": S("Value added growth, Mainland Norway (volume)", "per cent", "A", years, pct_change([Y[y] for y in years], 1), 1, derived=True),
        "hours_growth": S("Hours worked growth, Mainland Norway", "per cent", "A", years, pct_change([L[y] for y in years], 1), 1, derived=True),
        "capital_growth": S("Fixed capital growth, Mainland Norway (volume)", "per cent", "A", years, pct_change([K[y] for y in years], 1), 1, derived=True),
        "value_added_per_hour": S("Value added per hour worked, Mainland Norway", "NOK, 2023 prices", "A", years, [round(v * 1e6 / 1e6, 0) for v in lp], 0, derived=True),
    }
    return {"series": series,
            "method": ("TFP is the Solow residual from a Cobb-Douglas production function Y = A K^α L^(1-α) with α = 1/3: "
                       "log A = log Y − ⅓ log K − ⅔ log L. Y is gross value added in Mainland Norway at constant 2023 prices (SSB 09189), "
                       "L is total hours worked in Mainland Norway (SSB 09174), K is the net stock of fixed capital in Mainland Norway at constant 2023 prices (SSB 09181). "
                       "Labour productivity is Y/L. No adjustment is made for capacity utilisation or labour quality. Chain-linked volumes are treated as additive."),
            "sources": [src("Statistics Norway, annual national accounts", "09189", "https://www.ssb.no/en/statbank/table/09189", my["updated"]),
                        src("Statistics Norway, employment and hours worked by industry", "09174", "https://www.ssb.no/en/statbank/table/09174", ml["updated"]),
                        src("Statistics Norway, fixed capital by industry", "09181", "https://www.ssb.no/en/statbank/table/09181", mk["updated"])]}

# --------------------------------------------------------------------------- main

def main() -> int:
    previous = {}
    if OUT.exists():
        try:
            previous = json.loads(OUT.read_text(encoding="utf-8")).get("indicators", {})
        except Exception:
            previous = {}
    indicators: dict = {}
    errors: dict = {}

    def run(key, fn, *args):
        try:
            d = fn(*args)
            d["fetched"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            indicators[key] = d
            n = sum(len(s["periods"]) for s in d["series"].values())
            print(f"  ok   {key:16} {len(d['series'])} series, {n} points")
        except Exception as e:  # keep the previous data for this indicator
            errors[key] = f"{type(e).__name__}: {e}"
            print(f"  FAIL {key:16} {errors[key]}", file=sys.stderr)
            traceback.print_exc(limit=2)
            if key in previous:
                indicators[key] = previous[key]
                indicators[key]["stale"] = True
                indicators[key]["error"] = errors[key]

    print("Fetching dashboard data ...")
    run("gdp", ind_gdp)
    gdp = indicators.get("gdp")
    run("inflation", ind_inflation)
    run("labour", ind_labour)
    run("rates", ind_rates)
    run("fx", ind_fx)
    run("trade", ind_trade)
    run("current_account", ind_current_account, gdp)
    run("oil_fund", ind_oil_fund, gdp)
    run("house_prices", ind_house_prices)
    run("productivity", ind_productivity)

    OUT.parent.mkdir(exist_ok=True)
    payload = {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "errors": errors, "indicators": indicators}
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size/1024:.0f} kB), {len(indicators)} indicators, {len(errors)} errors")
    return 0 if not errors else (0 if indicators else 1)


if __name__ == "__main__":
    sys.exit(main())
