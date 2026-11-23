"""
HedgeWise one-page risk report (PDF, A4) for a CFO with no finance background.

    python report.py --company data/company_example.csv --lang nl
    python report.py --yearly-gas 300000 --yearly-power 1500 --sector offices --name "Kantoor Venlo" --lang en
    -> output/report_<name>_<date>.pdf

In code:  make_report(analysis, path_or_buffer, lang="en" | "nl", stress=None)
All text lives in TEXT (one dict per language).
"""

from __future__ import annotations

import argparse
import io
import json
from pathlib import Path

import numpy as np
import pandas as pd
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

import hedgewise as hw
from hedgewise import provenance
from hedgewise._types import PathOrBuffer
from hedgewise.naming import report_path  # re-exported: report.report_path() keeps working
from hedgewise.settings import DEFAULT_PATHS, DEFAULT_RISK_AVERSION, DEFAULT_SEED

# ---- Fonts: embed DejaVu Sans (ships with matplotlib, no download). The built-in Helvetica has no glyph for "³"
# (m³ rendered as "m "), and an embedded TTF also looks the same in every PDF viewer.
FONT, FONT_BOLD = "DejaVuSans", "DejaVuSans-Bold"


def _register_fonts() -> None:
    """Register DejaVu Sans (regular/bold/italic) with reportlab once."""
    import matplotlib
    from reportlab.lib.fonts import addMapping
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    if FONT in pdfmetrics.getRegisteredFontNames():
        return
    d = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
    files = {
        FONT: "DejaVuSans.ttf",
        FONT_BOLD: "DejaVuSans-Bold.ttf",
        "DejaVuSans-Oblique": "DejaVuSans-Oblique.ttf",
        "DejaVuSans-BoldOblique": "DejaVuSans-BoldOblique.ttf",
    }
    for name, fn in files.items():
        pdfmetrics.registerFont(TTFont(name, str(d / fn)))
    # so that <b> / <i> inside Paragraphs use the matching DejaVu faces
    for bold, italic, name in (
        (0, 0, FONT),
        (1, 0, FONT_BOLD),
        (0, 1, "DejaVuSans-Oblique"),
        (1, 1, "DejaVuSans-BoldOblique"),
    ):
        addMapping(FONT, bold, italic, name)


_register_fonts()

BRAND = colors.HexColor("#1b9e5a")
INK = colors.HexColor("#1f2937")
MUTED = colors.HexColor("#6b7280")
SOFT = colors.HexColor("#eef7f1")
RED_SOFT = colors.HexColor("#fdf0ee")

from .texts import NL_MONTHS, TEXT, _num, money, pct  # noqa: E402,F401 (re-exported)


def date_str(d: pd.Timestamp, lang: str) -> str:
    """'23 Nov 2026' (en) / '23 november 2026' (nl)."""
    d = pd.Timestamp(d)
    return f"{d.day} {d:%b %Y}" if lang == "en" else f"{d.day} {NL_MONTHS[d.month - 1]} {d.year}"


def tradeoff_per_10pct(a: hw.Analysis) -> tuple[float, float]:
    """(extra expected cost, bad-case reduction) per extra 10% fixed, averaged over 0-100% (as in plot_frontier)."""
    r = a.recommendation
    f = a.frontier
    f = f[(f.execution == r["execution"]) & np.isclose(f.hedge_gas, f.hedge_power)].sort_values("hedge_gas")
    lo, hi = f.iloc[0], f.iloc[-1]
    return float((hi.expected - lo.expected) / 10), float((lo.es95 - hi.es95) / 10)


