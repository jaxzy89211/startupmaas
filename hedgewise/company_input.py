"""
Turn whatever a finance manager hands you into a HedgeWise consumption profile.

Accepted inputs
  (a) our template  data/company_example.csv  (month, gas_m3, power_kwh)
  (b) any CSV or Excel export with a date/month column and gas and/or power columns. Column names
      are detected (Dutch or English); in the app the user can confirm or correct them.
  (c) only yearly totals (gas m3/yr, power kWh or MWh/yr) plus a sector, spread over the months with
      the sector's seasonal profile (SECTOR_PROFILES below).

Units:   gas  m3 | kWh | MWh | GJ        power  kWh | MWh
Formats: Dutch numbers ("1.234,5"), English numbers ("1,234.5"), Dutch/English month names
         ("januari 2025", "mrt-25", "Oct 2025"), ISO dates, day-first dates ("31-01-2025"), month numbers.

Several years   -> average per calendar month.
1-3 months gap  -> filled from the sector profile (scaled to the months you do have) + a warning.
>3 months gap   -> clear error.

    from company_input import load_company
    res = load_company("bills.xlsx", name="Bakkerij X", sector="food processing")
    res.company, res.warnings, res.mapping
"""
from __future__ import annotations

import csv
import dataclasses
import io
import re
from pathlib import Path

import numpy as np
import pandas as pd

from .company import Company
from .settings import MWH_PER_M3

MWH_PER_GJ = 0.2778

# --------------------------------------------------------------------------------------------
# Seasonal profiles per sector: share of the yearly volume in each month (Jan..Dec, in %).
# THESE ARE ASSUMPTIONS (typical shapes, not measured data). Replace them with interview or
# pilot data as soon as you have it.
#   food processing : process steam all year + space heating in winter; flat power, small summer cooling bump
#   metal           : mostly process heat (mildly seasonal); flat power with an August holiday dip
#   greenhouse      : heating-dominated gas (CHP), strong winter peak; power for grow lights in winter
#   offices         : gas almost only for space heating; nearly flat power, small summer holiday dip
# --------------------------------------------------------------------------------------------
SECTOR_PROFILES = {
    "food processing": {
        "gas":   [11.5, 10.5, 9.5, 8.0, 7.0, 6.0, 5.5, 5.5, 6.5, 8.0, 10.0, 12.0],
        "power": [8.2, 7.6, 8.3, 8.1, 8.4, 8.5, 8.9, 8.8, 8.4, 8.4, 8.2, 8.2],
    },
    "metal": {
        "gas":   [9.5, 9.0, 9.0, 8.5, 8.0, 7.5, 7.0, 6.5, 8.0, 8.5, 9.0, 9.5],
        "power": [8.6, 8.2, 8.7, 8.3, 8.4, 8.3, 8.0, 6.8, 8.5, 8.7, 8.6, 8.9],
    },
    "greenhouse horticulture": {
        "gas":   [14.0, 12.5, 11.0, 8.0, 5.5, 3.5, 3.0, 3.0, 5.0, 8.0, 12.0, 14.5],
        "power": [12.0, 11.0, 10.0, 7.5, 5.5, 4.5, 4.5, 5.0, 6.5, 9.0, 11.5, 13.0],
    },
    "offices": {
        "gas":   [16.0, 14.0, 12.0, 8.0, 4.0, 2.0, 1.5, 1.5, 3.0, 8.0, 13.0, 17.0],
        "power": [9.0, 8.5, 8.6, 8.0, 7.8, 8.0, 8.2, 7.4, 8.1, 8.6, 8.8, 9.0],
    },
}
SECTORS = list(SECTOR_PROFILES)
GAS_UNITS = ["m3", "kWh", "MWh", "GJ"]
POWER_UNITS = ["kWh", "MWh"]
MAX_MISSING_MONTHS = 3


def profile(sector: str, commodity: str) -> np.ndarray:
    """Monthly shares (sum to 1) for a sector and 'gas' or 'power'."""
    key = sector.strip().lower()
    if key not in SECTOR_PROFILES:
        raise ValueError(f"Unknown sector '{sector}'. Choose one of: {', '.join(SECTORS)}")
    p = np.asarray(SECTOR_PROFILES[key][commodity], float)
    return p / p.sum()


