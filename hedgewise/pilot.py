"""One command per interviewed company:  python pilot.py data/private/<company>/ [--alias "Bedrijf A"]

Reads every PDF (invoices, annual statements, contracts) and CSV/Excel (bill exports, or a past-contracts table) in
the folder, shows the numbers for confirmation, runs the analysis (the HedgeWise ladder, honest bad case), the
stress test and the past-contracts audit, and writes into the SAME private folder:
    report_<name>_en.pdf, report_<name>_nl.pdf   the client report (page 2 = the audit, if past contracts were found)
    sanity_check.pdf                             one page for Janek and Bruno: what was read from where, confidence,
                                                 implausible values, missing months - check it BEFORE sending
    pilot_numbers.csv, pilot_contracts.csv       the numbers used; edit them and rerun to correct anything

Confirmation: the tables are printed and the run asks "Use these numbers?" (skip with --yes). If you edited
pilot_numbers.csv / pilot_contracts.csv, the next run uses your edits instead of reading the documents again.
Privacy: data/private/ is git-ignored and blocked by the pre-commit hook; --alias replaces the company name in every
output; PDFs are read locally unless --ai is given AND ANTHROPIC_API_KEY is set (only with the company's written
consent, CONSENT.md).
"""

from __future__ import annotations

import argparse
import re
from collections.abc import Callable
from pathlib import Path

import pandas as pd

import hedgewise as hw

from . import audit as au
from . import company_input as ci
from . import extract as xt
from .settings import DEFAULT_SEED

NUMBERS, CONTRACTS = "pilot_numbers.csv", "pilot_contracts.csv"
OWN_FILES = {NUMBERS, CONTRACTS}


def _is_contracts(df: pd.DataFrame) -> bool:
    cols = {str(c).strip().lower() for c in df.columns}
    return bool(cols & {"year", "jaar"}) and bool(cols & {"commodity", "product"}) and bool(cols & {"price", "prijs"})


def read_folder(folder: Path, consent: bool = False) -> dict:
    """Everything in the folder: extractions from PDFs, consumption tables and contract tables from CSV/Excel."""
    out = {"extractions": [], "consumption_tables": [], "contract_tables": [], "skipped": []}
    for f in sorted(folder.iterdir()):
        if f.name.startswith(".") or f.name in OWN_FILES or f.name.startswith(("report_", "sanity_check")):
            continue
        if f.suffix.lower() == ".pdf":
            out["extractions"].append(xt.extract(f, f.name, consent=consent))
        elif f.suffix.lower() in (".csv", ".txt", ".xlsx", ".xls"):
            t = ci.read_table(f)
            (out["contract_tables"] if _is_contracts(t) else out["consumption_tables"]).append((f.name, t))
        else:
            out["skipped"].append(f.name)
    return out


def numbers(folder: Path, found: dict, sector: str, name: str) -> tuple[ci.CompanyInput, pd.DataFrame, str]:
    """The consumption profile, the monthly table shown for confirmation, and where it came from. Priority: an
    edited pilot_numbers.csv > a bill export (CSV/Excel) > monthly values from PDFs > period totals from PDFs."""
    edited = folder / NUMBERS
    if edited.exists():
        t = pd.read_csv(edited)
        return xt.confirmed_company(t, name, sector), t, f"{NUMBERS} (your edits)"
    if found["consumption_tables"]:
        fname, t = found["consumption_tables"][0]
        res = ci.company_from_frame(t, None, name, sector)
        months = pd.DataFrame(
            {
                "month": [f"{m:02d}" for m in range(1, 13)],
                "gas_m3": res.company.gas_m3,
                "power_kwh": res.company.power_kwh,
                "source": fname,
            }
        )
        return res, months, fname
    m = xt.monthly_table(found["extractions"])
    if len(m):
        return xt.confirmed_company(m, name, sector), m, "PDF monthly values"
    tot = [ex for ex in found["extractions"] if ex.totals]
    if tot:
        ex = tot[0]
        days = (
            ((ex.period_end.value - ex.period_start.value).days + 1)
            if ex.period_start.value is not None and ex.period_end.value is not None
            else 365
        )
        f = 365 / days
        res = ci.from_yearly(
            ex.totals.get("gas_m3", 0) * f, "m3", ex.totals.get("power_kwh", 0) * f, "kWh", sector, name
        )
        if abs(f - 1) > 0.05:
            res.warnings.append(f"{ex.source}: totals over {days} days scaled to a year (x{f:.2f}).")
        t = pd.DataFrame(
            {
                "month": ["year"],
                "gas_m3": [ex.totals.get("gas_m3", 0) * f],
                "power_kwh": [ex.totals.get("power_kwh", 0) * f],
                "source": [ex.source],
                "quote": [ex.totals["quote"]],
            }
        )
        return res, t, f"PDF period totals ({ex.source})"
    raise ValueError("no consumption found: add bills (PDF) or a bill export (CSV/Excel), or fill in pilot_numbers.csv")