# --------------------------------------------------------------------------------------------
# The report
# --------------------------------------------------------------------------------------------
def make_report(
    a: hw.Analysis,
    path: PathOrBuffer,
    lang: str = "en",
    stress: dict | None = None,
    today: pd.Timestamp | None = None,
    audit: object | None = None,
    green: pd.DataFrame | None = None,
) -> None:
    """Write the one-page PDF to `path` (file path or binary buffer).
    `stress` = output of hw.stress_test(a) (optional, adds the stress chart); `audit` = an audit.AuditResult
    (optional, adds a second page with the past-contracts comparison); `green` = green.evaluate()'s table (optional,
    adds one line under the recommendation)."""
    if lang not in TEXT:
        raise ValueError(f"lang must be one of {list(TEXT)}")
    T = TEXT[lang]
    today = pd.Timestamp(today) if today is not None else pd.Timestamp.today().normalize()
    c, r, t = a.company, a.recommendation, a.table
    var_row, hw_row = t.loc["All variable"], t.iloc[-1]
    n_paths = len(next(iter(a.costs.values())))

    ss = {
        "title": ParagraphStyle("title", fontName=FONT_BOLD, fontSize=17, leading=21, textColor=INK, spaceAfter=3),
        "sub": ParagraphStyle("sub", fontName=FONT, fontSize=8.5, leading=11, textColor=MUTED),
        "h": ParagraphStyle(
            "h", fontName=FONT_BOLD, fontSize=11, leading=14, textColor=BRAND, spaceBefore=5, spaceAfter=2
        ),
        "body": ParagraphStyle("body", fontName=FONT, fontSize=9, leading=12.5, textColor=INK),
        "small": ParagraphStyle("small", fontName=FONT, fontSize=8, leading=10.5, textColor=MUTED),
        "big": ParagraphStyle("big", fontName=FONT_BOLD, fontSize=18, leading=21, textColor=INK),
        "lbl": ParagraphStyle("lbl", fontName=FONT, fontSize=9.5, leading=12, textColor=INK),
        "rec": ParagraphStyle("rec", fontName=FONT, fontSize=10.5, leading=14, textColor=INK),
        "foot": ParagraphStyle("foot", fontName=FONT, fontSize=7, leading=9, textColor=MUTED, alignment=TA_LEFT),
    }
    W = A4[0] - 3.6 * cm
    prov = provenance.one_line()  # config hash, data hash and date range: printed in the footer
    story = []
    story.append(Paragraph(_esc(c.name), ss["title"]))  # the report title is already in the green header bar
    delivery = f"{a.delivery_start.year}"
    story.append(
        Paragraph(
            T["prepared"].format(today=date_str(today, lang), as_of=date_str(a.model.as_of, lang), delivery=delivery),
            ss["sub"],
        )
    )

    # consumption
    story.append(Paragraph(T["consumption_h"], ss["h"]))
    gas_m3 = sum(c.gas_m3)
    line = T["consumption"].format(
        gas_m3=_num(gas_m3, lang), gas_mwh=_num(gas_m3 * hw.MWH_PER_M3, lang), power=_num(sum(c.power_kwh) / 1000, lang)
    )
    if c.solar_kwp:
        line += T["solar"].format(kwp=_num(c.solar_kwp, lang))
    story.append(Paragraph(line, ss["body"]))

    # the bill if variable
    story.append(Paragraph(T["bill_h"], ss["h"]))
    cells = [
        [Paragraph(T["expected_lbl"], ss["lbl"]), Paragraph(T["bad_lbl"], ss["lbl"])],
        [
            Paragraph(money(var_row.expected, lang), ss["big"]),
            Paragraph(f"<font color='#b8433a'>{money(var_row.es95, lang)}</font>", ss["big"]),
        ],
    ]
    tb = Table(cells, colWidths=[W / 2, W / 2])
    tb.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), FONT),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f3f4f6")),
                ("BACKGROUND", (1, 0), (1, -1), RED_SOFT),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LINEAFTER", (0, 0), (0, -1), 3, colors.white),
            ]
        )
    )
    story.append(tb)
    story.append(Spacer(1, 2))
    story.append(Paragraph(T["bill_note"].format(n=_num(n_paths, lang)), ss["small"]))

    # frontier chart
    story.append(Paragraph(T["chart_h"], ss["h"]))
    buf = io.BytesIO()
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(8.2, 2.9))
    hw.plot_frontier(a, ax, text=T["chart"], show_tradeoff=False)
    ax.set_title("")
    fig.tight_layout()
    fig.savefig(buf, format="png", dpi=200)
    plt.close(fig)
    buf.seek(0)
    story.append(Image(buf, width=W, height=W * 2.9 / 8.2))
    story.append(Paragraph(T["chart_note"], ss["small"]))
    story.append(Paragraph(hw.frontier_scale_sentence(a, lang), ss["body"]))

    # recommendation
    story.append(Paragraph(T["rec_h"], ss["h"]))
    k = len(r["tranches"])
    if r["execution"] == "now" or k == 1:
        rec_txt = T["rec_now"].format(h=pct(r["hedge_gas"]))
    else:
        rec_txt = T["rec"].format(
            h=pct(r["hedge_gas"]),
            k=k,
            m=pct(r["buy_now_gas"]),
            tranche_word=T["tranche_many"] if k > 1 else T["tranche_one"],
        )
    d_e, d_es = tradeoff_per_10pct(a)
    rec_parts = [
        Paragraph(rec_txt, ss["rec"]),
        Spacer(1, 3),
        Paragraph(
            T["rec_effect"].format(
                e=money(hw_row.expected, lang), es=money(hw_row.es95, lang), es0=money(var_row.es95, lang)
            ),
            ss["body"],
        ),
        Paragraph(T["tradeoff"].format(a=money(d_e, lang), b=money(d_es, lang)), ss["body"]),
    ]
    if green is not None and len(green):
        from . import green as gr

        rec_parts.append(Paragraph("<b>" + T["green"] + "</b> " + _esc(gr.report_line(green, lang)), ss["body"]))
    box = Table([[rec_parts]], colWidths=[W])
    box.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), FONT),
                ("BACKGROUND", (0, 0), (-1, -1), SOFT),
                ("LINEBEFORE", (0, 0), (0, -1), 4, BRAND),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
            ]
        )
    )
    story.append(box)

    if stress:  # small stress chart in the bottom part of the page
        story.append(Paragraph(T["stress_h"], ss["h"]))
        sbuf = io.BytesIO()
        sh = 2.45 if green is None else 2.0  # the green-investments line takes the room of a little chart height
        fig, ax = plt.subplots(figsize=(8.2, sh))
        hw.plot_stress(stress, ax=ax, lang=lang, compact=True)
        fig.tight_layout(pad=0.4)
        fig.savefig(sbuf, format="png", dpi=200)
        plt.close(fig)
        sbuf.seek(0)
        story.append(Image(sbuf, width=W, height=W * sh / 8.2))
        story.append(Paragraph(T["stress_note"], ss["small"]))

    if audit is not None:
        story += _audit_page(audit, lang, T, ss, W)

    def _page(canv: Canvas, doc: SimpleDocTemplate) -> None:
        canv.saveState()
        canv.setFillColor(BRAND)
        canv.rect(0, A4[1] - 1.1 * cm, A4[0], 1.1 * cm, fill=1, stroke=0)
        canv.setFillColor(colors.white)
        canv.setFont(FONT_BOLD, 13)
        canv.drawString(1.8 * cm, A4[1] - 0.75 * cm, "HedgeWise")
        canv.setFont(FONT, 8.5)
        canv.drawRightString(A4[0] - 1.8 * cm, A4[1] - 0.72 * cm, T["doc_title"])
        src = r.get("price_source", {})
        real = [v.split(" quoted")[0].replace("forward ", "") for v in src.values() if v.startswith("forward")]
        fixed = T["fixed_real"].format(products=", ".join(real)) if real else T["fixed_proxy"]
        foot = Paragraph(T["footer"].format(fixed=fixed) + f"<br/>{prov}", ss["foot"])
        _, fh = foot.wrap(W, 3 * cm)
        canv.setStrokeColor(colors.HexColor("#d1d5db"))
        canv.line(1.8 * cm, 1.2 * cm + fh + 4, A4[0] - 1.8 * cm, 1.2 * cm + fh + 4)
        foot.drawOn(canv, 1.8 * cm, 1.2 * cm)
        canv.restoreState()

    _, foot_h = Paragraph(T["footer"].format(fixed=T["fixed_proxy"]) + f"<br/>{prov}", ss["foot"]).wrap(W, 5 * cm)
    doc = SimpleDocTemplate(
        path,
        pagesize=A4,
        leftMargin=1.8 * cm,
        rightMargin=1.8 * cm,
        topMargin=1.7 * cm,
        bottomMargin=1.2 * cm + foot_h + 0.45 * cm,
        title=f"HedgeWise - {c.name}",
        # every page starts in DejaVu: the default start font (Helvetica) would otherwise be referenced in the PDF
        initialFontName=FONT,
        subject="HedgeWise provenance: " + json.dumps(provenance.current()),
        author="HedgeWise",
    )
    doc.build(story, onFirstPage=_page, onLaterPages=_page)
    pages = 1 if audit is None else 2
    if doc.page != pages:
        raise RuntimeError(f"Report has {doc.page} pages instead of {pages}; shorten the text.")