# --------------------------------------------------------------------------------------------
# Units
# --------------------------------------------------------------------------------------------
def gas_to_m3(values, unit: str) -> np.ndarray:
    """Convert gas volumes to m3 (the Company class stores gas in m3)."""
    v = np.asarray(values, float)
    u = _norm_unit(unit)
    if u == "m3":
        return v
    if u == "kwh":
        return v / 1000 / MWH_PER_M3
    if u == "mwh":
        return v / MWH_PER_M3
    if u == "gj":
        return v * MWH_PER_GJ / MWH_PER_M3
    raise ValueError(f"Unknown gas unit '{unit}'. Use one of {GAS_UNITS}")


def power_to_kwh(values, unit: str) -> np.ndarray:
    v = np.asarray(values, float)
    u = _norm_unit(unit)
    if u == "kwh":
        return v
    if u == "mwh":
        return v * 1000
    raise ValueError(f"Unknown power unit '{unit}'. Use one of {POWER_UNITS}")


def _norm_unit(unit: str) -> str:
    return unit.strip().lower().replace("³", "3").replace("m^3", "m3").replace(" ", "")


def unit_from_name(col: str, commodity: str) -> str | None:
    """Guess the unit from a column header like 'Aardgas (m³)' or 'Verbruik MWh'."""
    s = _norm_unit(str(col))
    for u in (["gj", "mwh", "kwh", "m3", "nm3"] if commodity == "gas" else ["mwh", "kwh"]):
        if re.search(rf"(^|[^a-z]){u}($|[^a-z])", s) or s.endswith(u):
            return {"gj": "GJ", "mwh": "MWh", "kwh": "kWh", "m3": "m3", "nm3": "m3"}[u]
    return None


# --------------------------------------------------------------------------------------------
# Numbers and months
# --------------------------------------------------------------------------------------------
_THOUSANDS_DOT = re.compile(r"^-?\d{1,3}(\.\d{3})+$")
_THOUSANDS_COMMA = re.compile(r"^-?\d{1,3}(,\d{3})+$")
_UNIT_SUFFIX = re.compile(r"\s*(m3|m³|nm3|kwh|mwh|gj)\s*$", re.IGNORECASE)   # "12.345 kWh" -> "12.345"


def parse_numbers(values) -> np.ndarray:
    """Parse a column of numbers written Dutch ('1.234,5'), English ('1,234.5') or plain.
    The format is decided per column, so '1.234' is read as 1234 when the column is Dutch."""
    raw = [("" if v is None or (isinstance(v, float) and np.isnan(v)) else v) for v in values]
    if all(isinstance(v, (int, float, np.number)) or v == "" for v in raw):
        return np.array([np.nan if v == "" else float(v) for v in raw])
    s = [_UNIT_SUFFIX.sub("", str(v).strip().replace("€", "")).replace(" ", "").replace(" ", "")
         for v in raw]
    nonempty = [x for x in s if x not in ("", "-")]
    both = [x for x in nonempty if "," in x and "." in x]
    if both:
        dutch = sum(x.rfind(",") > x.rfind(".") for x in both) >= len(both) / 2
    elif any("," in x for x in nonempty):
        # only commas: thousands separators if every value looks like 1,234 / 12,345,678
        dutch = not all(_THOUSANDS_COMMA.match(x) for x in nonempty if "," in x)
    elif any("." in x for x in nonempty):
        # only dots: Dutch thousands separators if every dotted value looks like 1.234 / 1.234.567
        dutch = all(_THOUSANDS_DOT.match(x) for x in nonempty if "." in x)
    else:
        dutch = False
    out = []
    for x in s:
        if x in ("", "-"):
            out.append(np.nan)
            continue
        y = x.replace(".", "").replace(",", ".") if dutch else x.replace(",", "")
        try:
            out.append(float(y))
        except ValueError:
            out.append(np.nan)
    return np.array(out)


MONTH_NAMES = {
    # Dutch
    "januari": 1, "jan": 1, "februari": 2, "feb": 2, "maart": 3, "mrt": 3, "mar": 3, "april": 4, "apr": 4,
    "mei": 5, "juni": 6, "jun": 6, "juli": 7, "jul": 7, "augustus": 8, "aug": 8, "september": 9, "sep": 9,
    "sept": 9, "oktober": 10, "okt": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
    # English (where different)
    "january": 1, "february": 2, "march": 3, "may": 5, "june": 6, "july": 7, "august": 8,
    "october": 10, "oct": 10,
}