def contracts(folder: Path, found: dict) -> pd.DataFrame:
    """Past contracts for the audit: an edited pilot_contracts.csv > a contracts table > prices read from PDFs."""
    edited = folder / CONTRACTS
    if edited.exists():
        return pd.read_csv(edited)
    if found["contract_tables"]:
        return found["contract_tables"][0][1]
    return xt.contracts_table(found["extractions"]).drop(columns=["source", "quote"], errors="ignore")


def cross_check(found: dict, res: ci.CompanyInput) -> list[str]:
    """Documents with yearly totals that weren't used: compare them with the profile used (more than 10% apart is
    flagged: a different site or year, a missing register, or a typo)."""
    out = []
    used = {"gas_m3": sum(res.company.gas_m3), "power_kwh": sum(res.company.power_kwh)}
    for ex in found["extractions"]:
        if not ex.totals or ex.period_start.value is None or ex.period_end.value is None:
            continue
        days = (ex.period_end.value - ex.period_start.value).days + 1
        for k, label in (("gas_m3", "gas m3"), ("power_kwh", "power kWh")):
            if k in ex.totals and used[k]:
                yearly = ex.totals[k] * 365 / days
                if abs(yearly / used[k] - 1) > 0.10:
                    out.append(
                        f"{ex.source}: {label} {yearly:,.0f} per year, but the profile used has {used[k]:,.0f} "
                        f"({yearly / used[k] - 1:+.0%}). Which one is right?"
                    )
    return out


def alias_text(s: str, real: str | None, alias: str | None) -> str:
    """Replace the real company name by the alias (case-insensitive)."""
    if not alias or not real:
        return s
    return re.sub(re.escape(real), alias, s, flags=re.I)