def _audit_page(r: object, lang: str, T: dict, ss: dict, W: float) -> list:
    """Page 2: the procurement audit (table with totals and worst year, the summary sentence, the chart)."""
    from . import audit as au

    t = r.table
    span = f"{r.years[0]}-{r.years[-1]}" if len(r.years) > 1 else str(r.years[0])
    out = [PageBreak(), Paragraph(T["audit_h"], ss["h"])]
    out.append(Paragraph(T["audit_intro"].format(n=len(r.contracts), span=span), ss["body"]))
    out.append(Spacer(1, 5))
    hdr = [""] + [au.label(c, lang) for c in t.columns]
    rows = [[str(y)] + [_num(v / 1e3, lang) for v in t.loc[y]] for y in t.index]
    rows.append([T["audit_total"]] + [_num(v / 1e3, lang) for v in t.sum()])
    rows.append([T["audit_worst"]] + [_num(v / 1e3, lang) for v in t.max()])
    cell = ParagraphStyle("cell", fontName=FONT, fontSize=7, leading=8.5, textColor=INK)
    data = [[Paragraph(_esc(h), cell) for h in hdr]] + rows
    cw = [W * 0.1] + [W * 0.9 / len(t.columns)] * len(t.columns)
    tb = Table(data, colWidths=cw)
    tb.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), FONT),
                ("FONTSIZE", (0, 1), (-1, -1), 8.5),
                ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, 0), "BOTTOM"),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f3f4f6")),
                ("BACKGROUND", (1, 1), (1, -1), SOFT),  # the company's own column
                ("FONTNAME", (0, -2), (-1, -1), FONT_BOLD),
                ("LINEABOVE", (0, -2), (-1, -2), 0.6, colors.HexColor("#9ca3af")),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    out += [Paragraph("€ k" if lang == "en" else "€ duizend", ss["small"]), tb, Spacer(1, 6)]
    out.append(Paragraph(_esc(au.sentence(r, lang)), ss["rec"]))
    buf = io.BytesIO()
    au.plot_audit(r, buf, lang)
    buf.seek(0)
    out += [Spacer(1, 6), Image(buf, width=W, height=W * 4.6 / 11.5), Paragraph(T["audit_note"], ss["small"])]
    return out


