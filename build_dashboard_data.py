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
  - Derived series (TFP, labor productivity, current account in percent of GDP, year-on-year
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
from datetime import datetime, timedelta, timezone
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

# --------------------------------------------------------------------------- small math

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
            "qoq": S("GDP Mainland Norway, quarter-on-quarter volume growth (seasonally adjusted)", "percent", "Q", per, s[("bnpb.nr23_9fn", "VolumEndrSesJust")], 1),
            "yoy": S("GDP Mainland Norway, volume growth from same quarter a year earlier", "percent", "Q", per, s[("bnpb.nr23_9fn", "Volum")], 1),
            "total_qoq": S("GDP total, quarter-on-quarter volume growth (seasonally adjusted)", "percent", "Q", per, s[("bnpb.nr23_9", "VolumEndrSesJust")], 1),
            "annual": S("GDP Mainland Norway, annual volume growth", "percent", "A", pa, sa[("bnpb.nr23_9fn", "Volum")], 1),
            "annual_total": S("GDP total, annual volume growth", "percent", "A", pa, sa[("bnpb.nr23_9", "Volum")], 1),
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
        "cpi_yoy": S("CPI, 12-month change", "percent", "M", per, s[("00", "Tolvmanedersendring")], 1),
        "cpiate_yoy": S("CPI-ATE (adjusted for tax changes, excluding energy), 12-month change", "percent", "M", per2, s2[("KPI-JAE", "00", "Tolvmanedersendring")], 1),
        "cpiat_yoy": S("CPI-AT (adjusted for tax changes), 12-month change", "percent", "M", per2, s2[("KPI-JA", "00", "Tolvmanedersendring")], 1),
        "cpi_mom": S("CPI, monthly change", "percent", "M", per, s[("00", "Manedsendring")], 1),
        "cpi_index": S("CPI (2025=100)", "index", "M", per, s[("00", "KpiIndMnd")], 1),
    }
    for code, lab in labels3.items():
        series["grp_" + code] = S(lab + ", 12-month change", "percent", "M", per3, s3[("KPI", code, "Tolvmanedersendring")], 1)
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


def nav_edition(text: str) -> str:
    """NAV states the edition in Norwegian ("oktober 2026")."""
    months = {"januar": "January", "februar": "February", "mars": "March", "april": "April", "mai": "May", "juni": "June", "juli": "July",
              "august": "August", "september": "September", "oktober": "October", "november": "November", "desember": "December"}
    word, _, rest = text.partition(" ")
    return (months.get(word.lower(), word) + " " + rest).strip()


def _nav_fetch() -> tuple[dict, dict]:
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
        "nav_unemployment": S("Registered unemployment, NAV (seasonally adjusted)", "percent of labor force", "M", p_sa, v_sa, 1),
        "nav_gross": S("Registered unemployment incl. labor market programs, NAV (seasonally adjusted)", "percent of labor force", "M", p_gr, v_gr, 1),
        "nav_unemployment_nsa": S("Registered unemployment, NAV (not seasonally adjusted)", "percent of labor force", "M", p_h, v_h, 1),
        "nav_count": S("Registered fully unemployed, NAV (seasonally adjusted)", "persons", "M", p_n, v_n, 0),
    }
    source = src("NAV, Hovedtall om arbeidsmarkedet", None, page, None,
                 ("Registered fully unemployed as a share of the labor force. The seasonally adjusted series start in 2011 (2017 for the gross measure). "
                  "Statistics for April to July 2025 are affected by NAV's change of job-seeker register."
                  + (f" Edition: {nav_edition(edition.group(1))}." if edition else "")))
    return series, source


_NAV = None


def nav_registered() -> tuple[dict, dict]:
    global _NAV
    if _NAV is None:
        _NAV = _nav_fetch()
    return _NAV


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
                                                ("ContentsCode", ["SysselProsBefolkn", "ArbledProsArbstyrk", "Sysselsatte", "Arbeidsledige"]), ("Tid", ["from(2009K1)"])]))
    return _LFS_Q


LFS_NOTE_M = "Three-month moving averages are dated by the middle month."


def ind_unemployment() -> dict:
    per, s, m = lfs_monthly()
    perq, sq, mq = lfs_quarterly()
    g = lambda sex, age, c: s[(sex, age, "3MGG", c)]
    q = lambda sex, age, c: sq[(sex, age, "S", c)]
    nav_series, nav_source = nav_registered()
    series = {
        "unemployment": S("Unemployment rate, LFS, 15-74 years (seasonally adjusted, 3-month moving average)", "percent of labor force", "M", per, g("0", "15-74", "ArbledProsArbstyrk"), 1),
        **nav_series,
        "unemployment_men": S("Unemployment rate, LFS, men (seasonally adjusted, 3-month moving average)", "percent of labor force", "M", per, g("1", "15-74", "ArbledProsArbstyrk"), 1),
        "unemployment_women": S("Unemployment rate, LFS, women (seasonally adjusted, 3-month moving average)", "percent of labor force", "M", per, g("2", "15-74", "ArbledProsArbstyrk"), 1),
        "unemployment_15_24": S("Unemployment rate, LFS, 15-24 years (seasonally adjusted, 3-month moving average)", "percent of labor force", "M", per, g("0", "15-24", "ArbledProsArbstyrk"), 1),
        "unemployment_25_74": S("Unemployment rate, LFS, 25-74 years (seasonally adjusted, 3-month moving average)", "percent of labor force", "M", per, g("0", "25-74", "ArbledProsArbstyrk"), 1),
        "unemployed": S("Unemployed persons, LFS (seasonally adjusted, 3-month moving average)", "1000 persons", "M", per, g("0", "15-74", "Arbeidsledige"), 0),
        "q_unemployment_25_54": S("Unemployment rate, LFS, 25-54 years (quarterly, seasonally adjusted)", "percent of labor force", "Q", perq, q("0", "25-54", "ArbledProsArbstyrk"), 1),
        "q_unemployment_55_74": S("Unemployment rate, LFS, 55-74 years (quarterly, seasonally adjusted)", "percent of labor force", "Q", perq, q("0", "55-74", "ArbledProsArbstyrk"), 1),
        "q_unemployment_men_15_24": S("Unemployment rate, LFS, men 15-24 years (quarterly, seasonally adjusted)", "percent of labor force", "Q", perq, q("1", "15-24", "ArbledProsArbstyrk"), 1),
        "q_unemployment_women_15_24": S("Unemployment rate, LFS, women 15-24 years (quarterly, seasonally adjusted)", "percent of labor force", "Q", perq, q("2", "15-24", "ArbledProsArbstyrk"), 1),
    }
    return {"series": series,
            "sources": [src("Statistics Norway, Labor force survey, monthly", "13760", "https://www.ssb.no/en/statbank/table/13760", m["updated"], LFS_NOTE_M),
                        src("Statistics Norway, Labor force survey, quarterly", "14483", "https://www.ssb.no/en/statbank/table/14483", mq["updated"]),
                        nav_source]}


def ind_employment() -> dict:
    per, s, m = lfs_monthly()
    perq, sq, mq = lfs_quarterly()
    g = lambda sex, age, c: s[(sex, age, "3MGG", c)]
    q = lambda sex, age, c: sq[(sex, age, "S", c)]
    series = {
        "employment_rate": S("Employment rate, LFS, 15-74 years (seasonally adjusted, 3-month moving average)", "percent of population", "M", per, g("0", "15-74", "SysselProsBefolkn"), 1),
        "employment_rate_men": S("Employment rate, LFS, men 15-74 years (seasonally adjusted, 3-month moving average)", "percent of population", "M", per, g("1", "15-74", "SysselProsBefolkn"), 1),
        "employment_rate_women": S("Employment rate, LFS, women 15-74 years (seasonally adjusted, 3-month moving average)", "percent of population", "M", per, g("2", "15-74", "SysselProsBefolkn"), 1),
        "employment_rate_15_24": S("Employment rate, LFS, 15-24 years (seasonally adjusted, 3-month moving average)", "percent of population", "M", per, g("0", "15-24", "SysselProsBefolkn"), 1),
        "employment_rate_25_74": S("Employment rate, LFS, 25-74 years (seasonally adjusted, 3-month moving average)", "percent of population", "M", per, g("0", "25-74", "SysselProsBefolkn"), 1),
        "participation": S("Labor force participation, 15-74 years (seasonally adjusted, 3-month moving average)", "percent of population", "M", per, g("0", "15-74", "ArbStyrkProsBefolkn"), 1),
        "employed": S("Employed persons, LFS, 15-74 years (seasonally adjusted, 3-month moving average)", "1000 persons", "M", per, g("0", "15-74", "Sysselsatte"), 0),
        "manweeks": S("Man-weeks worked of 37.5 hours (seasonally adjusted, 3-month moving average)", "1000 man-weeks", "M", per, g("0", "15-74", "Ukeverk"), 0),
    }
    ages = {"15-24": "15-24 years", "25-39": "25-39 years", "40-54": "40-54 years", "55-74": "55-74 years", "20-64": "20-64 years"}
    for code, lab in ages.items():
        series["q_rate_" + code.replace("-", "_")] = S(f"Employment rate, LFS, {lab} (quarterly, seasonally adjusted)", "percent of population", "Q", perq, q("0", code, "SysselProsBefolkn"), 1)
    for sex, sl in (("1", "men"), ("2", "women")):
        for code in ("15-24", "25-54", "55-74"):
            series[f"q_rate_{sl}_{code.replace('-', '_')}"] = S(f"Employment rate, LFS, {sl} {code} years (quarterly, seasonally adjusted)", "percent of population", "Q", perq, q(sex, code, "SysselProsBefolkn"), 1)
    return {"series": series,
            "sources": [src("Statistics Norway, Labor force survey, monthly", "13760", "https://www.ssb.no/en/statbank/table/13760", m["updated"], LFS_NOTE_M),
                        src("Statistics Norway, Labor force survey, quarterly", "14483", "https://www.ssb.no/en/statbank/table/14483", mq["updated"])]}


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
    pub = re.search(r"(\d{1,2}) (%s) (\d{4})" % MONTHS_EN, rhtml)
    return {"report": f"{n}/{year}", "url": page, "published": f"{pub.group(2)} {pub.group(1)}, {pub.group(3)}" if pub else None,
            "first": (year, n), "anchor": (year, n - 1) if n > 1 else (year - 1, 4), "data": data}


def proj_series(proj: dict, name: str, label: str, decimals: int) -> dict:
    """Quarterly projection as a monthly-frequency series (each quarter at its middle month) so it can overlay the monthly charts."""
    mid = {1: "02", 2: "05", 3: "08", 4: "11"}
    pts = sorted((k, v) for k, v in proj["data"][name].items() if k >= proj["anchor"])
    return S(f"{label}, Norges Bank projection (MPR {proj['report']})", "percent", "M",
             [f"{y}-{mid[q]}" for (y, q), _ in pts], [round(v, decimals) for _, v in pts], decimals, projection=True,
             note=f"Quarterly projections from Monetary Policy Report {proj['report']}, from the quarter of publication onwards. The first point is the preceding quarter. Each quarter is plotted at its middle month.")


def add_projections(result: dict, proj: dict | None, prev: dict | None, items: list) -> dict:
    if proj:
        for key, name, label, dec in items:
            result["series"][key] = proj_series(proj, name, label, dec)
        result["sources"].append(src(f"Norges Bank, Monetary Policy Report {proj['report']}", None, proj["url"], proj.get("published"),
                                     "Projections from the quarter of publication. Quarterly values plotted at the middle month of each quarter."))
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
                "policy_rate": S("Policy rate (sight deposit rate), monthly average", "percent", "M", *pol_m, 2),
                "policy_rate_daily": S("Policy rate (sight deposit rate), daily", "percent", "D", *pol_d, 2),
                "nowa": S("NOWA overnight rate, monthly average", "percent", "M", *nowa_m, 2),
                "gov10y": S("10-year government bond yield, monthly average", "percent", "M", *y10_m, 2),
                "gov10y_daily": S("10-year government bond yield, daily", "percent", "D", *y10_d, 2),
                "gov3y": S("3-year government bond yield, monthly average", "percent", "M", *y3_m, 2),
                "tbill3m": S("3-month treasury bill yield, monthly average", "percent", "M", *tb3_m, 2)},
            "decisions": decisions[-40:],
            "sources": [src("Norges Bank, policy rate", "IR", "https://data.norges-bank.no/", None),
                        src("Norges Bank, NOWA", "SHORT_RATES"), src("Norges Bank, generic government yields", "GOVT_GENERIC_RATES"),
                        src("Norges Bank, policy rate decisions", "ANN_KPRA")]}
    return add_projections(out, proj, prev, [("proj_policy_rate", "policy_rate", "Policy rate, quarterly average", 2)])