def sanity_sheet(path: Path, info: dict) -> None:
    """One A4 page for the team: sources, engines, confidence, flags, months, the numbers used, the advice."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    from . import report as rp

    h = ParagraphStyle("h", fontName=rp.FONT_BOLD, fontSize=11, leading=14, spaceBefore=6, textColor=rp.BRAND)
    b = ParagraphStyle("b", fontName=rp.FONT, fontSize=8.5, leading=11)
    red = ParagraphStyle("r", parent=b, textColor=rp.colors.HexColor("#b8433a"))
    story = [
        Paragraph(
            f"Sanity check · {rp._esc(info['name'])} · internal, do not send",
            rp.ParagraphStyle("t", fontName=rp.FONT_BOLD, fontSize=14, leading=18),
        ),
        Spacer(1, 4),
    ]
    story.append(Paragraph("Documents read", h))
    for d in info["documents"]:
        story.append(Paragraph(rp._esc(d), b))
    story.append(Paragraph(f"Consumption used: {rp._esc(info['source'])}", h))
    story.append(Paragraph(rp._esc(info["volumes"]), b))
    story.append(Paragraph("Check these (flags, warnings, low confidence)", h))
    items = info["flags"] or ["Nothing flagged."]
    for f in items:
        story.append(Paragraph("• " + rp._esc(f), red if info["flags"] else b))
    story.append(Paragraph("Past contracts used for the audit", h))
    story.append(Paragraph(rp._esc(info["contracts"]), b))
    story.append(Paragraph("Result", h))
    story.append(Paragraph(rp._esc(info["result"]), b))
    story.append(Spacer(1, 6))
    story.append(
        Paragraph(
            "Before sending: compare the yearly totals with the company's own figures, check every "
            "flag above, and check the share of any contract (set to 100% when the document didn't "
            "say). Edit pilot_numbers.csv / pilot_contracts.csv and rerun to correct.",
            b,
        )
    )
    doc = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        leftMargin=1.8 * cm,
        rightMargin=1.8 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1.5 * cm,
        initialFontName=rp.FONT,
        invariant=True,
    )
    doc.build(story)


def run(
    folder: Path,
    alias: str | None = None,
    name: str | None = None,
    sector: str = "food processing",
    yes: bool = False,
    consent: bool = False,
    n_paths: int = 10_000,
    ask: Callable[[str], str] = input,
) -> dict:
    """The whole pilot for one company folder; returns what was written."""
    folder = Path(folder)
    real = name or folder.name
    shown = alias or real
    found = read_folder(folder, consent)
    res, table, source = numbers(folder, found, sector, shown)
    ctr = contracts(folder, found)
    flags = list(xt.validate(found["extractions"])) + res.warnings + cross_check(found, res)
    for ex in found["extractions"]:
        flags += [f"{ex.source}: {w}" for w in ex.warnings]
        low = [k for k in ("supplier", "period_start", "contract_type") if 0 < getattr(ex, k).confidence < 0.7]
        if low:
            flags.append(f"{ex.source}: low confidence on {', '.join(low)}")
    pd.set_option("display.width", 200)
    print(f"\n== {shown}: numbers to confirm (source: {source})")
    print(table.drop(columns=["quote"], errors="ignore").to_string(index=False))
    if len(ctr):
        print("\nPast contracts:\n" + ctr.to_string(index=False))
    for f in flags:
        print("FLAG:", alias_text(f, real, alias))
    table.drop(columns=["quote"], errors="ignore").to_csv(
        folder / NUMBERS, index=False
    ) if source != f"{NUMBERS} (your edits)" else None
    if len(ctr) and not (folder / CONTRACTS).exists():
        ctr.to_csv(folder / CONTRACTS, index=False)
    if not yes and ask(
        "\nUse these numbers? Edit pilot_numbers.csv / pilot_contracts.csv first if not. [y/N] "
    ).strip().lower() not in ("y", "yes", "j", "ja"):
        print("Stopped. Edit the CSV files in the folder and run again.")
        return {"confirmed": False}
    company = res.company
    df = hw.load_prices()
    m = hw.fit(df)
    a = hw.recommend(m, company, df, None, n_paths, DEFAULT_SEED)
    stress = hw.stress_test(a, df)
    audit_res = None
    if len(ctr):
        try:
            cs = au.read_contracts(
                ctr.rename(columns={"share_%": "share"}).assign(
                    share=lambda d: [None if pd.isna(v) else f"{v}%" for v in d["share"]]
                )
                if "share_%" in ctr
                else ctr
            )
            last = df.index.max().year - 1
            cs = [c for c in cs if au.FIRST_YEAR <= c.year <= last]
            if cs:
                audit_res = au.run_audit(cs, company, df)
            else:
                flags.append(f"Past contracts are outside {au.FIRST_YEAR}-{last}: no audit page.")
        except ValueError as e:
            flags.append(f"Past contracts not usable for the audit: {e}")
    from . import report

    slug = re.sub(r"[^a-z0-9]+", "_", shown.lower()).strip("_")
    files = []
    for lang in ("en", "nl"):
        p = folder / f"report_{slug}_{lang}.pdf"
        report.make_report(a, str(p), lang, stress, audit=audit_res)
        files.append(p)
    bad, src = hw.bad_case(a)
    r = a.recommendation
    docs = (
        [
            f"{alias_text(ex.source, real, alias)}: engine {ex.engine}, supplier {ex.supplier.value or '?'}, "
            f"{len(ex.monthly)} months, totals {'yes' if ex.totals else 'no'}, {len(ex.prices)} prices"
            for ex in found["extractions"]
        ]
        + [f"{alias_text(n, real, alias)}: consumption table" for n, _ in found["consumption_tables"]]
        + [f"{alias_text(n, real, alias)}: contracts table" for n, _ in found["contract_tables"]]
        + [f"{n}: not read (type)" for n in found["skipped"]]
    )
    info = {
        "name": shown,
        "documents": docs or ["(no documents)"],
        "source": alias_text(source, real, alias),
        "volumes": f"{sum(company.gas_m3):,.0f} m3 gas and {sum(company.power_kwh) / 1e3:,.0f} MWh power per year",
        "flags": [alias_text(f, real, alias) for f in flags],
        "contracts": "none" if not len(ctr) else f"{len(ctr)} rows; audit page: {'yes' if audit_res else 'no'}",
        "result": f"HedgeWise ladder: fix {r['hedge_gas']:.0%} (model target {r.get('model_target', 0):.0%}); "
        f"expected bill all variable EUR {a.table.loc['All variable', 'expected'] / 1e6:.2f}m; bad case with "
        f"the plan EUR {bad / 1e6:.2f}m ({src}).",
    }
    sanity_sheet(folder / "sanity_check.pdf", info)
    files.append(folder / "sanity_check.pdf")
    print("\nWrote:", ", ".join(str(f.name) for f in files), f"in {folder}")
    return {"confirmed": True, "files": files, "analysis": a, "audit": audit_res, "flags": flags}


def main(argv: list[str] | None = None) -> dict:
    """Command line."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder")
    ap.add_argument("--alias", help="name to show instead of the company's (all outputs)")
    ap.add_argument("--name", help="the company's real name (default: the folder name); replaced by --alias")
    ap.add_argument("--sector", default="food processing", choices=ci.SECTORS)
    ap.add_argument("--yes", action="store_true", help="don't ask for confirmation")
    ap.add_argument("--ai", action="store_true", help="send PDFs to the AI service (needs the key AND written consent)")
    args = ap.parse_args(argv)
    return run(Path(args.folder), args.alias, args.name, args.sector, args.yes, args.ai)


__all__ = ["read_folder", "numbers", "contracts", "run", "sanity_sheet", "main"]