def _esc(s: str) -> str:
    """Escape &, <, > for reportlab paragraphs."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def main(argv: list[str] | None = None) -> Path:
    """Command line: build the model, then write the PDF; returns its path."""
    from . import company_input

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    company_input.add_cli_args(ap)
    ap.add_argument("--lang", default="en", choices=list(TEXT))
    ap.add_argument("--as-of", help="decision date YYYY-MM-DD (default: last date with data)")
    ap.add_argument("--risk-aversion", type=float, default=DEFAULT_RISK_AVERSION)
    ap.add_argument("--budget", help="EUR or 'auto'")
    ap.add_argument("--paths", type=int, default=DEFAULT_PATHS)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--solar-kwp", type=float, default=0.0)
    ap.add_argument("--green-solar-kwp", type=float, default=0.0, help="green investment: rooftop solar (kWp)")
    ap.add_argument("--green-battery-kwh", type=float, default=0.0, help="green investment: battery (kWh)")
    ap.add_argument("--green-ppa-share", type=float, default=0.0, help="green investment: PPA share of power (0-1)")
    ap.add_argument("--audit", help="past contracts (CSV/Excel, see data/audit_example.csv): adds page 2")
    ap.add_argument("--out", help="output PDF path")
    args = ap.parse_args(argv)
    company = company_input.company_from_args(args)
    company.solar_kwp = args.solar_kwp
    df = hw.load_prices()
    m = hw.fit(df, args.as_of)
    a = hw.analyse(m, company, None, args.paths, args.seed, args.risk_aversion, budget=args.budget)
    stress = hw.stress_test(a, df) if hasattr(hw, "stress_test") else None
    out = Path(args.out) if args.out else report_path(company.name, m.as_of)
    out.parent.mkdir(exist_ok=True)
    audit = None
    if args.audit:
        from . import audit as au

        audit = au.run_audit(au.read_contracts(args.audit), company, df)
    from . import green as gr

    inv = gr.Investments(
        solar_kwp=args.green_solar_kwp, battery_kwh=args.green_battery_kwh, ppa_share=args.green_ppa_share
    )
    green = gr.evaluate(a, inv)[0] if inv.any() else None
    make_report(a, str(out), args.lang, stress, audit=audit, green=green)
    print(f"Report written to {out}")
    return out
