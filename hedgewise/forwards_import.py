"""Import real forward-price exports into data/forwards.csv (the format forwards.py reads).

    python forwards_import.py export1.xlsx [export2.csv ...] [--out data/forwards.csv] [--force]

Supported exports (detected from the column headers; the layouts are the typical end-of-day exports of each
vendor - CHECK THE REPORT against the first real file and extend `config.yaml: forwards.codes` if an instrument code
isn't recognised):
    refinitiv  Refinitiv / Eikon (LSEG Workspace): long ("Date / Exchange Date", "RIC / Instrument", "Close /
               Settle") or wide (a date column + one column per RIC)
    bloomberg  Bloomberg BDH/BDP export: long ("Date", "Security / Ticker", "PX_LAST / PX_SETTLE") or wide (Dates +
               one column per ticker, e.g. "TZT1 Comdty")
    eex        EEX DataSource end-of-day: "Trading Date", "Product", "Delivery Period / Maturity", optional "Load
               Type", "Settlement Price", optional "Unit"
    ice        ICE Endex end-of-day report: "Trade Date", "Contract / Commodity", "Strip / Contract Month",
               "Settlement Price / Settle", optional "Unit"
    hedgewise  already in our format: date, product, price (e.g. data/forwards_example.csv)

Only calendar-year baseload products of TTF gas and Dutch power are kept (forwards.parse_product: peakload,
months, quarters, seasons and other hubs are rejected and listed in the report). Prices are converted to EUR/MWh
(ct/kWh x 10); any other currency or unit (GBP, pence/therm, USD) is rejected. Nothing is overwritten without --force.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import numpy as np
import pandas as pd

from . import company_input as ci
from .config import load_config
from .forwards import parse_product
from .settings import DATA

CODES = {str(k).upper(): v for k, v in (load_config().get("forwards", {}) or {}).get("codes", {}).items()}
OUT_COLUMNS = ["date", "product", "price", "source"]


def _norm(c: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(c).lower()).strip()


def _col(df: pd.DataFrame, *names: str) -> str | None:
    """First column whose normalised name equals one of `names` (or starts with it)."""
    low = {c: _norm(c) for c in df.columns}
    for n in names:
        for c, lc in low.items():
            if lc == n:
                return c
    for n in names:
        for c, lc in low.items():
            if lc.startswith(n):
                return c
    return None


def detect_format(df: pd.DataFrame) -> str:
    """Which vendor layout a table has (see the module docstring), or 'unknown'."""
    cols = {_norm(c) for c in df.columns}
    if (
        {"trading date", "product"} <= cols
        or any(c.startswith("trading date") for c in cols)
        and _col(df, "settlement price")
    ):
        return "eex"
    if any(c.startswith("trade date") for c in cols) and (_col(df, "strip", "contract month") is not None):
        return "ice"
    if _col(df, "px last", "px settle") or any(c.endswith(" comdty") for c in cols) or "dates" in cols:
        return "bloomberg"
    if _col(df, "ric", "instrument") or "exchange date" in cols or _col(df, "close", "settle") and _col(df, "date"):
        if not _col(df, "product"):
            return "refinitiv"
    if _col(df, "date", "datum") and _col(df, "product") and _col(df, "price", "prijs", "settlement"):
        return "hedgewise"
    date = _col(df, "date", "exchange date", "timestamp")
    others = [c for c in df.columns if c != date]
    if date and others and all(re.fullmatch(r"[A-Z0-9.=:^-]{3,16}", str(c).strip()) for c in others):
        return "refinitiv"  # wide export: a date column + one column per instrument code (RIC)
    return "unknown"


def _code_to_name(code: str) -> str:
    """An instrument code (RIC / Bloomberg ticker) -> a product name parse_product understands, via the
    config table `forwards.codes` (e.g. TFMBYZ7: 'TTF Cal-27'); unknown codes are returned as they are."""
    c = str(code).strip().upper()
    return CODES.get(c, CODES.get(c.replace(" COMDTY", ""), code))


def _long(df: pd.DataFrame, date: str, name: str | list[str], price: str, unit: str | None, src: str) -> pd.DataFrame:
    names = df[name].astype(str).agg(" ".join, axis=1) if isinstance(name, list) else df[name].astype(str)
    out = pd.DataFrame({"date": df[date], "instrument": names, "price": df[price]})
    out["unit"] = df[unit].astype(str) if unit else "EUR/MWh"
    out["source"] = src
    return out


def _wide(df: pd.DataFrame, date: str, src: str) -> pd.DataFrame:
    value_cols = [c for c in df.columns if c != date]
    m = df.melt(id_vars=[date], value_vars=value_cols, var_name="instrument", value_name="price")
    return pd.DataFrame(
        {
            "date": m[date],
            "instrument": m["instrument"].astype(str),
            "price": m["price"],
            "unit": "EUR/MWh",
            "source": src,
        }
    )


def to_rows(df: pd.DataFrame, fmt: str) -> pd.DataFrame:
    """Vendor table -> rows (date, instrument, price, unit, source) before product checks."""
    if fmt == "eex":
        name = [
            c
            for c in (
                _col(df, "product"),
                _col(df, "delivery period", "maturity", "delivery"),
                _col(df, "load type", "load"),
            )
            if c
        ]
        return _long(
            df,
            _col(df, "trading date"),
            name,
            _col(df, "settlement price"),
            _col(df, "unit", "currency"),
            "EEX DataSource",
        )
    if fmt == "ice":
        name = [c for c in (_col(df, "contract", "commodity", "market"), _col(df, "strip", "contract month")) if c]
        return _long(
            df,
            _col(df, "trade date"),
            name,
            _col(df, "settlement price", "settle", "settlement"),
            _col(df, "unit", "currency"),
            "ICE Endex",
        )
    if fmt == "bloomberg":
        price = _col(df, "px settle", "px last")
        sec = _col(df, "security", "ticker")
        if price and sec:
            r = _long(df, _col(df, "date", "dates"), sec, price, None, "Bloomberg")
        else:
            r = _wide(df, _col(df, "dates", "date"), "Bloomberg")
        r["instrument"] = r["instrument"].map(_code_to_name)
        return r
    if fmt == "refinitiv":
        date = _col(df, "exchange date", "date", "timestamp")
        ric = _col(df, "ric", "instrument")
        if ric:
            r = _long(df, date, ric, _col(df, "settle", "close", "value"), None, "Refinitiv")
        else:
            r = _wide(df, date, "Refinitiv")
        r["instrument"] = r["instrument"].map(_code_to_name)
        return r
    if fmt == "hedgewise":
        src = _col(df, "source")
        r = _long(
            df, _col(df, "date", "datum"), _col(df, "product"), _col(df, "price", "prijs", "settlement"), None, ""
        )
        r["source"] = df[src].astype(str) if src else "file"
        return r
    raise ValueError("unknown export format: expected Refinitiv, Bloomberg, EEX DataSource or ICE Endex columns")


def parse_dates(col: pd.Series) -> pd.Series:
    """A date column: ISO (2026-12-02) as such; otherwise day-first (18.11.2026) or month-first (11/18/2026),
    whichever reads the whole column (ties: day-first, the European default)."""
    s = col.astype(str).str.strip()
    if s.str.match(r"^\d{4}-\d{2}-\d{2}").all():
        return pd.to_datetime(s.str[:10], format="%Y-%m-%d", errors="coerce")
    d1 = pd.to_datetime(s, dayfirst=True, errors="coerce", format="mixed")
    d2 = pd.to_datetime(s, dayfirst=False, errors="coerce", format="mixed")
    return d2 if d2.isna().sum() < d1.isna().sum() else d1


def _to_eur_mwh(price: float, unit: str) -> float | None:
    u = _norm(unit).replace(" ", "")
    if u in ("", "nan", "eurmwh", "eur", "mwh", "eurmw", "eurpermwh"):
        return price
    if u in ("ctkwh", "eurctkwh", "centkwh"):
        return price * 10
    return None  # GBP, pence/therm, USD, ...: not a TTF / Dutch power EUR price


def normalise(df: pd.DataFrame, name: str = "") -> tuple[pd.DataFrame, dict]:
    """Detect, map and check one export. Returns (rows in our format, report dict)."""
    fmt = detect_format(df)
    rows = to_rows(df, fmt)
    rows["price"] = ci.parse_numbers(rows["price"].tolist())
    rows["date"] = parse_dates(rows["date"])
    keep, rejected = [], []
    for r in rows.itertuples():
        pp = parse_product(r.instrument)
        eur = _to_eur_mwh(r.price, r.unit) if pd.notna(r.price) else None
        if pd.isna(r.date) or pp is None or eur is None or not np.isfinite(eur):
            why = (
                "no date"
                if pd.isna(r.date)
                else "not a calendar-year baseload TTF / Dutch power product"
                if pp is None
                else f"unit '{r.unit}' is not EUR/MWh or ct/kWh"
                if eur is None
                else "no price"
            )
            rejected.append((r.instrument, why))
            continue
        c, y = pp
        keep.append(
            {
                "date": r.date.normalize(),
                "product": f"{'TTF' if c == 'gas' else 'NL Base'} Cal-{y % 100:02d}",
                "price": round(float(eur), 4),
                "source": f"{r.source} {r.instrument}".strip(),
            }
        )
    out = pd.DataFrame(keep, columns=OUT_COLUMNS).drop_duplicates(["date", "product"], keep="last")
    rep = {
        "file": name,
        "format": fmt,
        "rows": len(rows),
        "kept": len(out),
        "rejected": pd.Series([w for _, w in rejected]).value_counts().to_dict() if rejected else {},
        "rejected_examples": sorted({i for i, _ in rejected})[:8],
        "products": product_report(out),
    }
    return out, rep


def product_report(out: pd.DataFrame) -> list[dict]:
    """Per product: first and last date, number of quotes, and gaps (more than 3 business days without a quote)."""
    rep = []
    for p, g in out.groupby("product"):
        d = pd.DatetimeIndex(sorted(g.date.unique()))
        gaps = []
        for a, b in zip(d[:-1], d[1:], strict=True):
            missing = len(pd.bdate_range(a, b)) - 2
            if missing > 3:
                gaps.append(f"{a.date()} -> {b.date()} ({missing} business days)")
        rep.append(
            {"product": p, "from": str(d.min().date()), "to": str(d.max().date()), "quotes": len(d), "gaps": gaps}
        )
    return rep


def read_any(path: str | Path) -> pd.DataFrame:
    """CSV (any separator, title rows above the header skipped) or Excel."""
    return ci.read_table(path)


def report_text(rep: dict) -> str:
    """The import report as plain text."""
    lines = [f"{rep['file']}: format {rep['format']}, {rep['rows']} rows, {rep['kept']} kept"]
    for p in rep["products"]:
        gaps = "; gaps: " + ", ".join(p["gaps"]) if p["gaps"] else "; no gaps"
        lines.append(f"  {p['product']:14s} {p['from']} .. {p['to']}, {p['quotes']} quotes{gaps}")
    for why, n in rep["rejected"].items():
        lines.append(f"  rejected {n}: {why}")
    if rep["rejected_examples"]:
        lines.append("  e.g. " + ", ".join(rep["rejected_examples"]))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> pd.DataFrame:
    """Import one or more exports, print the report, write data/forwards.csv (only with --force if it exists)."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+")
    ap.add_argument("--out", default=str(DATA / "forwards.csv"))
    ap.add_argument("--force", action="store_true", help="overwrite the output file")
    ap.add_argument("--dry-run", action="store_true", help="only print the report")
    args = ap.parse_args(argv)
    parts = []
    for f in args.files:
        out, rep = normalise(read_any(f), Path(f).name)
        print(report_text(rep))
        parts.append(out)
    allrows = pd.concat(parts, ignore_index=True).drop_duplicates(["date", "product"], keep="last")
    allrows = allrows.sort_values(["product", "date"])
    if args.dry_run:
        return allrows
    out_path = Path(args.out)
    if out_path.exists() and not args.force:
        raise SystemExit(f"{out_path} exists: use --force to overwrite (it is git-ignored: licensed data)")
    w = allrows.assign(date=allrows.date.dt.strftime("%Y-%m-%d"))
    w.to_csv(out_path, index=False)
    print(f"Wrote {len(w)} quotes to {out_path} (git-ignored: licensed data must not be committed or deployed)")
    return allrows


__all__ = ["detect_format", "to_rows", "normalise", "product_report", "report_text", "main"]
