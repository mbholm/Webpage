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


def ind_inflation(proj: dict | None = None, prev: dict | None = None) -> dict:
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
    out = {"series": series,
           "sources": [src("Statistics Norway, consumer price index", "14700", "https://www.ssb.no/en/statbank/table/14700", m["updated"]),
                       src("Statistics Norway, CPI adjusted for tax changes and excluding energy products", "14704", "https://www.ssb.no/en/statbank/table/14704", m2["updated"])],
           "target": 2.0}
    return add_projections(out, proj, prev, [("proj_cpi", "cpi", "CPI, four-quarter change", 1), ("proj_cpiate", "cpiate", "CPI-ATE, four-quarter change", 1)])


def xlsx_sheets(data: bytes) -> dict[str, list[dict]]:
    """Minimal .xlsx reader (standard library only): {sheet name: [ {column letter: value}, ... ]}."""
    import zipfile
    import xml.etree.ElementTree as ET
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rid_attr = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
    z = zipfile.ZipFile(io.BytesIO(data))
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", ns):
            shared.append("".join(t.text or "" for t in si.iter("{%s}t" % ns["m"])))
    rels = {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
    out = {}
    for sh in ET.fromstring(z.read("xl/workbook.xml")).find("m:sheets", ns):
        target = rels[sh.get(rid_attr)]
        target = target[1:] if target.startswith("/") else "xl/" + target
        rows = []
        for row in ET.fromstring(z.read(target)).iter("{%s}row" % ns["m"]):
            cells = {}
            for c in row.findall("m:c", ns):
                col = re.match(r"[A-Z]+", c.get("r")).group(0)
                v = c.find("m:v", ns)
                if v is None:
                    val = None
                elif c.get("t") == "s":
                    val = shared[int(v.text)]
                else:
                    try:
                        val = float(v.text)
                    except ValueError:
                        val = v.text
                cells[col] = val
            rows.append(cells)
        out[sh.get("name")] = rows
    return out


def nav_month_table(sheets: dict, title_contains: str) -> tuple[list[str], list[float]]:
    """NAV's year-by-month tables: a title in row 3 column C, one row per year (column B), months in columns C..N."""
    month_cols = "CDEFGHIJKLMN"
    for rows in sheets.values():
        title = " ".join(str(r.get("C") or "") for r in rows[:4])
        if title_contains.lower() not in title.lower():
            continue
        points = {}
        for r in rows:
            y = r.get("B")
            if not isinstance(y, float) or not (1950 <= y <= 2100):
                continue
            for i, col in enumerate(month_cols, start=1):
                v = r.get(col)
                if isinstance(v, float):
                    points[f"{int(y)}-{i:02d}"] = v
        periods = sorted(points)
        return periods, [points[p] for p in periods]
    raise RuntimeError(f"NAV workbook has no sheet titled like '{title_contains}'")


def nav_registered() -> tuple[dict, dict]:
    """Registered unemployment from NAV's 'Hovedtall om arbeidsmarkedet' page (no API; monthly Excel files whose links change every month)."""
    page = "https://www.nav.no/no/nav-og-samfunn/statistikk/arbeidssokere-og-stillinger-statistikk/hovedtall-om-arbeidsmarkedet"
    html = http_get(page, headers={"User-Agent": "Mozilla/5.0"}).decode("utf-8", "ignore")

    def find(prefix):
        m = re.search(r'href="(/_/attachment/download/[^"]*' + prefix + r'[^"]*\.xlsx)"', html)
        if not m:
            raise RuntimeError(f"NAV page has no {prefix} workbook link")
        return "https://www.nav.no" + m.group(1).replace("&amp;", "&")

    sa = xlsx_sheets(http_get(find("HARB100"), headers={"User-Agent": "Mozilla/5.0"}))      # seasonally adjusted main figures
    hist = xlsx_sheets(http_get(find("HL100"), headers={"User-Agent": "Mozilla/5.0"}))       # unadjusted history from 1991
    p_sa, v_sa = nav_month_table(sa, "Helt ledige, sesongjustert² tidsserie i prosent av arbeidsstyrken")
    p_gr, v_gr = nav_month_table(sa, "Bruttoledighet)², sesongjustert tidsserie i prosent av arbeidsstyrken")
    p_n, v_n = nav_month_table(sa, "Helt ledige, sesongjustert² tidsserie")
    p_h, v_h = nav_month_table(hist, "Beholdning helt ledige som andel av arbeidsstyrken")
    edition = re.search(r"Statistikk per (\w+ \d{4})", html)
    series = {
        "nav_unemployment": S("Registered unemployment, NAV (seasonally adjusted)", "per cent of labour force", "M", p_sa, v_sa, 1),
        "nav_gross": S("Registered unemployment incl. labour market programmes, NAV (seasonally adjusted)", "per cent of labour force", "M", p_gr, v_gr, 1),
        "nav_unemployment_nsa": S("Registered unemployment, NAV (not seasonally adjusted)", "per cent of labour force", "M", p_h, v_h, 1),
        "nav_count": S("Registered fully unemployed, NAV (seasonally adjusted)", "persons", "M", p_n, v_n, 0),
    }
    source = src("NAV, Hovedtall om arbeidsmarkedet", None, page, None,
                 ("Registered fully unemployed as a share of the labour force; the seasonally adjusted series start in 2011 (2017 for the gross measure). "
                  "Statistics for April to July 2025 are affected by NAV's change of job-seeker register."
                  + (f" Edition: {edition.group(1)}." if edition else "")))
    return series, source


_LFS_M = None
_LFS_Q = None


def lfs_monthly():
    """SSB 13760, seasonally adjusted 3-month moving averages by sex and age (shared by the unemployment and employment indicators)."""
    global _LFS_M
    if _LFS_M is None:
        per, s, m = ssb_series(ssb_data("13760", [("Kjonn", ["0", "1", "2"]), ("Alder", ["15-74", "15-24", "25-74"]), ("Justering", ["3MGG"]),
                                                   ("ContentsCode", ["ArbledProsArbstyrk", "SysselProsBefolkn", "ArbStyrkProsBefolkn", "Sysselsatte", "Arbeidsledige", "Ukeverk"]),
                                                   ("Tid", ["from(2006M01)"])]))
        # drop the trailing month that is always empty for the 3-month moving average
        while per and s[("0", "15-74", "3MGG", "ArbledProsArbstyrk")][-1] is None:
            per = per[:-1]
            for k in s: s[k] = s[k][:-1]
        _LFS_M = (per, s, m)
    return _LFS_M


def lfs_quarterly():
    """SSB 14483, seasonally adjusted quarterly LFS by sex and detailed age groups."""
    global _LFS_Q
    if _LFS_Q is None:
        _LFS_Q = ssb_series(ssb_data("14483", [("Kjonn", ["0", "1", "2"]), ("Alder", ["15-74", "15-24", "20-64", "25-39", "25-54", "40-54", "55-74"]), ("Justering", ["S"]),
                                                ("ContentsCode", ["SysselProsBefolkn", "ArbledProsArbstyrk", "Sysselsatte"]), ("Tid", ["from(2009K1)"])]))
    return _LFS_Q


LFS_NOTE_M = "Three-month moving averages are dated by the middle month."


def ind_unemployment() -> dict:
    per, s, m = lfs_monthly()
    perq, sq, mq = lfs_quarterly()
    g = lambda sex, age, c: s[(sex, age, "3MGG", c)]
    q = lambda sex, age, c: sq[(sex, age, "S", c)]
    nav_series, nav_source = nav_registered()
    series = {
        "unemployment": S("Unemployment rate, LFS, 15-74 years (seasonally adjusted, 3-month moving average)", "per cent of labour force", "M", per, g("0", "15-74", "ArbledProsArbstyrk"), 1),
        **nav_series,
        "unemployment_men": S("Unemployment rate, LFS, men (seasonally adjusted, 3-month moving average)", "per cent of labour force", "M", per, g("1", "15-74", "ArbledProsArbstyrk"), 1),
        "unemployment_women": S("Unemployment rate, LFS, women (seasonally adjusted, 3-month moving average)", "per cent of labour force", "M", per, g("2", "15-74", "ArbledProsArbstyrk"), 1),
        "unemployment_15_24": S("Unemployment rate, LFS, 15-24 years (seasonally adjusted, 3-month moving average)", "per cent of labour force", "M", per, g("0", "15-24", "ArbledProsArbstyrk"), 1),
        "unemployment_25_74": S("Unemployment rate, LFS, 25-74 years (seasonally adjusted, 3-month moving average)", "per cent of labour force", "M", per, g("0", "25-74", "ArbledProsArbstyrk"), 1),
        "unemployed": S("Unemployed persons, LFS (seasonally adjusted, 3-month moving average)", "1000 persons", "M", per, g("0", "15-74", "Arbeidsledige"), 0),
        "q_unemployment_25_54": S("Unemployment rate, LFS, 25-54 years (quarterly, seasonally adjusted)", "per cent of labour force", "Q", perq, q("0", "25-54", "ArbledProsArbstyrk"), 1),
        "q_unemployment_55_74": S("Unemployment rate, LFS, 55-74 years (quarterly, seasonally adjusted)", "per cent of labour force", "Q", perq, q("0", "55-74", "ArbledProsArbstyrk"), 1),
        "q_unemployment_men_15_24": S("Unemployment rate, LFS, men 15-24 years (quarterly, seasonally adjusted)", "per cent of labour force", "Q", perq, q("1", "15-24", "ArbledProsArbstyrk"), 1),
        "q_unemployment_women_15_24": S("Unemployment rate, LFS, women 15-24 years (quarterly, seasonally adjusted)", "per cent of labour force", "Q", perq, q("2", "15-24", "ArbledProsArbstyrk"), 1),
    }
    return {"series": series,
            "sources": [src("Statistics Norway, Labour force survey, monthly", "13760", "https://www.ssb.no/en/statbank/table/13760", m["updated"], LFS_NOTE_M),
                        src("Statistics Norway, Labour force survey, quarterly", "14483", "https://www.ssb.no/en/statbank/table/14483", mq["updated"]),
                        nav_source]}


def ind_employment() -> dict:
    per, s, m = lfs_monthly()
    perq, sq, mq = lfs_quarterly()
    g = lambda sex, age, c: s[(sex, age, "3MGG", c)]
    q = lambda sex, age, c: sq[(sex, age, "S", c)]
    series = {
        "employment_rate": S("Employment rate, LFS, 15-74 years (seasonally adjusted, 3-month moving average)", "per cent of population", "M", per, g("0", "15-74", "SysselProsBefolkn"), 1),
        "employment_rate_men": S("Employment rate, LFS, men 15-74 years (seasonally adjusted, 3-month moving average)", "per cent of population", "M", per, g("1", "15-74", "SysselProsBefolkn"), 1),
        "employment_rate_women": S("Employment rate, LFS, women 15-74 years (seasonally adjusted, 3-month moving average)", "per cent of population", "M", per, g("2", "15-74", "SysselProsBefolkn"), 1),
        "employment_rate_15_24": S("Employment rate, LFS, 15-24 years (seasonally adjusted, 3-month moving average)", "per cent of population", "M", per, g("0", "15-24", "SysselProsBefolkn"), 1),
        "employment_rate_25_74": S("Employment rate, LFS, 25-74 years (seasonally adjusted, 3-month moving average)", "per cent of population", "M", per, g("0", "25-74", "SysselProsBefolkn"), 1),
        "participation": S("Labour force participation, 15-74 years (seasonally adjusted, 3-month moving average)", "per cent of population", "M", per, g("0", "15-74", "ArbStyrkProsBefolkn"), 1),
        "employed": S("Employed persons, LFS, 15-74 years (seasonally adjusted, 3-month moving average)", "1000 persons", "M", per, g("0", "15-74", "Sysselsatte"), 0),
        "manweeks": S("Man-weeks worked of 37.5 hours (seasonally adjusted, 3-month moving average)", "1000 man-weeks", "M", per, g("0", "15-74", "Ukeverk"), 0),
    }
    ages = {"15-24": "15-24 years", "25-39": "25-39 years", "40-54": "40-54 years", "55-74": "55-74 years", "20-64": "20-64 years"}
    for code, lab in ages.items():
        series["q_rate_" + code.replace("-", "_")] = S(f"Employment rate, LFS, {lab} (quarterly, seasonally adjusted)", "per cent of population", "Q", perq, q("0", code, "SysselProsBefolkn"), 1)
    for sex, sl in (("1", "men"), ("2", "women")):
        for code in ("15-24", "25-54", "55-74"):
            series[f"q_rate_{sl}_{code.replace('-', '_')}"] = S(f"Employment rate, LFS, {sl} {code} years (quarterly, seasonally adjusted)", "per cent of population", "Q", perq, q(sex, code, "SysselProsBefolkn"), 1)
    return {"series": series,
            "sources": [src("Statistics Norway, Labour force survey, monthly", "13760", "https://www.ssb.no/en/statbank/table/13760", m["updated"], LFS_NOTE_M),
                        src("Statistics Norway, Labour force survey, quarterly", "14483", "https://www.ssb.no/en/statbank/table/14483", mq["updated"])]}


# --------------------------------------------------------------------------- Norges Bank projections (Monetary Policy Report)

MONTHS_EN = "January|February|March|April|May|June|July|August|September|October|November|December"


def nb_projections() -> dict:
    """Policy rate, CPI and CPI-ATE projections from the data workbook of the latest Monetary Policy Report (no API)."""
    from datetime import date, timedelta
    base = "https://www.norges-bank.no"
    index = base + "/en/news-events/publications/Monetary-Policy-Report/"
    html = http_get(index, headers={"User-Agent": "Mozilla/5.0"}).decode("utf-8", "ignore")
    found = re.findall(r"/en/news-events/publications/Monetary-Policy-Report/(\d{4})/mpr-(\d)(\d{4})/", html)
    year, n = max((int(y), int(k)) for y, k, y2 in found if y == y2)
    page = f"{base}/en/news-events/publications/Monetary-Policy-Report/{year}/mpr-{n}{year}/"
    rhtml = http_get(page, headers={"User-Agent": "Mozilla/5.0"}).decode("utf-8", "ignore")
    m = re.search(r'href="(/contentassets/[^"]*/data-mpr-\d-\d\d\.xlsx[^"]*)"', rhtml)
    if not m:
        raise RuntimeError(f"MPR {n}/{year}: no data workbook link on {page}")
    sheets = xlsx_sheets(http_get(base + m.group(1).replace("&amp;", "&"), headers={"User-Agent": "Mozilla/5.0"}))
    for rows in sheets.values():
        head = " ".join(str(v) for r in rows[:3] for v in r.values() if v)
        if "Key policy rate, output gap, CPI and CPI-ATE" in head:
            break
    else:
        raise RuntimeError("MPR workbook: no 'Key policy rate, output gap, CPI and CPI-ATE' sheet")
    hi = next(i for i, r in enumerate(rows) if r.get("A") == "Date")
    hdr = {k: str(v).strip() for k, v in rows[hi].items() if v}

    def col(pattern):
        for k, v in hdr.items():
            if re.fullmatch(pattern, v):
                return k
        raise RuntimeError(f"MPR workbook: no column matching {pattern!r} in {list(hdr.values())}")

    cols = {"policy_rate": col(r"Policy rate \(level\)"), "cpi": col(r"CPI, four-quarter change"), "cpiate": col(r"CPI-ATE, four-quarter change")}
    data = {k: {} for k in cols}
    for r in rows[hi + 1:]:
        d = r.get("A")
        if not isinstance(d, float):
            continue
        dt = date(1899, 12, 30) + timedelta(days=int(d))
        qk = (dt.year, (dt.month - 1) // 3 + 1)
        for name, c in cols.items():
            v = r.get(c)
            if isinstance(v, float):
                data[name][qk] = v
    pub = re.search(r"(\d{1,2} (?:%s) \d{4})" % MONTHS_EN, rhtml)
    return {"report": f"{n}/{year}", "url": page, "published": pub.group(1) if pub else None,
            "first": (year, n), "anchor": (year, n - 1) if n > 1 else (year - 1, 4), "data": data}


def proj_series(proj: dict, name: str, label: str, decimals: int) -> dict:
    """Quarterly projection as a monthly-frequency series (each quarter at its middle month) so it can overlay the monthly charts."""
    mid = {1: "02", 2: "05", 3: "08", 4: "11"}
    pts = sorted((k, v) for k, v in proj["data"][name].items() if k >= proj["anchor"])
    return S(f"{label}, Norges Bank projection (MPR {proj['report']})", "per cent", "M",
             [f"{y}-{mid[q]}" for (y, q), _ in pts], [round(v, decimals) for _, v in pts], decimals, projection=True,
             note=f"Quarterly projections from Monetary Policy Report {proj['report']}, from the quarter of publication onwards; the first point is the preceding quarter. Each quarter is plotted at its middle month.")


def add_projections(result: dict, proj: dict | None, prev: dict | None, items: list) -> dict:
    if proj:
        for key, name, label, dec in items:
            result["series"][key] = proj_series(proj, name, label, dec)
        result["sources"].append(src(f"Norges Bank, Monetary Policy Report {proj['report']}", None, proj["url"], proj.get("published"),
                                     "Projections from the quarter of publication; quarterly values plotted at the middle month of each quarter."))
    elif prev:   # keep the last successfully fetched projections
        for key, *_ in items:
            if key in prev.get("series", {}):
                result["series"][key] = prev["series"][key]
        result["sources"] += [x for x in prev.get("sources", []) if x["name"].startswith("Norges Bank, Monetary Policy Report")]
    return result


def ind_rates(proj: dict | None = None, prev: dict | None = None) -> dict:
    pol_m = nb_series("IR", "M.KPRA.SD.R", startPeriod="1991-01-01")[""]
    pol_d = nb_series("IR", "B.KPRA.SD.R", startPeriod="2019-01-01")[""]
    nowa_m = nb_series("SHORT_RATES", "M.NOWA.ON.R", startPeriod="2000-01-01")[""]
    y10_m = nb_series("GOVT_GENERIC_RATES", "M.10Y.GBON", startPeriod="1990-01-01")[""]
    y10_d = nb_series("GOVT_GENERIC_RATES", "B.10Y.GBON", startPeriod="2019-01-01")[""]
    y3_m = nb_series("GOVT_GENERIC_RATES", "M.3Y.GBON", startPeriod="1990-01-01")[""]
    tb3_m = nb_series("GOVT_GENERIC_RATES", "M.3M.TBIL", startPeriod="1990-01-01")[""]
    ann = nb_csv("ANN_KPRA", "KPRA", startPeriod="2000-01-01")
    decisions = [{"date": r["TIME_PERIOD"], "rate": float(r["OBS_VALUE"]), "change": float(r["DELTA_VALUE"] or 0)} for r in ann if r.get("OBS_VALUE")]
    out = {"series": {
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
    return add_projections(out, proj, prev, [("proj_policy_rate", "policy_rate", "Policy rate, quarterly average", 2)])


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


# NBIM publishes no machine-readable series of management costs in kroner. The figures below are transcribed by hand:
#  - 2011 onwards: "Total management costs in Norges Bank", NOK billion, from the Government Pension Fund Global annual reports
#    (each report states the year and the year before). Add the new year when the annual report is published in late February.
#  - 1998-2010: NBIM's cost ratio in basis points of assets under management ("Management costs 1998-2017", Return and risk 2017),
#    converted to kroner in ind_oil_fund with the average of the opening and closing market value.
NBIM_COSTS_REPORTED = {2011: 2.5, 2012: 2.2, 2013: 2.9, 2014: 3.2, 2015: 3.9, 2016: 3.7, 2017: 4.7, 2018: 4.5, 2019: 4.3, 2020: 5.3,
                       2021: 4.6, 2022: 5.2, 2023: 6.6, 2024: 7.4, 2025: 7.5}
NBIM_COST_BP = {1998: 5.1, 1999: 8.7, 2000: 10.9, 2001: 7.4, 2002: 9.1, 2003: 10.4, 2004: 10.5, 2005: 10.6, 2006: 9.8, 2007: 9.3,
                2008: 10.6, 2009: 13.8, 2010: 10.5}


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
    value = dict(zip(years, total))
    cy, cv = [], []
    for y in sorted(set(NBIM_COST_BP) | set(NBIM_COSTS_REPORTED)):
        if y in NBIM_COSTS_REPORTED:
            v = NBIM_COSTS_REPORTED[y] * 1000
        else:
            a, b = value.get(str(y - 1)), value.get(str(y))
            if b is None:
                continue
            v = round(NBIM_COST_BP[y] / 1e4 * ((a + b) / 2 if a is not None else b) * 1000, -1)
        cy.append(str(y))
        cv.append(v)
    series["costs"] = S("Management costs", "NOK million", "A", cy, cv, 0, derived=True,
                        note=("2011 onwards: total management costs in Norges Bank as stated in NBIM's annual reports, to the nearest NOK 100 million. "
                              "1998–2010: estimated as NBIM's published cost ratio (basis points of assets under management) times the average of the "
                              "opening and closing market value, so these years are approximate."))
    return {"series": series,
            "sources": [src("Norges Bank Investment Management, the fund's value", None, url, None,
                            "Year-end values read from the chart on NBIM's page; NBIM publishes no open data API."),
                        src("Norges Bank Investment Management, annual reports", None, "https://www.nbim.no/en/news-and-insights/reports/", None,
                            "Management costs are not published as data; the figures are transcribed from the annual reports and updated once a year.")]}


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


# --------------------------------------------------------------------------- national accounts income by industry (SSB 09170)

NA_INDUSTRIES = {   # SSB code: (key, label). Gross and net labour shares are computed for each.
    "nr23ind": ("mfg", "Manufacturing"),
    "nr23_6fn": ("mainland", "Mainland Norway"),
    "nr23_6": ("total", "Total economy"),
    "nr2X06_09": ("oilgas", "Oil and gas extraction incl. services"),
    "pub2X41_43": ("construction", "Construction"),
    "pub2X45_47": ("trade", "Wholesale and retail trade"),
    "pub2X49A_52": ("transport", "Transport excl. ocean transport"),
    "pub2X55_56": ("accommodation", "Accommodation and food services"),
    "pub2X58_63": ("ict", "Information and communication"),
    "pub2X64_66": ("finance", "Financial and insurance activities"),
    "pub2X69_75": ("professional", "Professional, scientific and technical services"),
}
NA_GOVERNMENT = "nr24_5"   # general government, subtracted from Mainland Norway to get the mainland business sector
_NA_INCOME = None


def na_income():
    """Compensation of employees (LOKO), consumption of fixed capital (DEP) and net operating surplus (DRI), current prices, by industry."""
    global _NA_INCOME
    if _NA_INCOME is None:
        _NA_INCOME = ssb_series(ssb_data("09170", [("NACE", list(NA_INDUSTRIES) + [NA_GOVERNMENT]), ("ContentsCode", ["LOKO", "DEP", "DRI"]), ("Tid", ["from(1970)"])]))
    return _NA_INCOME


def labour_shares(code: str, minus: str | None = None) -> tuple[list[str], list, list]:
    """(years, gross share, net share) in per cent. Gross = W/(W+DEP+OS), net = W/(W+OS), as in Holm & Jasinski (Samfunnsøkonomen).
    With `minus`, that industry is subtracted first (current-price income components are additive)."""
    years, s, _ = na_income()
    cols = []
    for c in ("LOKO", "DEP", "DRI"):
        a = s[(code, c)]
        if minus:
            b = s[(minus, c)]
            a = [None if (x is None or y is None) else x - y for x, y in zip(a, b)]
        cols.append(a)
    gross, net = [], []
    for w, d, o in zip(*cols):
        ok = None not in (w, d, o) and (w + d + o) > 0 and (w + o) > 0
        gross.append(round(100 * w / (w + d + o), 1) if ok else None)
        net.append(round(100 * w / (w + o), 1) if ok else None)
    return years, gross, net


def eurostat(dataset: str, **params) -> tuple[list[str], dict[tuple, list], str]:
    """Eurostat JSON-stat API. Returns (times, {(dim values excluding time, in dataset order): values}, updated)."""
    import itertools
    url = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/" + dataset + "?format=JSON&lang=EN&" + urllib.parse.urlencode(params, doseq=True)
    d = json.loads(http_get(url))
    ids = d["id"]
    dims = {k: list(d["dimension"][k]["category"]["index"]) for k in ids}
    other = [k for k in ids if k != "time"]
    times = dims["time"]
    out = {}
    for combo in itertools.product(*[dims[k] for k in other]):
        coords = dict(zip(other, combo))
        vals = []
        for t in times:
            coords["time"] = t
            i = 0
            for k in ids:
                i = i * len(dims[k]) + dims[k].index(coords[k])
            vals.append(d["value"].get(str(i)))
        out[combo] = vals
    return times, out, (d.get("updated") or "")[:10]


EUROSTAT_GEOS = {"NO": "Norway", "SE": "Sweden", "DK": "Denmark", "FI": "Finland", "DE": "Germany", "NL": "Netherlands", "FR": "France",
                 "IS": "Iceland", "EA20": "Euro area", "EU27_2020": "EU"}


def ind_wages() -> dict:
    pa, sa, ma = ssb_series(ssb_data("14858", [("ContentsCode", ["Arslonn", "ArslonnEndring", "ReallonnEndring"]), ("Tid", ["from(1970)"])]))
    ph, sh, mh = ssb_series(ssb_data("09174", [("NACE", ["nr23_6fn", "nr23ind"]), ("ContentsCode", ["LKostnader", "TimeverkL", "LKostTimeverkProsent"]), ("Tid", ["from(1970)"])]))
    et, es, eu = eurostat("lc_lci_lev", unit="EUR", lcstruct="D1_D4_MD5", nace_r2=["B-S_X_O", "C"], geo=list(EUROSTAT_GEOS), sinceTimePeriod="2008")
    ind_codes = {"A-S": "All industries", "C": "Manufacturing", "F": "Construction", "G": "Wholesale and retail trade", "H": "Transportation and storage",
                 "J": "Information and communication", "K": "Financial and insurance activities", "M": "Professional, scientific and technical activities",
                 "O": "Public administration and defence", "P": "Education", "Q": "Human health and social work activities"}
    pi, si, mi = ssb_series(ssb_data("11417", [("NACE2007", list(ind_codes)), ("ContentsCode", ["Arslonn", "ArslonnEndring"]), ("Tid", ["from(2015)"])]))
    series = {
        "wage_growth": S("Annual earnings, nominal growth", "per cent", "A", pa, sa[("ArslonnEndring",)], 1),
        "real_wage_growth": S("Annual earnings, real growth (CPI-deflated)", "per cent", "A", pa, sa[("ReallonnEndring",)], 1),
        "wage_level": S("Annual earnings, all employees", "NOK", "A", pa, sa[("Arslonn",)], 0),
    }
    for code, lab in ind_codes.items():
        series["wg_" + code.replace("-", "")] = S(f"Annual earnings growth, {lab}", "per cent", "A", pi, si[(code, "ArslonnEndring")], 1)
    # Hourly labour costs: compensation of employees per hour worked by employees (national accounts), NOK
    for code, lab, k in (("nr23_6fn", "Mainland Norway", "mainland"), ("nr23ind", "Manufacturing", "mfg")):
        W, H = sh[(code, "LKostnader")], sh[(code, "TimeverkL")]
        series[f"lc_hour_{k}"] = S(f"Labour cost per hour worked, {lab}", "NOK", "A", ph, [None if (w is None or not h) else round(w / h, 0) for w, h in zip(W, H)], 0, derived=True,
                                   note="Compensation of employees divided by hours worked by employees, national accounts (SSB 09174).")
        series[f"lc_hour_{k}_growth"] = S(f"Labour cost per hour worked, {lab}, growth", "per cent", "A", ph, sh[(code, "LKostTimeverkProsent")], 1)
    # Eurostat labour cost levels, EUR per hour (the survey years 2008, 2012, 2016 and annual estimates from 2020)
    for geo, lab in EUROSTAT_GEOS.items():
        series[f"lc_eur_{geo.lower()}"] = S(f"Hourly labour cost, {lab}, whole economy excl. public administration", "EUR per hour", "A", et, es[("A", "EUR", "D1_D4_MD5", "B-S_X_O", geo)], 1)
    for geo in ("NO", "SE", "DK", "DE", "EU27_2020"):
        series[f"lc_mfg_eur_{geo.lower()}"] = S(f"Hourly labour cost, {EUROSTAT_GEOS[geo]}, manufacturing", "EUR per hour", "A", et, es[("A", "EUR", "D1_D4_MD5", "C", geo)], 1)
    return {"series": series,
            "sources": [src("Statistics Norway, annual earnings, nominal and real", "14858", "https://www.ssb.no/en/statbank/table/14858", ma["updated"]),
                        src("Statistics Norway, annual earnings by industry", "11417", "https://www.ssb.no/en/statbank/table/11417", mi["updated"]),
                        src("Statistics Norway, wages, employment and productivity by industry", "09174", "https://www.ssb.no/en/statbank/table/09174", mh["updated"]),
                        src("Eurostat, labour cost levels by NACE Rev. 2 activity", "lc_lci_lev", "https://ec.europa.eu/eurostat/databrowser/view/lc_lci_lev/default/table", eu,
                            "Labour cost surveys every fourth year (2008, 2012, 2016) and annual estimates from 2020; converted to euro at annual average exchange rates, so the krone's movements show up in Norway's level.")]}


def ind_labour_shares() -> dict:
    _, _, mn = na_income()
    shares = [(k, lab, labour_shares(code)) for code, (k, lab) in NA_INDUSTRIES.items()]
    shares.insert(2, ("business", "Mainland Norway excl. general government", labour_shares("nr23_6fn", minus=NA_GOVERNMENT)))
    series = {}
    for k, lab, (years, gross, net) in shares:
        series[f"gross_{k}"] = S(f"Gross labour share, {lab}", "per cent", "A", years, gross, 1, derived=True)
    for k, lab, (years, gross, net) in shares:
        series[f"net_{k}"] = S(f"Net labour share, {lab}", "per cent", "A", years, net, 1, derived=True)
    return {"series": series,
            "method": ("Labour shares are computed from the national accounts in current prices. Gross labour share = compensation of employees / "
                       "(compensation of employees + consumption of fixed capital + net operating surplus); net labour share = compensation of employees / "
                       "(compensation of employees + net operating surplus). Taxes and subsidies on production are excluded from the denominator, and income of the "
                       "self-employed is part of the operating surplus. For general government the operating surplus is essentially zero, so the mainland and "
                       "total-economy shares are pulled up by the public sector; the series excluding general government (Mainland Norway minus general government, "
                       "own aggregation) avoids this. The definitions follow Holm and Jasinski, «Lønnsandelen i industrien», Samfunnsøkonomen."),
            "sources": [src("Statistics Norway, production account and income generation by industry", "09170", "https://www.ssb.no/en/statbank/table/09170", mn["updated"])]}


def ind_productivity() -> dict:
    py, sy, my = ssb_series(ssb_data("09189", [("Makrost", ["bnpb.nr23_6fn"]), ("ContentsCode", ["Faste"]), ("Tid", ["from(1970)"])]))
    pl, sl, ml = ssb_series(ssb_data("09174", [("NACE", ["nr23_6fn"]), ("ContentsCode", ["Timeverk"]), ("Tid", ["from(1970)"])]))
    pk, sk, mk = ssb_series(ssb_data("09181", [("NACE", ["nr23_6fn"]), ("ContentsCode", ["KAPN4"]), ("Tid", ["from(1970)"])]))
    ys_, gross_share, _ = labour_shares("nr23_6fn")
    _, _, mn = na_income()
    Y = dict(zip(py, sy[("bnpb.nr23_6fn", "Faste")]))
    L = dict(zip(pl, sl[("nr23_6fn", "Timeverk")]))
    K = dict(zip(pk, sk[("nr23_6fn", "KAPN4")]))
    SH = {y: v / 100 for y, v in zip(ys_, gross_share) if v is not None}          # labour share, fraction
    years = [y for y in py if Y.get(y) and L.get(y) and K.get(y) and SH.get(y)]
    lp = [Y[y] / L[y] for y in years]                                   # value added per hour, NOK (2023 prices)
    kl = [K[y] / L[y] for y in years]
    # TFP in growth form (Törnqvist growth accounting): dlog A = dlog Y - (1-s)*dlog K - s*dlog L, where s is the average of the
    # labour share in the two adjacent years (from the national accounts), then cumulated into a level index.
    # Levels of chain-linked volumes are not comparable across tables, growth rates are.
    dlog_a = [None] * len(years)
    for i in range(1, len(years)):
        y0, y1 = years[i - 1], years[i]
        if int(y1) - int(y0) != 1:
            continue
        s_bar = 0.5 * (SH[y0] + SH[y1])
        dlog_a[i] = ((math.log(Y[y1]) - math.log(Y[y0]))
                     - (1 - s_bar) * (math.log(K[y1]) - math.log(K[y0]))
                     - s_bar * (math.log(L[y1]) - math.log(L[y0])))
    tfp = [None] * len(years)
    level = 1.0
    for i in range(len(years)):
        if i == 0:
            tfp[i] = level
        elif dlog_a[i] is None:
            break                                                        # a gap in the data ends the cumulated series
        else:
            level *= math.exp(dlog_a[i])
            tfp[i] = level
    tfp_growth = [None if g is None else round((math.exp(g) - 1) * 100, 2) for g in dlog_a]
    base = "2000"
    series = {
        "labour_productivity": S("Labour productivity, Mainland Norway: value added per hour worked (2000=100)", "index", "A", years, index_to(lp, years, base), 1, derived=True),
        "tfp": S("Total factor productivity, Mainland Norway (2000=100)", "index", "A", years, index_to(tfp, years, base), 1, derived=True),
        "capital_per_hour": S("Capital per hour worked, Mainland Norway (2000=100)", "index", "A", years, index_to(kl, years, base), 1, derived=True),
        "labour_productivity_growth": S("Labour productivity growth", "per cent", "A", years, pct_change(lp, 1), 1, derived=True),
        "tfp_growth": S("TFP growth", "per cent", "A", years, tfp_growth, 1, derived=True),
        "labour_share": S("Labour share used as weight, Mainland Norway (gross)", "per cent", "A", years, [round(SH[y] * 100, 1) for y in years], 1, derived=True,
                          note="Compensation of employees / (compensation of employees + consumption of fixed capital + net operating surplus), SSB 09170."),
        "output_growth": S("Value added growth, Mainland Norway (volume)", "per cent", "A", years, pct_change([Y[y] for y in years], 1), 1, derived=True),
        "hours_growth": S("Hours worked growth, Mainland Norway", "per cent", "A", years, pct_change([L[y] for y in years], 1), 1, derived=True),
        "capital_growth": S("Fixed capital growth, Mainland Norway (volume)", "per cent", "A", years, pct_change([K[y] for y in years], 1), 1, derived=True),
        "value_added_per_hour": S("Value added per hour worked, Mainland Norway", "NOK, 2023 prices", "A", years, [round(v * 1e6 / 1e6, 0) for v in lp], 0, derived=True),
    }
    return {"series": series,
            "method": ("TFP is computed by growth accounting (Törnqvist index) from a Cobb-Douglas production function. Annual TFP growth is "
                       "Δlog A = Δlog Y − (1 − s̄) Δlog K − s̄ Δlog L, where s̄ is the average of the labour share in the two adjacent years, and the TFP index is the "
                       "cumulated growth rates rebased to 2000 = 100. The labour share is compensation of employees divided by compensation of employees plus "
                       "consumption of fixed capital plus net operating surplus for Mainland Norway (SSB 09170, current prices); it is shown as a separate series. "
                       f"Its mean over the sample is {sum(SH[y] for y in years) / len(years) * 100:.1f} per cent. "
                       "Y is gross value added in Mainland Norway at constant 2023 prices (SSB 09189), L is total hours worked in Mainland Norway (SSB 09174), "
                       "K is the net stock of fixed capital in Mainland Norway at constant 2023 prices (SSB 09181). Labour productivity is Y/L. "
                       "No adjustment is made for capacity utilisation, labour quality or self-employed income (which is part of the operating surplus)."),
            "sources": [src("Statistics Norway, annual national accounts", "09189", "https://www.ssb.no/en/statbank/table/09189", my["updated"]),
                        src("Statistics Norway, employment and hours worked by industry", "09174", "https://www.ssb.no/en/statbank/table/09174", ml["updated"]),
                        src("Statistics Norway, fixed capital by industry", "09181", "https://www.ssb.no/en/statbank/table/09181", mk["updated"]),
                        src("Statistics Norway, production account and income generation by industry", "09170", "https://www.ssb.no/en/statbank/table/09170", mn["updated"])]}

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
    proj = None
    try:
        proj = nb_projections()
        print(f"  ok   {'projections':16} Monetary Policy Report {proj['report']}")
    except Exception as e:   # the inflation and rates indicators then keep the previously fetched projections
        errors["projections"] = f"{type(e).__name__}: {e}"
        print(f"  FAIL {'projections':16} {errors['projections']}", file=sys.stderr)
    run("inflation", ind_inflation, proj, previous.get("inflation"))
    run("unemployment", ind_unemployment)
    run("employment", ind_employment)
    run("rates", ind_rates, proj, previous.get("rates"))
    run("fx", ind_fx)
    run("trade", ind_trade)
    run("current_account", ind_current_account, gdp)
    run("oil_fund", ind_oil_fund, gdp)
    run("house_prices", ind_house_prices)
    run("productivity", ind_productivity)
    run("wages", ind_wages)
    run("labour_shares", ind_labour_shares)

    OUT.parent.mkdir(exist_ok=True)
    payload = {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "errors": errors, "indicators": indicators}
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size/1024:.0f} kB), {len(indicators)} indicators, {len(errors)} errors")
    return 0 if not errors else (0 if indicators else 1)


if __name__ == "__main__":
    sys.exit(main())