def ind_fx() -> dict:
    m = nb_series("EXR", "M.I44+EUR+USD+SEK+GBP.NOK.SP", split_by="BASE_CUR", startPeriod="1990-01-01")
    d = nb_series("EXR", "B.I44+EUR+USD.NOK.SP", split_by="BASE_CUR", startPeriod="2019-01-01")
    return {"series": {
                "i44": S("Import-weighted krone exchange rate, I-44 (1995=100, a lower value is a stronger krone)", "index", "M", *m["I44"], 2),
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
                "mainland_exports": S("Mainland exports (excl. oil, gas, ships and platforms, seasonally adjusted)", "NOK million", "M", per, g("Etotusorn"), 0),
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
        series["pct_gdp"] = S("Current account balance, percent of GDP (four-quarter sums)", "percent of GDP", "Q", per, pct, 1, derived=True,
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
                2008: 10.6, 2009: 13.8, 2010: 10.5, 2011: 8.1, 2012: 6.2, 2013: 6.6, 2014: 5.9, 2015: 5.7, 2016: 5.2, 2017: 6.0}


def nbim_chart_blocks(url: str) -> list[dict[str, tuple[list, list]]]:
    """Chart data embedded in an NBIM page: one dict per chart, {series name: (years, values)}. NBIM has no data API."""
    html = http_get(url, headers={"User-Agent": "Mozilla/5.0"}).decode("utf-8", "ignore")
    out = []
    for block in re.findall(r"<script[^>]*>(.*?)</script>", html, re.S):
        if "Date.UTC" not in block:
            continue
        ser = {}
        for name, arr in re.findall(r"name:\s*['\"]([^'\"]*)['\"][^{}]*?data:\s*(\[[^\]]*(?:\][^\]]*)*?\]\s*\])", block, re.S):
            pts = re.findall(r"Date\.UTC\((\d+),\s*(\d+),\s*(\d+)\)\s*,\s*(-?[\d.]+)", arr)
            if pts and name not in ser:
                ser[name] = ([p[0] for p in pts], [float(p[3]) for p in pts])
        if ser:
            out.append(ser)
    return out


def ind_oil_fund(gdp: dict | None) -> dict:
    url = "https://www.nbim.no/en/investments/the-funds-value/"
    comp = next((b for b in nbim_chart_blocks(url) if "Total market value" in b and "Equity" in b), None)
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
        series["pct_gdp"] = S("Market value in percent of GDP", "percent of GDP", "A", years, ratio, 0, derived=True,
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
    # Costs as a share of the fund: NBIM's own ratio where it is published with enough precision, otherwise costs over average market value
    sy, sv = [], []
    for y in sorted(set(NBIM_COST_BP) | set(NBIM_COSTS_REPORTED)):
        if y in NBIM_COST_BP:
            v = NBIM_COST_BP[y] / 100
        else:
            a, b = value.get(str(y - 1)), value.get(str(y))
            if a is None or b is None:
                continue
            v = NBIM_COSTS_REPORTED[y] / ((a + b) / 2) * 100
        sy.append(str(y))
        sv.append(round(v, 3))
    series["cost_share"] = S("Management costs, share of the fund", "percent of assets under management", "A", sy, sv, 3, derived=True,
                             note=("1998–2017: NBIM's published cost ratio. 2018 onwards: reported costs divided by the average of the opening and "
                                   "closing market value, because NBIM states the ratio to two decimals only."))
    # Asset classes as shares of the fund
    for key, name in (("equity", "Equity"), ("fixed_income", "Fixed income"), ("real_estate", "Real estate"), ("renewables", "Renewable energy infrastructure")):
        if key in series:
            p, v = series[key]["periods"], series[key]["values"]
            series["share_" + key] = S(f"{name}, share of the fund", "percent of market value", "A", p,
                                       [None if (x is None or not value.get(y)) else round(100 * x / value[y], 1) for y, x in zip(p, v)], 1, derived=True)
    # Returns and relative return (the fund's return minus the benchmark's), from the charts on NBIM's returns page.
    # The series names are NBIM's own and must match the page.
    ret_url = "https://www.nbim.no/en/investments/returns/"
    rblocks = nbim_chart_blocks(ret_url)

    def pick(name):
        for b in rblocks:
            if name in b:
                return b[name]
        raise RuntimeError(f"NBIM returns page: no chart series named {name!r}")

    def since_start(yrs, vals):   # an asset class is reported as zero before it entered the fund
        i = next((k for k, v in enumerate(vals) if v != 0), len(vals))
        return yrs[i:], vals[i:]

    series["return"] = S("Annual return on the fund", "percent", "A", *pick("Annual return"), 2)
    series["return_accum"] = S("Accumulated annualized return since 1998", "percent", "A", *pick("Accumulated annualised return"), 2)
    series["relative_return"] = S("Annual relative return, fund minus benchmark index", "percentage points", "A", *pick("Annual relative return"), 2)
    series["relative_return_accum"] = S("Accumulated annualized relative return since 1998", "percentage points", "A", *pick("Accumulated annualised relative return"), 2)
    classes = next((b for b in rblocks if "Unlisted real estate" in b and "Equity" in b), None)
    if classes is None:
        raise RuntimeError("NBIM returns page: no chart with returns by asset class")
    for key, name, lab in (("return_equity", "Equity", "Equity"), ("return_fixed_income", "Fixed income", "Fixed income"),
                           ("return_real_estate", "Unlisted real estate", "Unlisted real estate"),
                           ("return_renewables", "Unlisted renewable infrastructure", "Unlisted renewable energy infrastructure")):
        if name in classes:
            series[key] = S(f"{lab}, annual return", "percent", "A", *since_start(*classes[name]), 2)
    return {"series": series,
            "sources": [src("Norges Bank Investment Management, returns", None, ret_url, None,
                            "Returns are measured in the fund's currency basket, before management costs. The relative return is the fund's return minus "
                            "the return on the benchmark index set by the Ministry of Finance."),
                        src("Norges Bank Investment Management, the fund's value", None, url, None,
                            "Year-end values read from the chart on NBIM's page. NBIM publishes no open data API."),
                        src("Norges Bank Investment Management, annual reports", None, "https://www.nbim.no/en/news-and-insights/reports/", None,
                            "Management costs are not published as data. The figures are transcribed from the annual reports and updated once a year.")]}


def ind_house_prices() -> dict:
    per, s, m = ssb_series(ssb_data("07221", [("Region", ["TOTAL", "001", "003", "004", "002"]), ("Boligtype", ["00"]),
                                               ("ContentsCode", ["SesJustBoligindeks", "Boligindeks"]), ("Tid", ["from(1992K1)"])]))
    idx = s[("TOTAL", "00", "SesJustBoligindeks")]
    nsa = s[("TOTAL", "00", "Boligindeks")]
    series = {
        "index": S("House price index, whole country (2015=100, seasonally adjusted)", "index", "Q", per, idx, 1),
        "yoy": S("House prices, change from same quarter a year earlier", "percent", "Q", per, pct_change(nsa, 4), 1, derived=True),
        "qoq": S("House prices, change from previous quarter (seasonally adjusted)", "percent", "Q", per, pct_change(idx, 1), 1, derived=True),
    }
    for code, lab in (("001", "Oslo incl. Bærum"), ("003", "Bergen"), ("004", "Trondheim"), ("002", "Stavanger")):
        series["yoy_" + code] = S(lab + ", change from same quarter a year earlier", "percent", "Q", per, pct_change(s[(code, "00", "Boligindeks")], 4), 1, derived=True)
    return {"series": series,
            "sources": [src("Statistics Norway, price index for existing dwellings", "07221", "https://www.ssb.no/en/statbank/table/07221", m["updated"])]}


# --------------------------------------------------------------------------- national accounts income by industry (SSB 09170)

NA_INDUSTRIES = {   # SSB code: (key, label). Gross and net labor shares are computed for each.
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


def labor_shares(code: str, minus: str | None = None) -> tuple[list[str], list, list]:
    """(years, gross share, net share) in percent. Gross = W/(W+DEP+OS), net = W/(W+OS), as in Holm & Jasinski (Samfunnsøkonomen).
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


def ind_wages() -> dict:
    pa, sa, ma = ssb_series(ssb_data("14858", [("ContentsCode", ["Arslonn", "ArslonnEndring", "ReallonnEndring"]), ("Tid", ["from(1970)"])]))
    ind_codes = {"A-S": "All industries", "C": "Manufacturing", "F": "Construction", "G": "Wholesale and retail trade", "H": "Transportation and storage",
                 "J": "Information and communication", "K": "Financial and insurance activities", "M": "Professional, scientific and technical activities",
                 "O": "Public administration and defense", "P": "Education", "Q": "Human health and social work activities"}
    pi, si, mi = ssb_series(ssb_data("11417", [("NACE2007", list(ind_codes)), ("ContentsCode", ["Arslonn", "ArslonnEndring"]), ("Tid", ["from(2015)"])]))
    series = {
        "wage_growth": S("Annual earnings, nominal growth", "percent", "A", pa, sa[("ArslonnEndring",)], 1),
        "real_wage_growth": S("Annual earnings, real growth (CPI-deflated)", "percent", "A", pa, sa[("ReallonnEndring",)], 1),
        "wage_level": S("Annual earnings, all employees", "NOK", "A", pa, sa[("Arslonn",)], 0),
    }
    for code, lab in ind_codes.items():
        series["wg_" + code.replace("-", "")] = S(f"Annual earnings growth, {lab}", "percent", "A", pi, si[(code, "ArslonnEndring")], 1)
    return {"series": series,
            "sources": [src("Statistics Norway, annual earnings, nominal and real", "14858", "https://www.ssb.no/en/statbank/table/14858", ma["updated"]),
                        src("Statistics Norway, annual earnings by industry", "11417", "https://www.ssb.no/en/statbank/table/11417", mi["updated"])]}


AVG_YEARS = 30   # the labor share chart draws each series' average over this many years (avgYears in dashboard.js)

# The eleven manufacturing sectors at the A38 level, as in Holm and Jasinski, «Lønnsandelen i industrien»
MFG_A38 = ["A38NP2X10_12", "A38NP2X13_15", "A38NP2X16_18", "A38NP2X19_21", "A38NP2X22_23", "A38NP2X24_25",
           "A38NP2X26", "A38NP2X27", "A38NP2X28", "A38NP2X29_30", "A38NP2X31_33"]


def _first_eigenvector(C: list[list[float]]) -> tuple[list[float], float]:
    """Dominant eigenvector and eigenvalue of a symmetric positive semi-definite matrix (power iteration, standard library only)."""
    n = len(C)
    v = [1.0 + 0.01 * i for i in range(n)]
    norm = math.sqrt(sum(x * x for x in v))
    v = [x / norm for x in v]
    for _ in range(20000):
        w = [sum(C[i][j] * v[j] for j in range(n)) for i in range(n)]
        norm = math.sqrt(sum(x * x for x in w))
        w = [x / norm for x in w]
        diff = max(abs(a - b) for a, b in zip(w, v))
        v = w
        if diff < 1e-14:
            break
    lam = sum(v[i] * sum(C[i][j] * v[j] for j in range(n)) for i in range(n))
    return v, lam


def pc1_expanding(years: list[int], S: list[list[float]], GP: list[list[float]], minobs: int = 25, lag: int = 5) -> tuple[list, float | None]:
    """Real-time common component (first principal component) of sectoral labor shares; a port of pc1_expanding.m from the
    replication package of Holm and Jasinski. For each year t everything is estimated on data up to t-lag: sector means and standard
    deviations, the PC1 loadings, and the weights (average share of total gross product). The loadings are applied to year t's
    standardized shares and the component is expressed in percent. Returns (component per year, variance explained in the last window)."""
    T, N = len(years), len(S[0])
    gp_share = [[g / sum(row) for g in row] for row in GP]
    out, explained = [None] * T, None
    for t in range(T):
        idx = [i for i in range(T) if years[i] <= years[t] - lag]
        n = len(idx)
        if n < minobs:
            continue
        Sw = [S[i] for i in idx]
        w = [sum(gp_share[i][j] for i in idx) / n for j in range(N)]
        mu = [sum(r[j] for r in Sw) / n for j in range(N)]
        sd = [math.sqrt(sum((r[j] - mu[j]) ** 2 for r in Sw) / (n - 1)) for j in range(N)]
        Z = [[(r[j] - mu[j]) / sd[j] for j in range(N)] for r in Sw]          # standardized, so already centered
        C = [[sum(z[a] * z[b] for z in Z) for b in range(N)] for a in range(N)]
        lam, top = _first_eigenvector(C)
        fw = [sum(z[j] * lam[j] for j in range(N)) for z in Z]                # the component in the window
        agg = [sum(r[j] * w[j] for j in range(N)) for r in Sw]                # the weighted aggregate in the window
        mf, ma = sum(fw) / n, sum(agg) / n
        if sum((a - mf) * (b - ma) for a, b in zip(fw, agg)) < 0:             # orient toward the aggregate
            lam = [-x for x in lam]
        zt = [(S[t][j] - mu[j]) / sd[j] for j in range(N)]
        ft = sum(zt[j] * lam[j] for j in range(N))
        out[t] = sum(w[j] * (mu[j] + sd[j] * lam[j] * ft) for j in range(N))
        explained = 100 * top / sum(C[j][j] for j in range(N))
    return out, explained


def mfg_common_component() -> dict:
    """Aggregate (M0) and common-component labor shares for manufacturing, gross and net, as in figure 5 of the paper."""
    per, s, meta = ssb_series(ssb_data("09170", [("NACE", MFG_A38), ("ContentsCode", ["LOKO", "DEP", "DRI"]), ("Tid", ["from(1970)"])]))
    rows = []
    for i, y in enumerate(per):
        W, D, O = ([s[(c, k)][i] for c in MFG_A38] for k in ("LOKO", "DEP", "DRI"))
        if None not in W and None not in D and None not in O:
            rows.append((int(y), W, D, O))
    years = [r[0] for r in rows]
    GP = [[w + d + o for w, d, o in zip(W, D, O)] for _, W, D, O in rows]
    Sg = [[100 * w / g for w, g in zip(W, G)] for (_, W, _, _), G in zip(rows, GP)]
    Sn = [[min(max(100 * w / (w + o), 0), 130) if (w + o) != 0 else 130 for w, o in zip(W, O)] for _, W, _, O in rows]   # clip extreme values
    tot_g = [100 * sum(W) / sum(G) for (_, W, _, _), G in zip(rows, GP)]
    tot_n = [100 * sum(W) / (sum(W) + sum(O)) for _, W, _, O in rows]
    out = {"years": [str(y) for y in years], "updated": meta["updated"]}
    for name, S_, tot in (("gross", Sg, tot_g), ("net", Sn, tot_n)):
        com, expl = pc1_expanding(years, S_, GP)
        ok = [i for i, c in enumerate(com) if c is not None]
        shift = sum(tot[i] for i in ok) / len(ok) - sum(com[i] for i in ok) / len(ok)   # re-center on the aggregate's mean over the same years
        out[name] = [None if c is None else round(c + shift, 1) for c in com]      # the paper's level (figure 5)
        out[name + "_raw"] = com                                                    # before re-centering
        out[name + "_total"] = [round(x, 1) for x in tot]
        out[name + "_explained"] = expl
    return out


def ind_labor_shares() -> dict:
    _, _, mn = na_income()
    shares = [(k, lab, labor_shares(code)) for code, (k, lab) in NA_INDUSTRIES.items()]
    shares.insert(2, ("business", "Mainland Norway excl. general government", labor_shares("nr23_6fn", minus=NA_GOVERNMENT)))
    cc = mfg_common_component()
    cc_note = ("First principal component of the labor shares of the eleven A38 manufacturing sectors, computed in real time as in Holm and Jasinski: "
               "for each year, sector means, standard deviations, loadings and value-added weights are estimated on data up to five years earlier "
               f"(at least 25 years, so the series starts in 1999). The level of a principal component is a normalization. Here it is set so that the component's "
               f"average equals the average of the manufacturing labor share over the past {AVG_YEARS} years, the dashed line in the chart. "
               "The papercenters it on the mean of the manufacturing share over the component's own years, which moves the whole line by a constant only.")
    series = {}
    for k, lab, (years, gross, net) in shares:
        series[f"gross_{k}"] = S(f"Gross labor share, {lab}", "percent", "A", years, gross, 1, derived=True)
    first = next(i for i, v in enumerate(cc["gross"]) if v is not None)      # the real-time component starts when the window is long enough
    cc = {k: (v[first:] if isinstance(v, list) else v) for k, v in cc.items()}
    mfg_years, mfg_gross, mfg_net = next(v for k, _, v in shares if k == "mfg")

    def recenter(raw, ref_vals):
        """Shift the component so that its mean equals the manufacturing share's average over the last AVG_YEARS years (the chart's dashed line)."""
        ref = [(y, v) for y, v in zip(mfg_years, ref_vals) if v is not None][-AVG_YEARS:]
        window = {y for y, _ in ref}
        target = sum(v for _, v in ref) / len(ref)
        inside = [c for y, c in zip(cc["years"], raw) if c is not None and y in window]
        shift = target - sum(inside) / len(inside)
        return [None if c is None else round(c + shift, 3) for c in raw]

    series["gross_pc"] = S("Gross labor share, manufacturing, common component across sectors", "percent", "A", cc["years"], recenter(cc["gross_raw"], mfg_gross), 1, derived=True, note=cc_note)
    for k, lab, (years, gross, net) in shares:
        series[f"net_{k}"] = S(f"Net labor share, {lab}", "percent", "A", years, net, 1, derived=True)
    series["net_pc"] = S("Net labor share, manufacturing, common component across sectors", "percent", "A", cc["years"], recenter(cc["net_raw"], mfg_net), 1, derived=True, note=cc_note)
    return {"series": series,
            "method": ("Labor shares are computed from the national accounts in current prices. Gross labor share = compensation of employees / "
                       "(compensation of employees + consumption of fixed capital + net operating surplus). Net labor share = compensation of employees / "
                       "(compensation of employees + net operating surplus). Taxes and subsidies on production are excluded from the denominator, and income of the "
                       "self-employed is part of the operating surplus. For general government the operating surplus is essentially zero, so the mainland and "
                       "total-economy shares are pulled up by the public sector. The series excluding general government (Mainland Norway minus general government, "
                       "own aggregation) avoids this. The definitions follow Holm and Jasinski, «Lønnsandelen i industrien», Samfunnsøkonomen. "
                       "The common component for manufacturing is the first principal component of the sectoral labor shares from the same paper, "
                       f"computed in real time. In the latest estimation window it explains {cc['gross_explained']:.1f} percent of the variance of the gross shares "
                       f"and {cc['net_explained']:.1f} percent of the net shares."),
            "sources": [src("Statistics Norway, production account and income generation by industry", "09170", "https://www.ssb.no/en/statbank/table/09170", mn["updated"])]}


def growth_accounting(Y: dict, L: dict, K: dict, SH: dict) -> dict:
    """
    Labor productivity and total factor productivity from annual {year: value} series: Y value added (volume), L hours worked,
    K net stock of fixed capital (volume) and SH the labor share (fraction). Used for Mainland Norway and for the country comparisons.

    TFP is in growth form (Törnqvist growth accounting): dlog A = dlog Y - (1-s)*dlog K - s*dlog L, where s is the average of the
    labor share in the two adjacent years, then cumulated into a level. Levels of chain-linked volumes are not comparable across
    tables, growth rates are. Labor productivity covers the years with Y and L. The TFP level covers the first unbroken run of
    years that also have K and SH: a gap in the data ends the cumulated series.
    """
    years = sorted(y for y in Y if Y.get(y) and L.get(y))
    lp = [Y[y] / L[y] for y in years]
    dlog_a = [None] * len(years)
    for i in range(1, len(years)):
        y0, y1 = years[i - 1], years[i]
        if int(y1) - int(y0) != 1 or not all(d.get(y) for d in (K, SH) for y in (y0, y1)):
            continue
        s_bar = 0.5 * (SH[y0] + SH[y1])
        dlog_a[i] = ((math.log(Y[y1]) - math.log(Y[y0]))
                     - (1 - s_bar) * (math.log(K[y1]) - math.log(K[y0]))
                     - s_bar * (math.log(L[y1]) - math.log(L[y0])))
    tfp = [None] * len(years)
    start = next((i for i in range(1, len(years)) if dlog_a[i] is not None), None)
    if start is not None:
        level = 1.0
        tfp[start - 1] = level
        for i in range(start, len(years)):
            if dlog_a[i] is None:
                break
            level *= math.exp(dlog_a[i])
            tfp[i] = level
    return {"years": years, "lp": lp, "tfp": tfp, "tfp_growth": [None if g is None else round((math.exp(g) - 1) * 100, 2) for g in dlog_a]}


def ind_productivity() -> dict:
    py, sy, my = ssb_series(ssb_data("09189", [("Makrost", ["bnpb.nr23_6fn"]), ("ContentsCode", ["Faste"]), ("Tid", ["from(1970)"])]))
    pl, sl, ml = ssb_series(ssb_data("09174", [("NACE", ["nr23_6fn"]), ("ContentsCode", ["Timeverk"]), ("Tid", ["from(1970)"])]))
    pk, sk, mk = ssb_series(ssb_data("09181", [("NACE", ["nr23_6fn"]), ("ContentsCode", ["KAPN4"]), ("Tid", ["from(1970)"])]))
    ys_, gross_share, _ = labor_shares("nr23_6fn")
    _, _, mn = na_income()
    Y = dict(zip(py, sy[("bnpb.nr23_6fn", "Faste")]))
    L = dict(zip(pl, sl[("nr23_6fn", "Timeverk")]))
    K = dict(zip(pk, sk[("nr23_6fn", "KAPN4")]))
    SH = {y: v / 100 for y, v in zip(ys_, gross_share) if v is not None}          # labor share, fraction
    years = [y for y in py if Y.get(y) and L.get(y) and K.get(y) and SH.get(y)]
    kl = [K[y] / L[y] for y in years]
    ga = growth_accounting({y: Y[y] for y in years}, {y: L[y] for y in years}, K, SH)
    lp, tfp, tfp_growth = ga["lp"], ga["tfp"], ga["tfp_growth"]         # lp: value added per hour, NOK (2023 prices)
    base = "2000"
    series = {
        "labor_productivity": S("Labor productivity, Mainland Norway: value added per hour worked (2000=100)", "index", "A", years, index_to(lp, years, base), 1, derived=True),
        "tfp": S("Total factor productivity, Mainland Norway (2000=100)", "index", "A", years, index_to(tfp, years, base), 1, derived=True),
        "capital_per_hour": S("Capital per hour worked, Mainland Norway (2000=100)", "index", "A", years, index_to(kl, years, base), 1, derived=True),
        "labor_productivity_growth": S("Labor productivity growth", "percent", "A", years, pct_change(lp, 1), 1, derived=True),
        "tfp_growth": S("TFP growth", "percent", "A", years, tfp_growth, 1, derived=True),
        "labor_share": S("Labor share used as weight, Mainland Norway (gross)", "percent", "A", years, [round(SH[y] * 100, 1) for y in years], 1, derived=True,
                          note="Compensation of employees / (compensation of employees + consumption of fixed capital + net operating surplus), SSB 09170."),
        "output_growth": S("Value added growth, Mainland Norway (volume)", "percent", "A", years, pct_change([Y[y] for y in years], 1), 1, derived=True),
        "hours_growth": S("Hours worked growth, Mainland Norway", "percent", "A", years, pct_change([L[y] for y in years], 1), 1, derived=True),
        "capital_growth": S("Fixed capital growth, Mainland Norway (volume)", "percent", "A", years, pct_change([K[y] for y in years], 1), 1, derived=True),
        "value_added_per_hour": S("Value added per hour worked, Mainland Norway", "NOK, 2023 prices", "A", years, [round(v * 1e6 / 1e6, 0) for v in lp], 0, derived=True),
    }
    return {"series": series,
            "method": ("TFP is computed by growth accounting (Törnqvist index) from a Cobb-Douglas production function. Annual TFP growth is "
                       "Δlog A = Δlog Y − (1 − s̄) Δlog K − s̄ Δlog L, where s̄ is the average of the labor share in the two adjacent years, and the TFP index is the "
                       "cumulated growth rates rebased to 2000 = 100. The labor share is compensation of employees divided by compensation of employees plus "
                       "consumption of fixed capital plus net operating surplus for Mainland Norway (SSB 09170, current prices). It is shown as a separate series. "
                       f"Its mean over the sample is {sum(SH[y] for y in years) / len(years) * 100:.1f} percent. "
                       "Y is gross value added in Mainland Norway at constant 2023 prices (SSB 09189), L is total hours worked in Mainland Norway (SSB 09174), "
                       "K is the net stock of fixed capital in Mainland Norway at constant 2023 prices (SSB 09181). Labor productivity is Y/L. "
                       "No adjustment is made for capacity utilization, labor quality or self-employed income (which is part of the operating surplus)."),
            "sources": [src("Statistics Norway, annual national accounts", "09189", "https://www.ssb.no/en/statbank/table/09189", my["updated"]),
                        src("Statistics Norway, employment and hours worked by industry", "09174", "https://www.ssb.no/en/statbank/table/09174", ml["updated"]),
                        src("Statistics Norway, fixed capital by industry", "09181", "https://www.ssb.no/en/statbank/table/09181", mk["updated"]),
                        src("Statistics Norway, production account and income generation by industry", "09170", "https://www.ssb.no/en/statbank/table/09170", mn["updated"])]}

# --------------------------------------------------------------------------- labor market tightness

def quarterly_mean(periods: list[str], values: list) -> dict[str, float]:
    """Monthly 'YYYY-MM' series to quarterly means 'YYYY-Qn', only for quarters with all three months."""
    acc = {}
    for p, v in zip(periods, values):
        if v is None:
            continue
        y, m = p.split("-")
        acc.setdefault(f"{y}-Q{(int(m) - 1) // 3 + 1}", []).append(v)
    return {q: sum(v) / 3 for q, v in acc.items() if len(v) == 3}


def ind_tightness() -> dict:
    pv, sv, mv = ssb_series(ssb_data("11587", [("NACE2007", ["01-96"]), ("ContentsCode", ["LedigeStillinger", "LedigeStillingerPros"]), ("Tid", ["from(2010K1)"])]))
    pq, sq, mq = lfs_quarterly()
    nav, nav_source = nav_registered()
    vac = dict(zip(pv, sv[("01-96", "LedigeStillinger")]))
    vrate = dict(zip(pv, sv[("01-96", "LedigeStillingerPros")]))
    u_lfs = {p: (v * 1000 if v is not None else None) for p, v in zip(pq, sq[("0", "15-74", "S", "Arbeidsledige")])}
    urate = dict(zip(pq, sq[("0", "15-74", "S", "ArbledProsArbstyrk")]))
    u_nav = quarterly_mean(nav["nav_count"]["periods"], nav["nav_count"]["values"])
    urate_nav = quarterly_mean(nav["nav_unemployment"]["periods"], nav["nav_unemployment"]["values"])

    def ratio(den):
        per = [p for p in pv if vac.get(p) is not None and den.get(p)]
        return per, [round(vac[p] / den[p], 3) for p in per]

    def scatter(rate, title, xlabel, note):
        pts = [{"p": p, "x": round(rate[p], 2), "y": vrate[p]} for p in pv if vrate.get(p) is not None and rate.get(p) is not None]
        return {"title": title, "subtitle": note, "x": xlabel, "y": "Vacancy rate, percent of jobs", "points": pts}

    nav_q = sorted(u_nav)
    series = {
        "vu_lfs": S("Vacancies per unemployed person, Labor Force Survey", "vacancies per unemployed person", "Q", *ratio(u_lfs), 2, derived=True,
                    note="Job vacancies (SSB 11587, seasonally adjusted) divided by unemployed persons in the Labor Force Survey (SSB 14483, seasonally adjusted)."),
        "vu_nav": S("Vacancies per registered unemployed person, NAV", "vacancies per unemployed person", "Q", *ratio(u_nav), 2, derived=True,
                    note="Job vacancies (SSB 11587, seasonally adjusted) divided by the quarterly average of NAV's seasonally adjusted number of registered fully unemployed."),
        "vacancies": S("Job vacancies (seasonally adjusted)", "vacancies", "Q", pv, sv[("01-96", "LedigeStillinger")], 0),
        "vacancy_rate": S("Job vacancy rate (seasonally adjusted)", "percent of jobs", "Q", pv, sv[("01-96", "LedigeStillingerPros")], 1),
        "unemployed_lfs": S("Unemployed persons, Labor Force Survey (seasonally adjusted)", "persons", "Q", [p for p in pq if u_lfs.get(p) is not None], [u_lfs[p] for p in pq if u_lfs.get(p) is not None], 0),
        "unemployed_nav": S("Registered fully unemployed, NAV (seasonally adjusted, quarterly average)", "persons", "Q", nav_q, [round(u_nav[q]) for q in nav_q], 0, derived=True,
                            note="Average of the three months in the quarter."),
    }
    scatters = [
        scatter(urate, "Beveridge curve, Labor Force Survey unemployment", "Unemployment rate (Labor Force Survey), percent of the labor force",
                "Each point is a quarter, joined in time order, with darker points for later quarters. Moves up and to the left mean a tighter labor market. "
                "A shift of the whole curve outward means vacancies are filled less easily at a given unemployment rate."),
        scatter(urate_nav, "Beveridge curve, registered unemployment", "Registered unemployment rate (NAV), percent of the labor force",
                "The same vacancy rate against NAV's registered unemployment, as a quarterly average of the monthly seasonally adjusted rate. "
                "The spike in 2020 is the furloughs during the pandemic."),
    ]
    return {"series": series, "scatters": scatters,
            "sources": [src("Statistics Norway, job vacancies", "11587", "https://www.ssb.no/en/statbank/table/11587", mv["updated"],
                            "A survey of the stock of vacancies, quarterly from 2010. Vacancies are rounded to the nearest 100."),
                        src("Statistics Norway, Labor Force Survey, quarterly", "14483", "https://www.ssb.no/en/statbank/table/14483", mq["updated"]),
                        nav_source]}


# --------------------------------------------------------------------------- consumption, investment and credit

QNA_DEMAND = ["koh.nr61_", "koh.nr61vare", "koh.nr61tjen", "koh.nr61L8", "koo.nroff",
              "bif.nr83_6fn", "bif.nr83_6", "bif.nr83naer", "bif.nr8368", "bif.nr84_5", "bif.nr83oljroer"]
_QNA_DEMAND = None


def demand_series(items: list[tuple[str, str, str]]) -> tuple[dict, dict]:
    """Quarter-on-quarter growth, growth from a year earlier and the level for demand components of the quarterly national accounts (SSB 09190)."""
    global _QNA_DEMAND
    if _QNA_DEMAND is None:
        _QNA_DEMAND = ssb_series(ssb_data("09190", [("Makrost", QNA_DEMAND), ("ContentsCode", ["FastePriserSesJust", "VolumEndrSesJust", "Volum"]), ("Tid", ["from(1995K1)"])]))
    per, s, m = _QNA_DEMAND
    series = {}
    for key, code, lab in items:
        series[key + "_qoq"] = S(f"{lab}, quarter-on-quarter volume growth (seasonally adjusted)", "percent", "Q", per, s[(code, "VolumEndrSesJust")], 1)
    for key, code, lab in items:
        series[key + "_yoy"] = S(f"{lab}, volume growth from same quarter a year earlier", "percent", "Q", per, s[(code, "Volum")], 1)
    for key, code, lab in items:
        series[key + "_level"] = S(f"{lab}, volume per quarter (constant 2023 prices, seasonally adjusted)", "NOK million", "Q", per, s[(code, "FastePriserSesJust")], 0)
    return series, src("Statistics Norway, quarterly national accounts", "09190", "https://www.ssb.no/en/statbank/table/09190", m["updated"], "Figures after the base year are preliminary.")


def ind_consumption() -> dict:
    series, source = demand_series([("households", "koh.nr61_", "Household consumption"), ("goods", "koh.nr61vare", "Household consumption of goods"),
                                    ("services", "koh.nr61tjen", "Household consumption of services"), ("abroad", "koh.nr61L8", "Household purchases abroad"),
                                    ("government", "koo.nroff", "General government consumption")])
    return {"series": series, "sources": [source]}


def ind_investment() -> dict:
    series, source = demand_series([("mainland", "bif.nr83_6fn", "Investment, Mainland Norway"), ("total", "bif.nr83_6", "Investment, total"),
                                    ("business", "bif.nr83naer", "Business investment, Mainland Norway"), ("housing", "bif.nr8368", "Housing investment"),
                                    ("government", "bif.nr84_5", "General government investment"), ("petroleum", "bif.nr83oljroer", "Petroleum investment")])
    return {"series": series, "sources": [source]}


def ind_credit() -> dict:
    per, s, m = ssb_series(ssb_data("11599", [("Valuta", ["00"]), ("Lantaker2", ["Kred01", "Kred02", "Kred03", "Kred04"]),
                                               ("ContentsCode", ["AarsTrans2", "Bruttogjeld"]), ("Tid", ["from(1990M01)"])]))
    pq, sq, mq = ssb_series(ssb_data("09477", [("Justering", ["S"]), ("ContentsCode", ["Gjeldsrate"]), ("Tid", ["from(1996K4)"])]))
    sectors = (("households", "Kred04", "households"), ("corporations", "Kred03", "non-financial corporations"),
               ("municipalities", "Kred02", "municipalities"), ("total", "Kred01", "all three sectors (C2)"))
    series = {}
    for key, code, lab in sectors:
        series["growth_" + key] = S(f"Domestic loan debt, {lab}, 12-month growth", "percent", "M", per, s[("00", code, "AarsTrans2")], 1)
    for key, code, lab in sectors:
        series["debt_" + key] = S(f"Domestic loan debt, {lab}", "NOK million", "M", per, s[("00", code, "Bruttogjeld")], 0)
    series["debt_income"] = S("Household debt as a share of disposable income (seasonally adjusted)", "percent of disposable income", "Q", pq, sq[("S", "Gjeldsrate")], 1)
    return {"series": series,
            "sources": [src("Statistics Norway, credit indicator (domestic loan debt)", "11599", "https://www.ssb.no/en/statbank/table/11599", m["updated"],
                            "Growth rates are adjusted for exchange rate changes, statistical breaks and, from 2019, confirmed losses."),
                        src("Statistics Norway, financial accounts, household ratios", "09477", "https://www.ssb.no/en/statbank/table/09477", mq["updated"])]}


# --------------------------------------------------------------------------- inequality

def owid_series(slug: str, country: str = "Norway") -> tuple[list[str], list[float]]:
    """One country's series from an Our World in Data chart (CSV download of the full chart data)."""
    url = f"https://ourworldindata.org/grapher/{slug}.csv?csvType=full&useColumnShortNames=true"
    rows = list(csv.DictReader(io.StringIO(http_get(url, headers={"User-Agent": "Mozilla/5.0"}).decode("utf-8"))))
    if not rows:
        raise RuntimeError(f"Our World in Data: no rows for {slug}")
    col = next(c for c in rows[0] if c not in ("entity", "code", "year"))
    pts = sorted((int(r["year"]), float(r[col])) for r in rows if r["entity"] == country and r[col] not in ("", None))
    if not pts:
        raise RuntimeError(f"Our World in Data: no {country} rows for {slug}")
    return [str(y) for y, _ in pts], [round(v, 2) for _, v in pts]


def ind_inequality(prev: dict | None = None) -> dict:
    pi, si, mi = ssb_series(ssb_data("07756", [("Forbruksenhet2", ["01", "99"]), ("ContentsCode", ["Ginikoeffisient", "P90P10", "S80S20"]), ("Tid", ["from(1986)"])]))
    pw, sw, mw = ssb_series(ssb_data("10318", [("Desiler", ["01", "02", "03", "04", "05", "10", "05b", "01b", "01c"]), ("ContentsCode", ["Nettoformue"]), ("Tid", ["from(2010)"])]))
    low = [[sw[(d, "Nettoformue")][i] for d in ("01", "02", "03", "04", "05")] for i in range(len(pw))]
    bottom50 = [None if None in row else round(sum(row), 1) for row in low]
    series = {
        "gini": S("Gini coefficient, income after tax per consumption unit (excluding student households)", "Gini coefficient", "A", pi, si[("99", "Ginikoeffisient")], 3),
        "gini_all": S("Gini coefficient, income after tax per consumption unit (whole population)", "Gini coefficient", "A", pi, si[("01", "Ginikoeffisient")], 3),
        "p90p10": S("P90/P10: income at the 90th percentile relative to the 10th", "ratio", "A", pi, si[("99", "P90P10")], 1),
        "s80s20": S("S80/S20: income share of the top fifth relative to the bottom fifth", "ratio", "A", pi, si[("99", "S80S20")], 1),
        "wealth_top10": S("Share of net wealth, top 10 percent of households", "percent of net wealth", "A", pw, sw[("10", "Nettoformue")], 1),
        "wealth_top5": S("Share of net wealth, top 5 percent of households", "percent of net wealth", "A", pw, sw[("05b", "Nettoformue")], 1),
        "wealth_top1": S("Share of net wealth, top 1 percent of households", "percent of net wealth", "A", pw, sw[("01b", "Nettoformue")], 1),
        "wealth_top01": S("Share of net wealth, top 0.1 percent of households", "percent of net wealth", "A", pw, sw[("01c", "Nettoformue")], 1),
        "wealth_bottom50": S("Share of net wealth, bottom half of households", "percent of net wealth", "A", pw, bottom50, 1, derived=True,
                             note="Sum of the shares of deciles 1 to 5 (SSB 10318)."),
    }
    sources = [src("Statistics Norway, measures of income dispersion", "07756", "https://www.ssb.no/en/statbank/table/07756", mi["updated"],
                   "Household income after tax per consumption unit (EU scale), distributed between persons. Negative amounts are set to zero."),
               src("Statistics Norway, share of net wealth by decile", "10318", "https://www.ssb.no/en/statbank/table/10318", mw["updated"],
                   "Estimated net wealth of households, with homes at estimated market value. Students are not included.")]
    wid = {"wid_income_top1": ("income-share-top-1-before-tax-wid", "Share of pre-tax income, top 1 percent of adults (World Inequality Database)", "percent of pre-tax income"),
           "wid_income_top10": ("income-share-top-10-before-tax-wid", "Share of pre-tax income, top 10 percent of adults (World Inequality Database)", "percent of pre-tax income"),
           "wid_wealth_top1": ("wealth-share-richest-1-percent", "Share of net wealth, top 1 percent of adults (World Inequality Database)", "percent of net wealth"),
           "wid_wealth_top10": ("wealth-share-richest-10-percent", "Share of net wealth, top 10 percent of adults (World Inequality Database)", "percent of net wealth")}
    wid_source = src("World Inequality Database, processed by Our World in Data", None, "https://ourworldindata.org/economic-inequality", None,
                     "Top shares among adults, before tax for income. The series have gaps in early years and are not directly comparable with Statistics Norway's household figures.")
    try:
        for key, (slug, label, unit) in wid.items():
            series[key] = S(label, unit, "A", *owid_series(slug), 1)
        sources.append(wid_source)
    except Exception as e:   # these change once a year: keep what was fetched last time
        print(f"       inequality: World Inequality Database series kept from the previous run ({type(e).__name__}: {e})")
        for key in wid:
            if prev and key in prev.get("series", {}):
                series[key] = prev["series"][key]
        if any(k in series for k in wid):
            sources.append(wid_source)
    return {"series": series, "sources": sources}


# --------------------------------------------------------------------------- government budget (Nasjonalbudsjettet, regjeringen.no)

BUDGET_LABELS = {   # table labels on regjeringen.no -> English
    "Arbeidsgiveravgift og trygdeavgift": "Employers' and employees' social security contributions",
    "Skatt på formue og inntekt": "Tax on income and wealth",
    "Merverdiavgift": "Value added tax",
    "Avgifter på tobakk og alkohol": "Excise duties on tobacco and alcohol",
    "Avgifter på motorvogner": "Motor vehicle taxes",
    "Elektrisitetsavgift": "Electricity tax",
    "Tollinntekter": "Customs duties",
    "Andre avgifter": "Other excise duties",
    "Renter og aksjeutbytte mv.": "Interest and dividends",
    "Inntekter fra statens forvaltningsbedrifter": "Government business operations",
    "Andre inntekter": "Other income",
    "Alderspensjon": "Old-age pensions",
    "Uføretrygd": "Disability benefit",
    "Sykepenger": "Sickness benefit",
    "Arbeidsavklaringspenger": "Work assessment allowance",
    "Øvrige sosiale formål": "Other social benefits",
    "Helsetjenester": "Health services under National Insurance",
    "Foreldrepenger mv.": "Parental benefit",
    "Arbeidsliv": "Unemployment and labor market benefits",
    "Rammetilskudd til kommuner og fylkeskommuner": "Block grants to municipalities and counties",
    "Regionale helseforetak": "Hospitals (regional health authorities)",
    "Forsvar": "Defense",
    "Samferdsel": "Transport",
    "Høyere utdanning, forskning og fagskoler": "Higher education and research",
    "Bistand": "Development aid",
    "Politi og påtalemyndighet": "Police and prosecution",
    "Jordbruksavtalen": "Agricultural agreement",
    "Barnetrygd og kontantstøtte": "Child benefit and cash-for-care benefit",
    "Renter på statsgjeld": "Interest on government debt",
    "Andre utgifter": "Other expenditure",
    "Sum skatter og avgifter fra Fastlands-Norge": "Taxes from Mainland Norway",
    "Sum folketrygden": "National Insurance",
}


def _html_tables(page: str) -> list[list[list[str]]]:
    import html as _html
    out = []
    for t in re.findall(r"<table.*?</table>", page, re.S):
        rows = []
        for r in re.findall(r"<tr.*?</tr>", t, re.S):
            rows.append([re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", "", c))).strip() for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, re.S)])
        out.append(rows)
    return out


def _no_number(text: str) -> float | None:
    t = text.replace(" ", "").replace(" ", "").replace("−", "-").replace("–", "-").replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def budget_breakdowns(page_html: str, year: int) -> tuple[list[dict], dict]:
    """Revenue and expenditure of the state budget proposal (excluding petroleum), from the table on regjeringen.no.
    The table has three amount columns: items, subtotals and section totals."""
    table = next((t for t in _html_tables(page_html) if any(r and r[0].startswith("Statsbudsjettets inntekter") for r in t)), None)
    if table is None:
        raise RuntimeError("budget page: no revenue and expenditure table")
    rows = [(re.sub(r"\d+$", "", r[0]).strip(), [_no_number(c) for c in r[1:4]]) for r in table if len(r) >= 4]
    sections, cur, group = {}, None, None
    for i, (label, (a, b, c)) in enumerate(rows):
        if c is not None:                                   # section total
            cur, group = label, None
            sections[cur] = {"total": c, "items": []}
        elif cur is None:
            continue
        elif a is not None:                                 # item inside a subtotal
            sections[cur]["items"].append({"label": BUDGET_LABELS.get(label, label), "value": a, "group": group})
        elif b is not None:
            nxt = rows[i + 1][1] if i + 1 < len(rows) else (None, None, None)
            if nxt[0] is not None:                          # a subtotal: its items follow
                group = BUDGET_LABELS.get(label, label)
            else:
                group = None
                sections[cur]["items"].append({"label": BUDGET_LABELS.get(label, label), "value": b, "group": None})
    rev = next((v for k, v in sections.items() if k.startswith("Statsbudsjettets inntekter")), None)
    exp = next((v for k, v in sections.items() if k.startswith("Statsbudsjettets utgifter")), None)
    bal = next((v["total"] for k, v in sections.items() if k.startswith("Oljekorrigert")), None)
    for name, sec in (("revenue", rev), ("expenditure", exp)):
        if not sec or not sec["items"] or abs(sum(x["value"] for x in sec["items"]) - sec["total"]) > 2:
            raise RuntimeError(f"budget page: {name} items do not add up to the total")
    out = [
        {"key": "spending", "title": f"What the state spends on, {year}", "unit": "NOK billion", "total": exp["total"],
         "subtitle": f"State budget proposal for {year}, expenditure excluding petroleum activities: NOK {exp['total']:,.1f} billion.",
         "items": sorted(exp["items"], key=lambda x: -x["value"])},
        {"key": "revenue", "title": f"Where the income comes from, {year}", "unit": "NOK billion", "total": rev["total"],
         "subtitle": (f"State budget proposal for {year}, revenue excluding petroleum activities: NOK {rev['total']:,.1f} billion. "
                      + (f"The gap to expenditure, NOK {abs(bal):,.1f} billion, is the oil-corrected deficit, covered by a transfer from the oil fund." if bal is not None else "")),
         "items": sorted(rev["items"], key=lambda x: -x["value"])},
    ]
    return out, {"expenditure": exp["total"], "revenue": rev["total"], "oil_corrected_balance": bal}


def ind_budget(fund: dict | None = None) -> dict:
    """Fiscal policy from the latest Nasjonalbudsjett: the published data behind the figures (Excel) and the state budget's
    revenue and expenditure table. No API: the pages are found from the budget's hub page for the newest year available."""
    base = "https://www.regjeringen.no"
    ua = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
          "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "Accept-Language": "nb-NO,nb;q=0.9,en;q=0.8"}
    this_year = datetime.now(timezone.utc).year
    hub = year = None
    tried = []
    for y in (this_year + 1, this_year, this_year - 1):
        try:
            hub = http_get(f"{base}/no/statsbudsjett/{y}/", headers=ua).decode("utf-8", "ignore")
        except Exception as e:
            tried.append(f"{y}: {type(e).__name__} {e}")
            continue
        if "tallene-bak-figurene" in hub:
            year = y
            break
        tried.append(f"{y}: page without data links ({len(hub)} bytes)")
    if year is None:
        raise RuntimeError("no budget hub page with data links found on regjeringen.no (" + ", ".join(tried) + ")")

    def link(pattern):
        m = re.search(r'href="(/no/statsbudsjett/%d/%s[^"]*)"' % (year, pattern), hub)
        if not m:
            raise RuntimeError(f"budget {year}: no link matching {pattern}")
        return base + m.group(1)

    fig_url, bud_url = link("nasjonalbudsjettet-%d-tallene-bak-figurene" % year), link("statsbudsjettet-%d-statens-inntekter-og-utgifter" % year)
    breakdowns, totals = budget_breakdowns(http_get(bud_url, headers=ua).decode("utf-8", "ignore"), year)

    # Index every figure in the workbooks by its title, because figure numbers change from year to year
    figs = []
    for href in sorted(set(re.findall(r'href="(/contentassets/[^"]*\.xlsx)"', http_get(fig_url, headers=ua).decode("utf-8", "ignore")))):
        wb = xlsx_sheets(http_get(base + href, headers=ua))
        index = next((rows for name, rows in wb.items() if name.strip().lower() == "innhold"), [])
        titles = {str(r.get("A")).strip(): str(r.get("B")) for r in index if r.get("A") and r.get("B")}
        for rows in wb.values():
            fid = str(rows[0].get("A")).strip() if rows and rows[0].get("A") else None
            if fid in titles:
                figs.append((titles[fid], rows))

    def figure(pattern):
        for title, rows in figs:
            if re.search(pattern, title, re.I):
                return rows
        raise RuntimeError(f"budget {year}: no figure titled like {pattern!r}")

    def column(rows, pattern, sign=1.0):
        """Year series from the columns whose header matches: the outturn column where it has a value, otherwise the estimate column."""
        hdr = {k: str(v) for k, v in rows[1].items() if v}
        cols = [k for k, v in hdr.items() if k != "A" and re.search(pattern, v, re.I) and not re.search("gjennomsnitt", v, re.I)]
        cols.sort(key=lambda k: "anslag" in hdr[k].lower())
        if not cols:
            raise RuntimeError(f"budget {year}: no column like {pattern!r} in {list(hdr.values())}")
        per, val = [], []
        for r in rows[2:]:
            if not isinstance(r.get("A"), float):
                continue
            v = next((r[k] for k in cols if isinstance(r.get(k), float)), None)
            if v is not None:
                per.append(str(int(r["A"])))
                val.append(round(sign * v, 2))
        return per, val

    est = f"The latest years are estimates and {year} is the budget proposal."
    f_gdp = figure(r"^Strukturelt oljekorrigert budsjettunderskudd\. Prosent av trend-BNP for Fastlands-Norge")
    f_imp = figure(r"Endring fra året før \(budsjettindikator\)")
    f_fund = figure(r"^Strukturelt oljekorrigert budsjettunderskudd\. Prosent av Statens pensjonsfond utland")
    f_share = figure(r"^Bruken av fondsmidler som andel av utgifter i statsbudsjettet")
    f_bal = figure(r"^Oljekorrigert og strukturell oljekorrigert budsjettbalanse\. Prosent av trend-BNP")
    f_pub = figure(r"^Utgifter og skatte- og avgiftsinntekter i offentlig forvaltning\. Prosent av BNP for Fastlands-Norge")
    series = {
        "deficit_gdp": S("Structural non-oil deficit", "percent of trend GDP for Mainland Norway", "A", *column(f_gdp, r"^SOBU"), 1, note=est),
        "oil_corrected_gdp": S("Oil-corrected deficit (not cyclically adjusted)", "percent of trend GDP for Mainland Norway", "A", *column(f_bal, r"^Oljekorrigert budsjettbalanse", -1.0), 1),
        "impulse": S("Change in the structural non-oil deficit (budget indicator)", "percentage points of trend GDP", "A", *column(f_imp, r"^Budsjettimpulser"), 1),
        "fund_pct": S("Structural non-oil deficit as a share of the oil fund (fund spending)", "percent of the fund", "A", *column(f_fund, r"^Uttaksprosent"), 1),
        "expected_return": S("Expected real return on the fund (the fiscal rule)", "percent of the fund", "A", *column(f_fund, r"^Forventet realavkastning"), 1),
        "fund_share_budget": S("Fund spending as a share of state budget expenditure", "percent of expenditure", "A", *column(f_share, r"^Bruk av fondsmidler"), 1),
        "public_expenditure": S("General government expenditure", "percent of GDP for Mainland Norway", "A", *column(f_pub, r"^Offentlige utgifter"), 1),
        "tax_revenue": S("General government tax revenue", "percent of GDP for Mainland Norway", "A", *column(f_pub, r"^Skatte- og avgiftsinntekter"), 1),
    }
    # Actual use of oil money: the oil-corrected deficit in kroner over the fund's value at the start of the year.
    # Kroner amounts are the published shares of trend GDP times trend GDP for Mainland Norway (same workbook).
    f_trend = figure(r"^BNP Fastlands-Norge\. Mrd\. kroner\. Løpende priser")
    trend = dict(zip(*column(f_trend, r"^Trend$")))
    oc = dict(zip(series["oil_corrected_gdp"]["periods"], series["oil_corrected_gdp"]["values"]))
    sobu = dict(zip(series["deficit_gdp"]["periods"], series["deficit_gdp"]["values"]))
    uttak = dict(zip(series["fund_pct"]["periods"], series["fund_pct"]["values"]))
    value = dict(zip(fund["series"]["value"]["periods"], fund["series"]["value"]["values"])) if fund else {}
    ap, av, kp, k_oc, k_st = [], [], [], [], []
    for y in sorted(oc):
        if y in trend:
            kp.append(y)
            k_oc.append(round(oc[y] / 100 * trend[y], 1))
            k_st.append(round(sobu[y] / 100 * trend[y], 1) if y in sobu else None)
        opening = value.get(str(int(y) - 1)) if int(y) - 1 < this_year else None    # NBIM's year-end value; the current year is not a year-end
        if y in trend and opening:
            ap.append(y)
            av.append(round(100 * (oc[y] / 100 * trend[y]) / opening, 2))
        elif sobu.get(y) and y in uttak:                                            # opening value not known yet: scale the structural figure
            ap.append(y)
            av.append(round(uttak[y] * oc[y] / sobu[y], 2))
    series["fund_pct_actual"] = S("Oil-corrected deficit as a share of the oil fund (actual use of oil money)", "percent of the fund", "A", ap, av, 1, derived=True,
                                  note=("The oil-corrected deficit in kroner divided by the fund's market value at the start of the year (NBIM). The deficit in kroner is its "
                                        "published share of trend GDP times trend GDP for Mainland Norway. It is not adjusted for the business cycle. For a year where the opening "
                                        "fund value is not known yet, the structural figure is scaled by the ratio of the two deficits."))
    series["oil_corrected_nok"] = S("Oil-corrected deficit", "NOK billion", "A", kp, k_oc, 0, derived=True,
                                    note="Published share of trend GDP times trend GDP for Mainland Norway, both from Nasjonalbudsjettet.")
    series["deficit_nok"] = S("Structural non-oil deficit", "NOK billion", "A", kp, k_st, 0, derived=True,
                              note="Published share of trend GDP times trend GDP for Mainland Norway, both from Nasjonalbudsjettet.")
    return {"series": series, "breakdowns": breakdowns, "budget_year": year, "totals": totals,
            "sources": [src(f"Ministry of Finance, Nasjonalbudsjettet {year}, data behind the figures", None, fig_url, None, est),
                        src(f"Ministry of Finance, state budget {year}, revenue and expenditure", None, bud_url, None,
                            "The government's proposal, before the Storting's budget settlement.")]}


# --------------------------------------------------------------------------- comparisons with other countries
# Two blocks of seven tiles each: Norway against the Nordic countries, and against Europe and the US. The figures come from
# sources that publish all the countries on the same definitions: Eurostat, the BIS (policy rates) and the OECD (bond yields).
# The exception is Mainland Norway, which only Statistics Norway publishes.

CMP_COUNTRIES = {   # our key: (Eurostat code, BIS code, OECD code, name)
    "no": ("NO", "NO", "NOR", "Norway"), "se": ("SE", "SE", "SWE", "Sweden"), "dk": ("DK", "DK", "DNK", "Denmark"),
    "fi": ("FI", None, "FIN", "Finland"), "is": ("IS", "IS", "ISL", "Iceland"), "ea": ("EA", "XM", None, "Euro area"),
    "de": ("DE", None, "DEU", "Germany"), "fr": ("FR", None, "FRA", "France"), "nl": ("NL", None, "NLD", "Netherlands"),
    "it": ("IT", None, "ITA", "Italy"), "es": ("ES", None, "ESP", "Spain"), "ch": ("CH", "CH", "CHE", "Switzerland"),
    "gb": (None, "GB", "GBR", "United Kingdom"), "us": ("US", "US", "USA", "United States"),
}
CMP_BLOCKS = {"nordic": ["no", "se", "dk", "fi", "is"], "europe": ["no", "us", "ea", "de", "fr", "nl", "it", "es"]}
# Policy rates: series key -> whose rate it is (Finland has had the euro area's rate since 1999). Eurostat has no United Kingdom
# data after 2020, so the United Kingdom appears only where the BIS and the OECD are the sources. A block holds at most eight
# countries, one per color in the chart palette. Eurostat has the United States only for unemployment: see us_series.
CMP_POLICY = {"nordic": {"no": "no", "se": "se", "dk": "dk", "fi": "ea", "is": "is"}, "europe": {"no": "no", "us": "us", "ea": "ea", "gb": "gb", "ch": "ch"}}
CMP_YIELDS = {"nordic": ["no", "se", "dk", "fi", "is"], "europe": ["no", "us", "de", "fr", "it", "gb", "ch"]}
EA_CODES = ["EA", "EA21", "EA20"]   # Eurostat's euro area code differs between datasets: the one with the latest data is used
CMP_EUROSTAT = {
    "gdp": ("namq_10_gdp", dict(na_item="B1GQ", s_adj="SCA", unit=["CLV_I20", "CLV_PCH_PRE", "CLV_PCH_SM"], sinceTimePeriod="1995-Q1")),
    "hicp": ("prc_hicp_minr", dict(coicop18=["TOTAL", "TOT_X_NRG_FOOD"], unit="RCH_A", sinceTimePeriod="2000-01")),
    "une": ("une_rt_m", dict(s_adj="SA", age=["TOTAL", "Y_LT25"], unit="PC_ACT", sex="T", sinceTimePeriod="2000-01")),
    "emp": ("lfsi_emp_q", dict(indic_em="EMP_LFS", s_adj="SA", age=["Y20-64", "Y15-64"], unit="PC_POP", sex="T", sinceTimePeriod="2000-Q1")),
    # Inputs to the productivity calculation (national accounts, national currency): value added, hours, capital and income
    "va": ("nama_10_a10", dict(na_item="B1G", unit="CLV20_MNAC", nace_r2="TOTAL", sinceTimePeriod="1990")),
    "hours": ("nama_10_a10_e", dict(na_item="EMP_DC", unit="THS_HW", nace_r2="TOTAL", sinceTimePeriod="1990")),
    "capital": ("nama_10_nfa_st", dict(asset10="N11N", unit="CLV20_MNAC", nace_r2="TOTAL", sinceTimePeriod="1990")),
    "income": ("nama_10_gdp", dict(na_item=["D1", "B2A3G"], unit="CP_MNAC", sinceTimePeriod="1990")),
    # Inputs to the wage calculation: wages and salaries, hours worked by employees, and the annual average change in the HICP
    "wagebill": ("nama_10_a10", dict(na_item="D11", unit="CP_MNAC", nace_r2="TOTAL", sinceTimePeriod="1994")),
    "employee_hours": ("nama_10_a10_e", dict(na_item="SAL_DC", unit="THS_HW", nace_r2="TOTAL", sinceTimePeriod="1994")),
    "hicp_annual": ("prc_hicp_ainr", dict(coicop18="TOTAL", unit="RCH_A_AVG", sinceTimePeriod="1995")),
}
_CMP: dict = {}


def trim(periods: list, values: list) -> tuple[list, list]:
    """Drop the empty observations at both ends of a series."""
    idx = [i for i, v in enumerate(values) if v is not None]
    return ([], []) if not idx else (list(periods[idx[0]:idx[-1] + 1]), list(values[idx[0]:idx[-1] + 1]))


def cmp_eurostat(name: str) -> tuple[list[str], dict[tuple, list], str]:
    """One Eurostat call per dataset for all the comparison countries, cached for the run."""
    if name not in _CMP:
        dataset, params = CMP_EUROSTAT[name]
        geos = sorted({c[0] for c in CMP_COUNTRIES.values() if c[0] and c[0] != "EA"}) + EA_CODES
        _CMP[name] = eurostat(dataset, geo=geos, **params)
        time.sleep(0.3)
    return _CMP[name]


def cmp_values(name: str, key: str, *codes: str, geo: str | None = None) -> tuple[list[str], list]:
    """(periods, values) for one country (our key) and the given dimension codes. The country is the last dimension before time.
    geo names the Eurostat code to use instead of the country's own (to hold the euro area's composition fixed across datasets)."""
    times, out, _ = cmp_eurostat(name)
    geo = geo or CMP_COUNTRIES[key][0]
    best = None
    for g in (EA_CODES if geo == "EA" else [geo]):
        hits = [v for k, v in out.items() if k[-1] == g and all(c in k[:-1] for c in codes)]
        if len(hits) != 1:
            continue
        p, v = trim(times, hits[0])
        if p and (best is None or p[-1] > best[0][-1]):
            best = (p, v)
    if best is None:
        raise KeyError(f"{CMP_EUROSTAT[name][0]}: no data for {geo} {codes}")
    return best


def cmp_src(name: str, title: str, note: str | None = None) -> dict:
    dataset = CMP_EUROSTAT[name][0]
    return src(f"Eurostat, {title}", dataset, f"https://ec.europa.eu/eurostat/databrowser/view/{dataset}/default/table", cmp_eurostat(name)[2], note)


def _sdmx_csv(url: str, by: str = "REF_AREA") -> dict[str, dict[str, float]]:
    """{area: {period: value}} from an SDMX csv response with TIME_PERIOD and OBS_VALUE columns (by: the column to split on)."""
    raw = http_get(url, {"Accept": "text/csv"}).decode("utf-8-sig", "replace")
    out: dict[str, dict[str, float]] = {}
    for r in csv.DictReader(io.StringIO(raw)):
        try:
            v = float(r["OBS_VALUE"])
        except (TypeError, ValueError):
            continue
        if not math.isnan(v):
            out.setdefault(r[by], {})[r["TIME_PERIOD"]] = v
    time.sleep(0.3)
    return out


def cmp_policy_rates() -> dict[str, tuple[list, list]]:
    """Central bank policy rates from the BIS, daily, as step series: the first day, every day the rate changed and the last day.
    The page fills in the days in between (series flag `steps`), which keeps 27 years of daily rates for ten series small.
    The BIS publishes some central banks for every calendar day and others for business days only, and the rate holds on the
    days in between. A hole of more than ten days is kept as a hole. All series end on the latest day the BIS has for every
    central bank, so the tiles compare the same day (a central bank more than a week behind the others keeps its own last day)."""
    if "bis" not in _CMP:
        areas = "+".join(sorted({c[1] for c in CMP_COUNTRIES.values() if c[1]}))
        daily = _sdmx_csv("https://stats.bis.org/api/v2/data/dataflow/BIS/WS_CBPOL/1.0/D." + areas + "?format=csv&startPeriod=2000-01-01")
        day = datetime.fromisoformat
        newest = max(max(d) for d in daily.values())
        end = min(max(d) for d in daily.values() if (day(newest) - day(max(d))).days <= 7)
        out = {}
        for area, d in daily.items():
            ds = [p for p in sorted(d) if p <= end]
            ps, vs = [ds[0]], [d[ds[0]]]
            for prev, p in zip(ds, ds[1:]):
                if (day(p) - day(prev)).days > 10:   # no figures for a longer stretch: leave it empty instead of carrying the rate forward
                    ps.append((day(prev) + timedelta(days=1)).strftime("%Y-%m-%d")); vs.append(None)
                if d[p] != vs[-1]:
                    ps.append(p); vs.append(d[p])
            if ps[-1] != ds[-1]:
                ps.append(ds[-1]); vs.append(d[ds[-1]])
            out[area] = (ps, vs)
        _CMP["bis"] = out
    return _CMP["bis"]


def cmp_bond_yields() -> dict[str, tuple[list, list]]:
    """Long-term (10-year) government bond yields from the OECD, monthly averages."""
    if "oecd" not in _CMP:
        areas = "+".join(sorted(c[2] for c in CMP_COUNTRIES.values() if c[2]))
        data = _sdmx_csv(f"https://sdmx.oecd.org/public/rest/data/OECD.SDD.STES,DSD_STES@DF_FINMARK,/{areas}.M.IRLT.PA.....?startPeriod=2000-01&format=csvfile")
        _CMP["oecd"] = {a: (sorted(m), [round(m[p], 2) for p in sorted(m)]) for a, m in data.items()}
    return _CMP["oecd"]


def cmp_name(key: str) -> str:
    return CMP_COUNTRIES[key][3]


# The United States. Eurostat has it only for unemployment, so the other tiles use the national statistics: real GDP and
# consumer prices through FRED's chart download (no key needed), the net capital stock from the Bureau of Economic Analysis'
# flat file, and hours, income components and the employment rate from the OECD (the employment rate through FRED's copy).

CMP_GDP_BASE = ("2019-Q4", "Q4 2019 = 100")
OECD_DATA = "https://sdmx.oecd.org/public/rest/data/"
BEA_FIXED_ASSETS = "https://apps.bea.gov/national/FixedAssets/Release/TXT/FixedAssets.txt"
BEA_NET_STOCK = "kcttotl1es00"   # Fixed Assets table 1.2, line 2: chain-type quantity index for the net stock of fixed assets, private and government


def fred(ids: list[str], start: str) -> dict[str, dict[str, float]]:
    """{series id: {date: value}} from FRED's chart download. Missing observations are left out.
    FRED does not always answer the build server. A request that gets no answer waits for its timeout, so the timeout is short
    and after one failure FRED is not asked again in the same run (the tiles then keep their previous United States series)."""
    if "fred_down" in _CMP:
        raise RuntimeError("FRED did not answer earlier in this run: " + _CMP["fred_down"])
    try:
        raw = http_get("https://fred.stlouisfed.org/graph/fredgraph.csv?id=" + ",".join(ids) + "&cosd=" + start, timeout=20).decode("utf-8-sig")
    except Exception as e:
        _CMP["fred_down"] = f"{type(e).__name__}: {e}"
        raise
    rows = list(csv.reader(io.StringIO(raw)))
    out: dict[str, dict[str, float]] = {i: {} for i in rows[0][1:]}
    for r in rows[1:]:
        for i, v in zip(rows[0][1:], r[1:]):
            try:
                out[i][r[0]] = float(v)
            except ValueError:
                pass
    missing = [i for i in ids if not out.get(i)]
    if missing:
        raise RuntimeError(f"FRED returned no data for {missing}")
    time.sleep(0.3)
    return out


def fred_periods(d: dict[str, float], freq: str) -> dict[str, float]:
    """FRED dates (first day of the period) to our period codes."""
    code = lambda x: x[:4] if freq == "A" else f"{x[:4]}-Q{(int(x[5:7]) - 1) // 3 + 1}" if freq == "Q" else x[:7]
    return {code(x): v for x, v in sorted(d.items())}


def change_over(d: dict[str, float], earlier) -> tuple[list[str], list]:
    """Percent change from the period that earlier(p) names. A missing period gives no value, so a hole in the data cannot shift the comparison."""
    ps = [q for q in sorted(d) if d.get(earlier(q))]
    return ps, [round((d[q] / d[earlier(q)] - 1) * 100, 2) for q in ps]


year_earlier = lambda q: str(int(q[:4]) - 1) + q[4:]
quarter_earlier = lambda q: f"{int(q[:4]) - 1}-Q4" if q.endswith("Q1") else f"{q[:4]}-Q{int(q[-1]) - 1}"


def bea_fixed_assets(code: str) -> dict[str, float]:
    """One annual series from the Bureau of Economic Analysis' fixed assets flat file (all series in one text file, read as a stream)."""
    out = {}
    req = urllib.request.Request(BEA_FIXED_ASSETS, headers=UA)
    with urllib.request.urlopen(req, timeout=40) as r:   # the timeout is per read, not for the whole file
        for line in r:
            if line.startswith(code.encode() + b","):
                _, year, value = next(csv.reader([line.decode("utf-8", "replace").strip()]))
                out[year] = float(value.replace(",", ""))
    if not out:
        raise RuntimeError(f"series {code} not found in the BEA fixed assets file")
    return out


def us_growth_accounting() -> tuple[dict, dict]:
    """Growth accounting for the United States with the same function as the other countries. Y is real GDP (the US accounts have no
    long series for total value added at basic prices), L total hours worked (OECD), K the quantity index for the net stock of fixed
    assets (BEA) and the labor share compensation of employees over compensation plus gross operating surplus and mixed income (OECD)."""
    Y = {y: v for y, v in fred_periods(fred(["GDPCA"], "1989-01-01")["GDPCA"], "A").items() if y >= "1990"}
    L = _sdmx_csv(OECD_DATA + "OECD.SDD.TPS,DSD_PDB@DF_PDB,/USA.A.HRSTO._T.H._Z.N._Z._Z?startPeriod=1990&format=csvfile")["USA"]
    K = {y: v for y, v in bea_fixed_assets(BEA_NET_STOCK).items() if y >= "1990"}
    inc = _sdmx_csv(OECD_DATA + "OECD.SDD.NAD,DSD_NAMAIN10@DF_TABLE1_INCOME,/A.USA.S1.S1.D1+B2A3G._Z._T._Z.XDC.V.N.T0103?startPeriod=1990&format=csvfile", by="TRANSACTION")
    W, O = inc["D1"], inc["B2A3G"]
    SH = {y: W[y] / (W[y] + O[y]) for y in sorted(W) if O.get(y) is not None and W[y] + O[y] > 0}
    return growth_accounting(Y, L, K, SH), SH


def us_series(kind: str) -> dict:
    """The United States series of one comparison tile, keyed like the other countries' series (suffix _us)."""
    if ("us", kind) in _CMP:
        return _CMP[("us", kind)]
    name, out = "United States", {}
    if kind == "gdp":
        base, base_txt = CMP_GDP_BASE
        d = fred_periods(fred(["GDPC1"], "1995-01-01")["GDPC1"], "Q")
        ps = sorted(d)
        out["index_us"] = S(f"GDP, {name}, volume index ({base_txt})", "index", "Q", ps, index_to([d[q] for q in ps], ps, base), 1)
        out["yoy_us"] = S(f"GDP, {name}, volume growth from a year earlier", "percent", "Q", *change_over(d, year_earlier), 1)
        out["qoq_us"] = S(f"GDP, {name}, quarter-on-quarter volume growth", "percent", "Q", *change_over(d, quarter_earlier), 1)
    elif kind == "inflation":
        f = {i: {x: v for x, v in d.items() if x >= "1999-01-01"} for i, d in fred(["CPIAUCNS", "CPILFENS"], "1999-01-01").items()}
        out["hicp_us"] = S(f"Consumer price index, {name} (national index, not harmonized), 12-month change", "percent", "M",
                           *change_over(fred_periods(f["CPIAUCNS"], "M"), year_earlier), 1)
        out["core_us"] = S(f"Consumer price index excluding food and energy, {name} (national index, not harmonized), 12-month change", "percent", "M",
                           *change_over(fred_periods(f["CPILFENS"], "M"), year_earlier), 1)
    elif kind == "employment":
        d = fred_periods(fred(["LREM64TTUSQ156S"], "2000-01-01")["LREM64TTUSQ156S"], "Q")
        out["rate1564_us"] = S(f"Employment rate, {name}, ages 15–64", "percent of population", "Q", sorted(d), [round(d[q], 1) for q in sorted(d)], 1)
    elif kind == "wages":
        f = fred(["A4401C0A052NBEA"], "1997-01-01")
        nominal = dict(zip(*change_over(fred_periods(f["A4401C0A052NBEA"], "A"), year_earlier)))
        months: dict[str, list] = {}
        for q, v in fred_periods(fred(["CPIAUCNS"], "1997-01-01")["CPIAUCNS"], "M").items():
            months.setdefault(q[:4], []).append(v)
        prices = dict(zip(*change_over({y: sum(v) / len(v) for y, v in months.items() if len(v) >= 10}, year_earlier)))
        out.update(wage_series("us", name, nominal, prices, "wages and salaries per full-time equivalent employee", "the national consumer price index"))
    elif kind == "productivity":
        out["_ga"] = us_growth_accounting()
    else:
        raise KeyError(kind)
    _CMP[("us", kind)] = out
    return out


def add_us(series: dict, prev: dict | None, kind: str, build=None) -> str | None:
    """Add the United States series of one tile. Their sources (FRED, BEA, OECD) are not the ones the rest of the tile uses, so if
    one of them fails the United States series from the previous run are kept and the reason is returned (None when all is well)."""
    try:
        series.update(build() if build else us_series(kind))
        return None
    except Exception as e:
        old = {k: v for k, v in ((prev or {}).get("series") or {}).items() if k.endswith("_us")}
        if not old:
            raise
        series.update(old)
        print(f"  kept United States series in {kind} from the previous run ({type(e).__name__}: {e})", file=sys.stderr)
        return f"{type(e).__name__}: {e}"


def with_us(result: dict, kept: str | None) -> dict:
    if kept:
        result["us_kept"] = kept
    return result


FRED_NOTE = "Downloaded from FRED, the data service of the Federal Reserve Bank of St. Louis."


def cmp_gdp(block: str, gdp: dict | None, prev: dict | None = None) -> dict:
    if not gdp:
        raise RuntimeError("needs the gdp indicator (Mainland Norway comes from Statistics Norway)")
    base, base_txt = CMP_GDP_BASE
    lv, qq = gdp["series"]["level"], gdp["series"]["qoq"]
    series = {
        "index_mainland": S(f"GDP, Mainland Norway, volume index ({base_txt})", "index", "Q", lv["periods"], index_to(lv["values"], lv["periods"], base), 1, derived=True,
                            note="Statistics Norway's seasonally adjusted volume series (table 09190), rebased."),
        "yoy_mainland": S("GDP, Mainland Norway, volume growth from a year earlier", "percent", "Q", *trim(lv["periods"], pct_change(lv["values"], 4)), 1, derived=True,
                          note=("Computed from Statistics Norway's seasonally adjusted volume series, to match Eurostat's seasonally adjusted figures for the other "
                                "countries. It can differ slightly from the unadjusted growth rate in the GDP tile at the top of the page.")),
        "qoq_mainland": S("GDP, Mainland Norway, quarter-on-quarter volume growth", "percent", "Q", qq["periods"], qq["values"], 1),
    }
    for key in CMP_BLOCKS[block]:
        if key == "us":
            continue
        name = "Norway, total economy" if key == "no" else cmp_name(key)
        p, v = cmp_values("gdp", key, "CLV_I20")
        series[f"index_{key}"] = S(f"GDP, {name}, volume index ({base_txt})", "index", "Q", p, index_to(v, p, base), 1)
        series[f"yoy_{key}"] = S(f"GDP, {name}, volume growth from a year earlier", "percent", "Q", *cmp_values("gdp", key, "CLV_PCH_SM"), 1)
        series[f"qoq_{key}"] = S(f"GDP, {name}, quarter-on-quarter volume growth", "percent", "Q", *cmp_values("gdp", key, "CLV_PCH_PRE"), 1)
    us = "us" in CMP_BLOCKS[block]
    kept = add_us(series, prev, "gdp") if us else None
    return with_us({"series": series,
            "sources": [cmp_src("gdp", "GDP and main components, quarterly", "Chain-linked volumes, seasonally and calendar adjusted. The index is rebased from 2020 = 100 to " + base_txt + "."),
                        src("Statistics Norway, quarterly national accounts (Mainland Norway)", "09190", "https://www.ssb.no/en/statbank/table/09190")]
                       + ([src("US Bureau of Economic Analysis, real GDP, seasonally adjusted (series GDPC1)", url="https://fred.stlouisfed.org/series/GDPC1", note=FRED_NOTE)] if us else [])}, kept)


def cmp_inflation(block: str, prev: dict | None = None) -> dict:
    series = {}
    for key in CMP_BLOCKS[block]:
        if key == "us":
            continue
        series[f"hicp_{key}"] = S(f"HICP, {cmp_name(key)}, 12-month change", "percent", "M", *cmp_values("hicp", key, "TOTAL"), 1)
        series[f"core_{key}"] = S(f"HICP excluding energy, food, alcohol and tobacco, {cmp_name(key)}, 12-month change", "percent", "M", *cmp_values("hicp", key, "TOT_X_NRG_FOOD"), 1)
    us = "us" in CMP_BLOCKS[block]
    kept = add_us(series, prev, "inflation") if us else None
    return with_us({"series": series,
            "sources": [cmp_src("hicp", "harmonized index of consumer prices (HICP), monthly",
                                "The HICP is compiled on the same definitions in every country. It differs from the national consumer price indices, mainly by leaving out owner-occupied housing costs.")]
                       + ([src("US Bureau of Labor Statistics, consumer price index for all urban consumers, not seasonally adjusted (series CPIAUCNS and CPILFENS)",
                               url="https://fred.stlouisfed.org/series/CPIAUCNS",
                               note=("The United States has no current harmonized index, so its figures are the national consumer price index, which includes "
                                     "owner-occupied housing costs. Its core measure excludes food and energy. " + FRED_NOTE))] if us else [])}, kept)


def cmp_unemployment(block: str) -> dict:
    series = {}
    for key in CMP_BLOCKS[block]:
        ages = "ages 16 and over" if key == "us" else "ages 15–74"
        series[f"rate_{key}"] = S(f"Unemployment rate, {cmp_name(key)}, {ages}", "percent of labor force", "M", *cmp_values("une", key, "TOTAL"), 1)
        series[f"youth_{key}"] = S(f"Youth unemployment rate, {cmp_name(key)}, under 25", "percent of labor force", "M", *cmp_values("une", key, "Y_LT25"), 1)
    note = "Labor force survey figures, seasonally adjusted."
    if "us" in CMP_BLOCKS[block]:
        note += " The United States figures are the national labor force survey's, where the working-age population starts at 16 and has no upper limit."
    return {"series": series, "sources": [cmp_src("une", "unemployment by sex and age, monthly", note)]}


def cmp_employment(block: str, prev: dict | None = None) -> dict:
    series = {}
    for key in CMP_BLOCKS[block]:
        if key == "us":
            continue
        series[f"rate2064_{key}"] = S(f"Employment rate, {cmp_name(key)}, ages 20–64", "percent of population", "Q", *cmp_values("emp", key, "Y20-64"), 1)
        series[f"rate1564_{key}"] = S(f"Employment rate, {cmp_name(key)}, ages 15–64", "percent of population", "Q", *cmp_values("emp", key, "Y15-64"), 1)
    us = "us" in CMP_BLOCKS[block]
    kept = add_us(series, prev, "employment") if us else None
    return with_us({"series": series,
            "sources": [cmp_src("emp", "employment and labor force by sex and age, quarterly", "Labor force survey figures, seasonally adjusted.")]
                       + ([src("OECD, employment rate for ages 15–64 in the United States, seasonally adjusted (series LREM64TTUSQ156S)", url="https://fred.stlouisfed.org/series/LREM64TTUSQ156S",
                               note="The United States labor force survey starts at age 16. There is no United States series for ages 20–64. " + FRED_NOTE)] if us else [])}, kept)


def cmp_growth_accounting(key: str) -> tuple[dict, dict]:
    """Growth accounting for one country from its national accounts at Eurostat: (growth_accounting result, labor share by year).
    The labor share is compensation of employees over compensation plus gross operating surplus and mixed income, which is the
    same ratio as for Mainland Norway (gross operating surplus is the net surplus plus consumption of fixed capital)."""
    if ("ga", key) not in _CMP:
        found = None
        for code in (["EA21", "EA20"] if key == "ea" else [None]):   # one euro area composition for all four datasets
            def get(name, *codes):
                try:
                    return dict(zip(*cmp_values(name, key, *codes, geo=code)))
                except KeyError:
                    return {}
            Y, L, K, W, O = get("va"), get("hours"), get("capital"), get("income", "D1"), get("income", "B2A3G")
            if not (Y and L):
                continue
            SH = {y: W[y] / (W[y] + O[y]) for y in sorted(W) if O.get(y) is not None and W[y] + O[y] > 0}
            if found is None or K:
                found = (growth_accounting(Y, L, K, SH), SH)
            if K:
                break
        if found is None:
            raise KeyError(f"no value added and hours for {key}")
        _CMP[("ga", key)] = found
    return _CMP[("ga", key)]


def norway_total_growth_accounting() -> tuple[dict, dict]:
    """Growth accounting for Norway's total economy from Statistics Norway's national accounts, the same tables as for
    Mainland Norway. Eurostat's figures for Norway are not used because its capital stock for Norway ends several years earlier."""
    if ("ga", "no_ssb") not in _CMP:
        code, start = "nr23_6", "from(1990)"
        py, sy, _ = ssb_series(ssb_data("09170", [("NACE", [code]), ("ContentsCode", ["BNPB2"]), ("Tid", [start])]))
        pl, sl, _ = ssb_series(ssb_data("09174", [("NACE", [code]), ("ContentsCode", ["Timeverk"]), ("Tid", [start])]))
        pk, sk, _ = ssb_series(ssb_data("09181", [("NACE", [code]), ("ContentsCode", ["KAPN4"]), ("Tid", [start])]))
        ys, gross, _ = labor_shares(code)
        SH = {y: v / 100 for y, v in zip(ys, gross) if v is not None and y >= "1990"}
        Y, L, K = dict(zip(py, sy[(code, "BNPB2")])), dict(zip(pl, sl[(code, "Timeverk")])), dict(zip(pk, sk[(code, "KAPN4")]))
        _CMP[("ga", "no_ssb")] = (growth_accounting(Y, L, K, SH), SH)
    return _CMP[("ga", "no_ssb")]


def cmp_productivity(block: str, prod: dict | None, prev: dict | None = None) -> dict:
    if not prod:
        raise RuntimeError("needs the productivity indicator (Mainland Norway comes from Statistics Norway)")
    base = "2000"
    m = prod["series"]
    since = lambda s: ([p for p in s["periods"] if p >= "1990"], [v for p, v in zip(s["periods"], s["values"]) if p >= "1990"])
    series = {
        "lp_mainland": S("Labor productivity, Mainland Norway: value added per hour worked (2000 = 100)", "index", "A", *since(m["labor_productivity"]), 1, derived=True),
        "lpg_mainland": S("Labor productivity growth, Mainland Norway", "percent", "A", *since(m["labor_productivity_growth"]), 1, derived=True),
        "tfp_mainland": S("Total factor productivity, Mainland Norway (2000 = 100)", "index", "A", *since(m["tfp"]), 1, derived=True),
        "tfpg_mainland": S("TFP growth, Mainland Norway", "percent", "A", *since(m["tfp_growth"]), 1, derived=True),
        "share_mainland": S("Labor share used as weight, Mainland Norway", "percent", "A", *since(m["labor_share"]), 1, derived=True),
    }
    no_capital = []

    def add(key, name, ga, SH, output="value added"):
        out = {}
        years = ga["years"]
        out[f"lp_{key}"] = S(f"Labor productivity, {name}: {output} per hour worked (2000 = 100)", "index", "A", years, index_to(ga["lp"], years, base), 1, derived=True)
        out[f"lpg_{key}"] = S(f"Labor productivity growth, {name}", "percent", "A", *trim(years, pct_change(ga["lp"], 1)), 1, derived=True)
        if any(v is not None for v in ga["tfp"]):
            out[f"tfp_{key}"] = S(f"Total factor productivity, {name} (2000 = 100)", "index", "A", *trim(years, index_to(ga["tfp"], years, base)), 1, derived=True)
            out[f"tfpg_{key}"] = S(f"TFP growth, {name}", "percent", "A", *trim(years, ga["tfp_growth"]), 1, derived=True)
        else:
            no_capital.append(name)
        out[f"share_{key}"] = S(f"Labor share used as weight, {name}", "percent", "A", sorted(SH), [round(100 * SH[y], 1) for y in sorted(SH)], 1, derived=True)
        return out

    for key in CMP_BLOCKS[block]:
        if key == "us":
            continue
        name = "Norway, total economy" if key == "no" else cmp_name(key)
        series.update(add(key, name, *(norway_total_growth_accounting() if key == "no" else cmp_growth_accounting(key))))
    us = "us" in CMP_BLOCKS[block]
    kept = add_us(series, prev, "productivity", lambda: add("us", "United States", *us_series("productivity")["_ga"], output="GDP")) if us else None
    method = ("Every series in this tile is my own calculation from national accounts data, with the same method for every country as in the productivity tile "
              "for Mainland Norway. Labor productivity is gross value added at constant prices divided by total hours worked. TFP is computed by growth accounting "
              "(Törnqvist index) from a Cobb-Douglas production function. Annual TFP growth is Δlog A = Δlog Y − (1 − s̄) Δlog K − s̄ Δlog L, where s̄ is the average of "
              "the labor share in the two adjacent years, and the index is the cumulated growth rates set to 100 in 2000. Y is gross value added at constant prices, "
              "L is total hours worked and K is the net stock of fixed assets at constant prices. The labor share is compensation of employees divided by compensation "
              "of employees plus gross operating surplus and mixed income. For Mainland Norway and for Norway's total economy the data are Statistics Norway's "
              "national accounts. For the other countries they are the national accounts as published by Eurostat, for the whole economy. TFP ends where the capital "
              "stock ends, which is earlier than the other series for some countries. "
              + ("For the United States, Y is real GDP because its accounts have no long series for total value added at basic prices, K is the Bureau of Economic "
                 "Analysis' quantity index for the net stock of private and government fixed assets, L is total hours worked as published by the OECD, and the "
                 "labor share uses the income components the United States reports to the OECD. " if us else "")
              + (f"Eurostat has no capital stock at constant prices for {' and '.join(no_capital)}, so TFP is missing there. " if no_capital else "")
              + "No adjustment is made for capacity utilization, labor quality or the labor income of the self-employed (which is part of mixed income).")
    return with_us({"series": series, "method": method,
            "sources": [cmp_src("va", "gross value added by industry, annual national accounts", "Total economy, chain-linked volumes."),
                        cmp_src("hours", "employment by industry, annual national accounts", "Hours worked, total employment, domestic concept."),
                        cmp_src("capital", "capital stocks by industry and asset type", "Net stock of total fixed assets, chain-linked volumes."),
                        cmp_src("income", "GDP and main components, annual", "Compensation of employees and gross operating surplus and mixed income, current prices."),
                        src("Statistics Norway, annual national accounts (Mainland Norway and Norway's total economy)", "09189", "https://www.ssb.no/en/statbank/table/09189",
                            note="Also tables 09174 (hours), 09181 (capital) and 09170 (income and total value added), as in the productivity tile at the top of the page.")]
                       + ([src("US Bureau of Economic Analysis, real GDP, annual (series GDPCA)", url="https://fred.stlouisfed.org/series/GDPCA", note=FRED_NOTE),
                           src("US Bureau of Economic Analysis, fixed assets accounts, table 1.2 (series " + BEA_NET_STOCK + ")", url="https://apps.bea.gov/iTable/?ReqID=10&step=2",
                               note="Chain-type quantity index for the net stock of fixed assets, private and government."),
                           src("OECD, productivity database (total hours worked) and annual national accounts (income components), United States", "DSD_PDB@DF_PDB",
                               "https://data-explorer.oecd.org/vis?df[ds]=dsDisseminateFinalDMZ&df[id]=DSD_PDB%40DF_PDB&df[ag]=OECD.SDD.TPS")] if us else [])}, kept)


def wage_series(key: str, name: str, nominal: dict, prices: dict, measure: str, deflator: str) -> dict:
    """Nominal and real wage growth series for one country from {year: percent} dicts. Real growth is (1 + w) / (1 + p) - 1."""
    ys = sorted(nominal)
    real = {y: round(((1 + nominal[y] / 100) / (1 + prices[y] / 100) - 1) * 100, 2) for y in ys if prices.get(y) is not None}
    return {f"nominal_{key}": S(f"Wage growth, {name}: {measure}", "percent", "A", ys, [nominal[y] for y in ys], 1, derived=True),
            f"real_{key}": S(f"Real wage growth, {name}: {measure}, deflated by {deflator}", "percent", "A", sorted(real), [real[y] for y in sorted(real)], 1, derived=True)}


def cmp_wages(block: str, prev: dict | None = None) -> dict:
    """Annual wage growth from the national accounts: wages and salaries per hour worked by employees, nominal and deflated by the HICP."""
    series = {}
    for key in CMP_BLOCKS[block]:
        if key == "us":
            continue
        for code in (["EA21", "EA20"] if key == "ea" else [None]):   # one euro area composition for wages and hours
            try:
                W, H = dict(zip(*cmp_values("wagebill", key, geo=code))), dict(zip(*cmp_values("employee_hours", key, geo=code)))
                break
            except KeyError:
                continue
        else:
            raise KeyError(f"no wages and employee hours for {key}")
        nominal = dict(zip(*change_over({y: W[y] / H[y] for y in W if H.get(y)}, year_earlier)))
        prices = dict(zip(*cmp_values("hicp_annual", key)))
        series.update(wage_series(key, cmp_name(key), nominal, prices, "wages and salaries per hour worked by employees", "the HICP"))
    us = "us" in CMP_BLOCKS[block]
    kept = add_us(series, prev, "wages") if us else None
    method = ("Every series in this tile is my own calculation. Nominal wage growth is the growth in wages and salaries per hour worked by employees, both from the "
              "national accounts for the whole economy as published by Eurostat. Real wage growth deflates it by the annual average change in the harmonized index "
              "of consumer prices, i.e. (1 + wage growth) / (1 + inflation) − 1. Norway is computed in the same way as the other countries, so the figures differ "
              "from annual earnings in the wage tile at the top of the page, which Statistics Norway measures per full-time equivalent. "
              + ("For the United States, wages are the Bureau of Economic Analysis' wages and salaries per full-time equivalent employee, since its accounts have "
                 "no current series for hours worked by employees, and prices are the national consumer price index. " if us else "")
              + "Real wages start later for countries where the harmonized price index starts later.")
    return with_us({"series": series, "method": method,
            "sources": [cmp_src("wagebill", "gross value added and income by industry, annual national accounts", "Wages and salaries, total economy, current prices."),
                        cmp_src("employee_hours", "employment by industry, annual national accounts", "Hours worked by employees, domestic concept."),
                        cmp_src("hicp_annual", "harmonized index of consumer prices (HICP), annual", "Annual average rate of change.")]
                       + ([src("US Bureau of Economic Analysis, wages and salaries per full-time equivalent employee (series A4401C0A052NBEA)", url="https://fred.stlouisfed.org/series/A4401C0A052NBEA", note=FRED_NOTE),
                           src("US Bureau of Labor Statistics, consumer price index for all urban consumers (series CPIAUCNS)", url="https://fred.stlouisfed.org/series/CPIAUCNS",
                               note="Annual averages of the monthly index. " + FRED_NOTE)] if us else [])}, kept)


def cmp_rates(block: str) -> dict:
    policy, yields = cmp_policy_rates(), cmp_bond_yields()
    series = {}
    for key, whose in CMP_POLICY[block].items():
        p, v = policy[CMP_COUNTRIES[whose][1]]
        label = f"Policy rate, {cmp_name(key)}" + ("" if key == whose else " (the euro area’s rate, set by the European Central Bank)")
        series[f"policy_{key}"] = S(label + ", daily", "percent", "D", p, v, 2, steps=True)
    for key in CMP_YIELDS[block]:
        p, v = yields[CMP_COUNTRIES[key][2]]
        series[f"gov10y_{key}"] = S(f"10-year government bond yield, {cmp_name(key)}", "percent", "M", p, v, 2)
    return {"series": series,
            "sources": [src("Bank for International Settlements, central bank policy rates", "WS_CBPOL", "https://data.bis.org/topics/CBPOL",
                            note=("Daily, up to the latest day the BIS has published for all the central banks. The BIS picks each central bank’s main policy rate. For the euro area "
                                  "it is the deposit facility rate from September 2024 and the main refinancing rate before that.")),
                        src("OECD, long-term interest rates (Main Economic Indicators)", "DSD_STES@DF_FINMARK", "https://data-explorer.oecd.org/vis?df[ds]=dsDisseminateFinalDMZ&df[id]=DSD_STES%40DF_FINMARK&df[ag]=OECD.SDD.STES",
                            note="Monthly averages of yields on government bonds with about ten years to maturity.")]}

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
    kept: dict = {}

    def run(key, fn, *args, keep_quietly=False):
        """keep_quietly: if the fetch fails and earlier data exists, keep it without reporting an error on the page.
        For sources that change a few times a year and that the build server may not be able to reach."""
        try:
            d = fn(*args)
            d["fetched"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            indicators[key] = d
            n = sum(len(s["periods"]) for s in d["series"].values())
            print(f"  ok   {key:16} {len(d['series'])} series, {n} points")
        except Exception as e:  # keep the previous data for this indicator
            if keep_quietly and key in previous:
                kept[key] = f"{type(e).__name__}: {e}"
                indicators[key] = previous[key]
                indicators[key].pop("stale", None)
                indicators[key].pop("error", None)
                print(f"  kept {key:16} from {previous[key].get('fetched', 'an earlier run')} ({kept[key]})")
                return
            errors[key] = f"{type(e).__name__}: {e}"
            print(f"  FAIL {key:16} {errors[key]}", file=sys.stderr)
            traceback.print_exc(limit=2)
            if key in previous:
                indicators[key] = previous[key]
                indicators[key]["stale"] = True
                indicators[key]["error"] = errors[key]

    print("Fetching dashboard data ...")
    run("gdp", ind_gdp)
    run("consumption", ind_consumption)
    run("investment", ind_investment)
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
    run("tightness", ind_tightness)
    run("rates", ind_rates, proj, previous.get("rates"))
    run("fx", ind_fx)
    run("trade", ind_trade)
    run("current_account", ind_current_account, gdp)
    run("oil_fund", ind_oil_fund, gdp)
    run("budget", ind_budget, indicators.get("oil_fund"), keep_quietly=True)   # regjeringen.no does not answer GitHub's servers
    run("house_prices", ind_house_prices)
    run("credit", ind_credit)
    run("productivity", ind_productivity)
    run("wages", ind_wages)
    run("labor_shares", ind_labor_shares)
    run("inequality", ind_inequality, previous.get("inequality"))
    for block in CMP_BLOCKS:   # Norway against the Nordic countries and against European countries
        run(f"{block}_gdp", cmp_gdp, block, indicators.get("gdp"), previous.get(f"{block}_gdp"))
        run(f"{block}_inflation", cmp_inflation, block, previous.get(f"{block}_inflation"))
        run(f"{block}_rates", cmp_rates, block)
        run(f"{block}_unemployment", cmp_unemployment, block)
        run(f"{block}_employment", cmp_employment, block, previous.get(f"{block}_employment"))
        run(f"{block}_wages", cmp_wages, block, previous.get(f"{block}_wages"))
        run(f"{block}_productivity", cmp_productivity, block, indicators.get("productivity"), previous.get(f"{block}_productivity"))

    OUT.parent.mkdir(exist_ok=True)
    payload = {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "errors": errors, "kept": kept, "indicators": indicators}
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size/1024:.0f} kB), {len(indicators)} indicators, {len(errors)} errors")
    return 0 if not errors else (0 if indicators else 1)


if __name__ == "__main__":
    sys.exit(main())