def parse_month(v) -> int | None:
    """Calendar month (1-12) of a cell, or None if it isn't recognisable."""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    if isinstance(v, (pd.Timestamp, np.datetime64)) or hasattr(v, "month"):
        return int(pd.Timestamp(v).month)
    if isinstance(v, (int, np.integer)) or (isinstance(v, float) and float(v).is_integer()):
        return int(v) if 1 <= int(v) <= 12 else None
    s = str(v).strip().lower()
    if not s:
        return None
    for word in re.findall(r"[a-z]+", s):
        if word in MONTH_NAMES:
            return MONTH_NAMES[word]
    if re.fullmatch(r"\d{1,2}", s):
        n = int(s)
        return n if 1 <= n <= 12 else None
    m = re.fullmatch(r"(\d{4})[-/.](\d{1,2})([-/.]\d{1,2})?(\s.*)?", s)        # 2025-01, 2025-01-31
    if m:
        return int(m.group(2)) if 1 <= int(m.group(2)) <= 12 else None
    m = re.fullmatch(r"(\d{1,2})[-/.](\d{4})", s)                              # 01-2025
    if m:
        return int(m.group(1)) if 1 <= int(m.group(1)) <= 12 else None
    m = re.fullmatch(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})(\s.*)?", s)       # 31-01-2025 (day first, NL)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if a > 12 and 1 <= b <= 12:
            return b
        if b > 12 and 1 <= a <= 12:     # US style 01/31/2025
            return a
        return b if 1 <= b <= 12 else None
    try:
        return int(pd.Timestamp(s).month)
    except (ValueError, TypeError):
        return None


def parse_year(v) -> int | None:
    if hasattr(v, "year") and not isinstance(v, (int, float)):
        return int(pd.Timestamp(v).year)
    m = re.search(r"(19|20)\d{2}", str(v))
    if m:
        return int(m.group(0))
    m = re.search(r"[-/' ](\d{2})$", str(v).strip())   # 'jan-25', "mrt '25"
    return 2000 + int(m.group(1)) if m else None


# --------------------------------------------------------------------------------------------
# Reading files and detecting columns
# --------------------------------------------------------------------------------------------
DATE_WORDS = ["maand", "month", "datum", "date", "periode", "period", "factuurmaand", "jaar-maand", "tijdvak"]
GAS_WORDS = ["gas", "aardgas", "m3", "m³", "gj"]
POWER_WORDS = ["elektr", "electr", "stroom", "power", "kwh", "mwh", "levering"]
NOT_POWER = ["gas"]


def _source_name(src) -> str:
    return str(getattr(src, "name", src))


def read_table(src) -> pd.DataFrame:
    """Read a CSV (any delimiter) or Excel file into a DataFrame of raw values, with the header row found
    automatically (exports often have a few title lines above the table)."""
    name = _source_name(src).lower()
    if name.endswith((".xlsx", ".xlsm", ".xls")):
        raw = pd.read_excel(src, header=None, dtype=object)
    else:
        data = src.read() if hasattr(src, "read") else Path(src).read_bytes()
        if isinstance(data, bytes):
            for enc in ("utf-8-sig", "cp1252", "latin-1"):
                try:
                    text = data.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
        else:
            text = data
        try:
            delim = csv.Sniffer().sniff(text[:4096], delimiters=";,\t|").delimiter
        except csv.Error:
            delim = ";" if text.count(";") > text.count(",") else ","
        # csv.reader instead of pd.read_csv: title lines above the table have fewer fields
        rows = [r for r in csv.reader(io.StringIO(text), delimiter=delim) if any(x.strip() for x in r)]
        width = max((len(r) for r in rows), default=0)
        raw = pd.DataFrame([r + [""] * (width - len(r)) for r in rows], dtype=object)
    raw = raw.dropna(how="all").reset_index(drop=True)
    h = _header_row(raw)
    cols = [str(c).strip() if str(c).strip() not in ("", "nan", "None") else f"col{i}"
            for i, c in enumerate(raw.iloc[h])]
    df = raw.iloc[h + 1:].reset_index(drop=True)
    df.columns = _dedupe(cols)
    df = df.replace("", np.nan).dropna(how="all")
    return df.dropna(axis=1, how="all").reset_index(drop=True)


def _dedupe(cols):
    seen, out = {}, []
    for c in cols:
        seen[c] = seen.get(c, 0) + 1
        out.append(c if seen[c] == 1 else f"{c}_{seen[c]}")
    return out


def _header_row(raw: pd.DataFrame) -> int:
    for i in range(min(15, len(raw))):
        cells = [str(x).lower() for x in raw.iloc[i] if str(x).strip() not in ("", "nan", "None")]
        has_date = any(any(w in c for w in DATE_WORDS) for c in cells)
        has_val = any(any(w in c for w in GAS_WORDS + POWER_WORDS) for c in cells)
        if has_date and has_val:
            return i
    return 0


