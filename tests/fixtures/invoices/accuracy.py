"""Extraction accuracy per field on the fixture invoices (expected.json): python tests/fixtures/invoices/accuracy.py
writes ACCURACY.md next to this file; tests/test_invoice_fixtures.py asserts it."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))
from hedgewise import extract as xt  # noqa: E402

SCALARS = ("supplier", "period_start", "period_end", "contract_type", "contract_start", "contract_end", "fixed_on")


def _close(a: float | None, b: float, tol: float) -> bool:
    return a is not None and abs(a - b) <= tol


def check(name: str, exp: dict) -> list[dict]:
    """One row per expected field: field, expected, got, ok."""
    ex = xt.extract(HERE / name, name)
    rows = []
    for k in SCALARS:
        if k in exp:
            v = getattr(ex, k).value
            got = str(v.date()) if hasattr(v, "date") else v
            rows.append({"file": name, "field": k, "expected": exp[k], "got": got, "ok": got == exp[k]})
    for k, want in exp.get("totals", {}).items():
        got = ex.totals.get(k)
        rows.append({"file": name, "field": f"total {k}", "expected": want, "got": got, "ok": _close(got, want, 0.5)})
    months = {m["month"]: m for m in ex.monthly}
    for month, vals in exp.get("monthly", {}).items():
        for k, want in vals.items():
            got = months.get(month, {}).get(k)
            rows.append(
                {"file": name, "field": f"month {k}", "expected": want, "got": got, "ok": _close(got, want, 0.5)}
            )
    prices = xt.combined_prices(ex)
    for c, want in exp.get("prices_eur_mwh", {}).items():
        got = prices.get(c, {}).get("eur_mwh")
        rows.append(
            {"file": name, "field": f"price {c} (EUR/MWh)", "expected": want, "got": got, "ok": _close(got, want, 0.05)}
        )
    return rows


def accuracy() -> pd.DataFrame:
    """All checks for all fixtures."""
    exp = json.loads((HERE / "expected.json").read_text())
    return pd.DataFrame([r for name, e in exp.items() for r in check(name, e)])


def per_field(df: pd.DataFrame) -> pd.DataFrame:
    """Accuracy per field type (monthly values grouped)."""
    g = df.assign(kind=df.field.str.replace(r"^month .*", "monthly values", regex=True))
    return g.groupby("kind", sort=False).ok.agg(["sum", "count"]).assign(accuracy=lambda t: t["sum"] / t["count"])


if __name__ == "__main__":
    df = accuracy()
    pf = per_field(df)
    lines = [
        "# Extraction accuracy on the fixture invoices (rules engine)",
        "",
        "Five FAKE invoices in the layouts of publicly published Dutch bill explanations (see make_fixtures.py).",
        "",
        "| Field | Correct | Checked | Accuracy |",
        "|---|---|---|---|",
    ]
    lines += [f"| {k} | {int(r['sum'])} | {int(r['count'])} | {r['accuracy']:.0%} |" for k, r in pf.iterrows()]
    lines += ["", f"Overall: {df.ok.sum()} of {len(df)} fields correct ({df.ok.mean():.0%})."]
    bad = df[~df.ok]
    if len(bad):
        lines += ["", "Wrong or missing:", ""] + [
            f"- {r.file}: {r.field} expected {r.expected}, got {r.got}" for r in bad.itertuples()
        ]
    (HERE / "ACCURACY.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
