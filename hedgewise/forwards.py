"""
Real forward prices (e.g. an EEX / ICE Endex export from Refinitiv or Bloomberg).

Put the export at  data/forwards.csv  (CSV or .xlsx with the same name stem also works:
data/forwards.xlsx). Expected columns (names are detected, Dutch or English):
    date      the quote date (settlement date)
    product   e.g. "TTF Cal-27", "NL Base Cal-27", "Dutch Power Baseload Cal 2027", "TTF Gas CAL27"
    price     EUR/MWh (settlement / close); Dutch or English number format

    from forwards import forward_price
    forward_price("gas", "2026-11-20", 2027)     # latest TTF Cal-27 quote on or before 20 Nov 2026, or None

If no forwards file exists, HedgeWise keeps using its proxy (30-day spot, power seasonally adjusted, + 5%).
data/forwards_example.csv is FAKE data for the tests only - never copy it to data/forwards.csv.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

import pandas as pd

from ._types import DateLike
from .settings import DATA

FORWARDS_PATH = Path(os.environ.get("HEDGEWISE_FORWARDS", DATA / "forwards.csv"))  # env var: for testing

GAS_WORDS = ("ttf", "gas")
POWER_WORDS = ("power", "base", "stroom", "elec", "electricity", "nl b", "dutch b", "baseload")
_CAL = re.compile(r"cal(?:endar)?[\s\-_/]*'?(\d{4}|\d{2})\b|\by[\s\-]?(\d{2}|\d{4})\b", re.IGNORECASE)


def parse_product(name: str) -> tuple[str, int] | None:
    """('gas' | 'power', delivery year) for a calendar-year product, else None (months, quarters, peak...)."""
    s = str(name).lower()
    if re.search(r"\bpeak\b", s):  # peakload: not what a baseload buyer fixes
        return None
    if re.search(r"\b(q[1-4]|m\d{1,2}|month|maand|week|wk|day|dag|season|winter|summer)\b", s):
        return None  # only full calendar years
    m = _CAL.search(s)
    if not m:
        return None
    y = int(m.group(1) or m.group(2))
    year = y + 2000 if y < 100 else y
    if any(w in s for w in GAS_WORDS):
        return "gas", year
    if any(w in s for w in POWER_WORDS):
        return "power", year
    return None


def _find(cols: pd.Index, words: tuple[str, ...]) -> str | None:
    """First column whose (lower-case) name equals or starts with one of `words`."""
    low = {c: str(c).strip().lower() for c in cols}
    return next((c for c in cols if any(w == low[c] or low[c].startswith(w) for w in words)), None)


def read_forwards(path: str | Path) -> pd.DataFrame:
    """Tidy frame: date, product, commodity, delivery_year, price (calendar-year products only)."""
    path = Path(path)
    raw = (
        pd.read_excel(path)
        if path.suffix.lower() in (".xlsx", ".xls")
        else pd.read_csv(path, sep=None, engine="python", dtype=str)
    )
    dcol = _find(raw.columns, ("date", "datum", "trade date", "settlement date", "quote date"))
    pcol = _find(raw.columns, ("product", "contract", "instrument", "ric", "ticker", "name"))
    vcol = _find(raw.columns, ("price", "settle", "settlement", "close", "last", "prijs", "slot"))
    if not (dcol and pcol and vcol):
        raise ValueError(f"{path.name}: need date, product and price columns; found {list(raw.columns)}")
    from .company_input import parse_numbers

    df = pd.DataFrame(
        {
            "date": pd.to_datetime(raw[dcol], dayfirst=True, format="mixed", errors="coerce"),
            "product": raw[pcol].astype(str).str.strip(),
            "price": parse_numbers(raw[vcol].tolist()),
        }
    )
    parsed = df["product"].map(parse_product)
    df["commodity"] = parsed.map(lambda x: x[0] if x else None)
    df["delivery_year"] = parsed.map(lambda x: x[1] if x else None)
    df = df.dropna(subset=["date", "price", "commodity"])
    df["delivery_year"] = df["delivery_year"].astype(int)
    return df.sort_values("date").reset_index(drop=True)


def _resolve(path: str | Path | None) -> Path | None:
    """The forwards file to use (explicit path, else data/forwards.csv or .xlsx), or None."""
    p = Path(path) if path is not None else FORWARDS_PATH
    if p.exists():
        return p
    alt = p.with_suffix(".xlsx")
    return alt if path is None and alt.exists() else None


@lru_cache(maxsize=8)
def _load(path_str: str, mtime: float) -> pd.DataFrame:
    """Cached read; `mtime` is part of the cache key so an edited file is re-read."""
    return read_forwards(path_str)


def available(path: str | Path | None = None) -> bool:
    """True if a forward-price file is present."""
    return _resolve(path) is not None


def forward_quote(commodity: str, as_of: DateLike, delivery_year: int, path: str | Path | None = None) -> dict | None:
    """{'price', 'date', 'product'} of the latest quote on or before as_of, or None."""
    p = _resolve(path)
    if p is None:
        return None
    df = _load(str(p), p.stat().st_mtime)
    sel = df[(df.commodity == commodity) & (df.delivery_year == int(delivery_year)) & (df.date <= pd.Timestamp(as_of))]
    if sel.empty:
        return None
    row = sel.iloc[-1]
    return {"price": float(row.price), "date": row.date, "product": row["product"]}


def forward_price(commodity: str, as_of: DateLike, delivery_year: int, path: str | Path | None = None) -> float | None:
    """Latest forward price (EUR/MWh) for `commodity` ('gas' or 'power') and calendar `delivery_year`,
    quoted on or before `as_of`; None if there is no file or no such quote (strict: never looks ahead)."""
    q = forward_quote(commodity, as_of, delivery_year, path)
    return q["price"] if q else None


def main(argv: list[str] | None = None) -> None:
    """Command-line entry point."""
    import argparse

    ap = argparse.ArgumentParser(description="Check a forward-price export.")
    ap.add_argument("file", nargs="?", default=str(FORWARDS_PATH))
    a = ap.parse_args(argv)
    d = read_forwards(a.file)
    print(
        d.groupby(["commodity", "delivery_year"]).agg(
            quotes=("price", "size"), first=("date", "min"), last=("date", "max"), last_price=("price", "last")
        )
    )
