"""Output file names (kept free of heavy imports so the dashboard can name files without loading reportlab)."""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from ._types import DateLike
from .settings import OUT


def report_path(company_name: str, as_of: DateLike) -> Path:
    """output/report_<company-slug>_<as-of date>.pdf"""
    slug = re.sub(r"[^a-z0-9]+", "_", company_name.lower()).strip("_") or "company"
    return OUT / f"report_{slug}_{pd.Timestamp(as_of):%Y-%m-%d}.pdf"