def detect_columns(df: pd.DataFrame) -> dict:
    """Best guess of {'date', 'gas', 'power', 'gas_unit', 'power_unit'} (None where not found)."""
    cols = list(df.columns)
    low = {c: str(c).lower() for c in cols}

    def numeric_share(c):
        v = parse_numbers(df[c].tolist())
        return np.isfinite(v).mean() if len(v) else 0

    date = next((c for c in cols if any(w in low[c] for w in DATE_WORDS)), None)
    if date is None:   # fall back to the first column whose cells look like months
        date = next((c for c in cols if df[c].map(parse_month).notna().mean() > 0.8), None)
    gas = next((c for c in cols if c != date and any(w in low[c] for w in GAS_WORDS) and numeric_share(c) > 0.5), None)
    power = next((c for c in cols if c not in (date, gas) and any(w in low[c] for w in POWER_WORDS)
                  and not any(w in low[c] for w in NOT_POWER) and numeric_share(c) > 0.5), None)
    return {"date": date, "gas": gas, "power": power,
            "gas_unit": (unit_from_name(gas, "gas") if gas else None) or "m3",
            "power_unit": (unit_from_name(power, "power") if power else None) or "kWh"}


# --------------------------------------------------------------------------------------------
# Building the company
# --------------------------------------------------------------------------------------------
@dataclasses.dataclass
class CompanyInput:
    company: Company
    warnings: list
    mapping: dict
    months_found: dict          # commodity -> sorted list of calendar months with data
    years: list


def _monthly_profile(months, years, values, commodity, sector, warnings):
    """Average per calendar month; fill 1-3 missing months from the sector profile."""
    s = pd.DataFrame({"m": months, "y": years, "v": values}).dropna(subset=["m", "v"])
    if s.empty:
        return None, []
    # sum within (year, month) first: some exports have several rows per month (e.g. two meters)
    per = s.groupby(["y", "m"], dropna=False)["v"].sum().groupby(level="m").mean()
    per = per.reindex(range(1, 13))
    have = [int(m) for m in per.index[per.notna()]]
    missing = [m for m in range(1, 13) if m not in have]
    if len(missing) > MAX_MISSING_MONTHS:
        raise ValueError(f"{commodity}: only {len(have)} of 12 calendar months have data "
                         f"(missing: {', '.join(_MONTH_ABBR[m-1] for m in missing)}). "
                         f"Need at least {12 - MAX_MISSING_MONTHS} months, or enter yearly totals + sector instead.")
    if missing:
        p = profile(sector, commodity)
        idx = np.array(have) - 1
        scale = per.loc[have].sum() / p[idx].sum()
        for m in missing:
            per.loc[m] = p[m - 1] * scale
        warnings.append(f"{commodity}: no data for {', '.join(_MONTH_ABBR[m-1] for m in missing)}; filled in from "
                        f"the '{sector}' seasonal profile (an assumption). Check these months with the company.")
    return per.values.astype(float), have


_MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def company_from_frame(df: pd.DataFrame, mapping: dict | None = None, name: str = "Company",
                       sector: str = "food processing") -> CompanyInput:
    """Build a Company from a table of monthly consumption. `mapping` overrides detect_columns()."""
    mp = detect_columns(df)
    if mapping:
        mp.update({k: v for k, v in mapping.items() if v is not None or k in ("gas", "power")})
    if mp["date"] is None or mp["date"] not in df.columns:
        raise ValueError(f"Could not find a date/month column. Columns are: {list(df.columns)}")
    if not mp.get("gas") and not mp.get("power"):
        raise ValueError(f"Could not find a gas or power consumption column. Columns are: {list(df.columns)}")
    warnings = []
    months = df[mp["date"]].map(parse_month)
    years = df[mp["date"]].map(parse_year)
    bad = int(months.isna().sum())
    if bad:
        warnings.append(f"{bad} row(s) skipped: '{mp['date']}' is not a recognisable month "
                        f"(e.g. '{df[mp['date']][months.isna()].iloc[0]}').")
    n_years = len(set(y for y in years.dropna())) or 1
    if n_years > 1:
        warnings.append(f"Data covers {n_years} years; using the average per calendar month.")

    out = {}
    found = {}
    for c, conv, unit_key in (("gas", gas_to_m3, "gas_unit"), ("power", power_to_kwh, "power_unit")):
        col = mp.get(c)
        if not col:
            out[c] = np.zeros(12)
            found[c] = []
            warnings.append(f"No {c} column: {c} consumption set to zero.")
            continue
        vals = conv(parse_numbers(df[col].tolist()), mp[unit_key])
        prof_vals, have = _monthly_profile(months.values, years.values, vals, c, sector, warnings)
        if prof_vals is None:
            raise ValueError(f"Column '{col}' has no usable numbers.")
        out[c] = prof_vals
        found[c] = have
    comp = Company(name=name, gas_m3=tuple(out["gas"]), power_kwh=tuple(out["power"]))
    return CompanyInput(comp, warnings, mp, found, sorted(int(y) for y in set(years.dropna())))


def load_company(src, name: str | None = None, sector: str = "food processing",
                 mapping: dict | None = None) -> CompanyInput:
    """Read a CSV/Excel file (template or any export) and return the profile + warnings."""
    df = read_table(src)
    nm = name or Path(_source_name(src)).stem
    return company_from_frame(df, mapping, nm, sector)


def from_yearly(gas: float = 0.0, gas_unit: str = "m3", power: float = 0.0, power_unit: str = "kWh",
                sector: str = "food processing", name: str = "Company") -> CompanyInput:
    """Yearly totals only: spread over the months with the sector's seasonal profile."""
    if gas < 0 or power < 0:
        raise ValueError("Yearly totals can't be negative.")
    if gas == 0 and power == 0:
        raise ValueError("Enter a yearly gas and/or power volume.")
    g = gas_to_m3(gas, gas_unit) * profile(sector, "gas")
    p = power_to_kwh(power, power_unit) * profile(sector, "power")
    w = [f"Monthly split uses the '{sector}' seasonal profile (an assumption, not your bills)."]
    comp = Company(name=name, gas_m3=tuple(g), power_kwh=tuple(p))
    return CompanyInput(comp, w, {"source": "yearly totals", "sector": sector}, {"gas": [], "power": []}, [])


def describe(res: CompanyInput) -> str:
    c = res.company
    gas_m3 = sum(c.gas_m3)
    return (f"{c.name}: {gas_m3:,.0f} m3 gas ({gas_m3 * MWH_PER_M3:,.0f} MWh) and "
            f"{sum(c.power_kwh)/1000:,.0f} MWh power per year")


def main(argv=None):
    """Command-line entry point."""
    import argparse
    ap = argparse.ArgumentParser(description="Check how HedgeWise reads a consumption file.")
    ap.add_argument("file")
    ap.add_argument("--sector", default="food processing", choices=SECTORS)
    a = ap.parse_args(argv)
    r = load_company(a.file, sector=a.sector)
    print(describe(r))
    print("Columns used:", r.mapping)
    for w in r.warnings:
        print("WARNING:", w)
    print(pd.DataFrame({"gas_m3": r.company.gas_m3, "power_kwh": r.company.power_kwh}, index=_MONTH_ABBR).round(0))


# --------------------------------------------------------------------------------------------
# Shared command-line options (hedgewise_model.py, backtest.py, report.py)
# --------------------------------------------------------------------------------------------
def add_cli_args(ap) -> None:
    ap.add_argument("--company", help="consumption file: our template, or any CSV/Excel export of monthly bills")
    ap.add_argument("--name", help="company name (default: file name)")
    ap.add_argument("--sector", default="food processing", choices=SECTORS,
                    help="seasonal profile for yearly totals and for filling missing months")
    ap.add_argument("--yearly-gas", type=float, help="yearly gas use, instead of a file (unit: --gas-unit)")
    ap.add_argument("--gas-unit", default="m3", choices=GAS_UNITS)
    ap.add_argument("--yearly-power", type=float, help="yearly power use, instead of a file (unit: --power-unit)")
    ap.add_argument("--power-unit", default="MWh", choices=POWER_UNITS)


def company_from_args(args, verbose: bool = True) -> Company:
    """The Company described by the CLI options (default: the hypothetical food processor)."""
    if args.company:
        res = load_company(args.company, name=args.name, sector=args.sector)
    elif args.yearly_gas or args.yearly_power:
        res = from_yearly(args.yearly_gas or 0, args.gas_unit, args.yearly_power or 0, args.power_unit,
                          args.sector, args.name or f"{args.sector.title()} company")
    else:
        return Company(name=args.name) if args.name else Company()
    if verbose:
        print(describe(res))
        for w in res.warnings:
            print("  WARNING:", w)
    return res.company
