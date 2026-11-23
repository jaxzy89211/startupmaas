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
import re
from pathlib import Path

import numpy as np
import pandas as pd
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

import hedgewise_model as hw

# ---- Fonts: embed DejaVu Sans (ships with matplotlib, no download). The built-in Helvetica has no glyph for "³"
# (m³ rendered as "m "), and an embedded TTF also looks the same in every PDF viewer.
FONT, FONT_BOLD = "DejaVuSans", "DejaVuSans-Bold"


def _register_fonts() -> None:
    import matplotlib
    from reportlab.lib.fonts import addMapping
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    if FONT in pdfmetrics.getRegisteredFontNames():
        return
    d = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
    files = {FONT: "DejaVuSans.ttf", FONT_BOLD: "DejaVuSans-Bold.ttf",
             "DejaVuSans-Oblique": "DejaVuSans-Oblique.ttf", "DejaVuSans-BoldOblique": "DejaVuSans-BoldOblique.ttf"}
    for name, fn in files.items():
        pdfmetrics.registerFont(TTFont(name, str(d / fn)))
    # so that <b> / <i> inside Paragraphs use the matching DejaVu faces
    for bold, italic, name in ((0, 0, FONT), (1, 0, FONT_BOLD), (0, 1, "DejaVuSans-Oblique"),
                               (1, 1, "DejaVuSans-BoldOblique")):
        addMapping(FONT, bold, italic, name)


_register_fonts()

BRAND = colors.HexColor("#1b9e5a")
INK = colors.HexColor("#1f2937")
MUTED = colors.HexColor("#6b7280")
SOFT = colors.HexColor("#eef7f1")
RED_SOFT = colors.HexColor("#fdf0ee")

TEXT = {
    "en": {
        "doc_title": "Energy price risk report",
        "prepared": "Prepared {today} · market prices up to {as_of} · next year = {delivery}",
        "consumption_h": "Your consumption",
        "consumption": "Gas: <b>{gas_m3} m³</b> per year ({gas_mwh} MWh) · Electricity: <b>{power} MWh</b> per year",
        "solar": " · Rooftop solar: {kwp} kWp",
        "bill_h": "Your bill next year if you stay variable",
        "expected_lbl": "Expected",
        "bad_lbl": "Bad case <font size=8>(average of the worst 5% of scenarios)</font>",
        "bill_note": "Gas and electricity commodity cost only, based on {n} simulated price scenarios.",
        "chart_h": "How much to fix: every option at a glance",
        "chart_note": "Each dot = a share of next year's energy fixed now. Left = a milder bad year; "
                      "up = dearer on average. Star = our advice.",
        "rec_h": "Our recommendation",
        "rec": "Fix <b>{h}</b> of next year's gas and electricity, spread over <b>{k} monthly {tranche_word}</b>; "
               "start with <b>{m}</b> this month.",
        "rec_now": "Fix <b>{h}</b> of next year's gas and electricity, all of it this month.",
        "tranche_one": "tranche", "tranche_many": "tranches",
        "rec_effect": "With this plan: expected bill {e}, bad case {es} (instead of {es0}).",
        "tradeoff": "Each extra 10% fixed costs about <b>{a}</b> on average and removes about <b>{b}</b> "
                    "from the bad case.",
        "stress_h": "What if 2022 happens again?",
        "stress_sc": "<b>{label}</b> (gas avg {gas}, power {power} per MWh): staying variable {var}; "
                     "with our recommendation {hw}.",
        "stress_note": "Historical stress scenarios, not forecasts. Left: 2022 monthly prices as they were. Right: "
                       "today's fixed price × (2022 avg ÷ 2021 avg), 2022 monthly pattern. Fixed share at today's price.",
        "footer": "<b>HedgeWise</b> · Assumptions: commodity cost only (no network tariffs, energy tax or supplier "
                  "margin); {fixed}; volumes are "
                  "taken as known. Scenarios come from a statistical model of past prices and can miss events "
                  "that never happened before. This is decision support, not financial advice.",
        "fixed_proxy": "the fixed price is a proxy for real forward prices (recent market price + 5%)",
        "fixed_real": "fixed prices are real forward prices ({products})",
        "chart": {},
    },
    "nl": {
        "doc_title": "Rapport energieprijsrisico",
        "prepared": "Opgesteld {today} · marktprijzen t/m {as_of} · volgend jaar = {delivery}",
        "consumption_h": "Uw verbruik",
        "consumption": "Gas: <b>{gas_m3} m³</b> per jaar ({gas_mwh} MWh) · Elektriciteit: <b>{power} MWh</b> per jaar",
        "solar": " · Zonnepanelen: {kwp} kWp",
        "bill_h": "Uw rekening volgend jaar als u variabel blijft",
        "expected_lbl": "Verwacht",
        "bad_lbl": "Slecht scenario <font size=8>(gem. van de 5% duurste scenario's)</font>",
        "bill_note": "Alleen de kosten van gas en elektriciteit zelf, op basis van {n} gesimuleerde prijsscenario's.",
        "chart_h": "Hoeveel vastzetten: alle opties in één oogopslag",
        "chart_note": "Elk punt = een deel van volgend jaar dat u nu vastzet. Links = een milder slecht jaar; "
                      "hoger = gemiddeld duurder. Ster = ons advies.",
        "rec_h": "Ons advies",
        "rec": "Zet <b>{h}</b> van het gas en de elektriciteit voor volgend jaar vast, verdeeld over "
               "<b>{k} maandelijkse {tranche_word}</b>; begin deze maand met <b>{m}</b>.",
        "rec_now": "Zet <b>{h}</b> van het gas en de elektriciteit voor volgend jaar vast, alles deze maand.",
        "tranche_one": "aankoop", "tranche_many": "aankopen",
        "rec_effect": "Met dit plan: verwachte rekening {e}, slecht scenario {es} (in plaats van {es0}).",
        "tradeoff": "Elke extra 10% vastzetten kost gemiddeld ongeveer <b>{a}</b> en haalt ongeveer <b>{b}</b> "
                    "van het slechte scenario af.",
        "stress_h": "Wat als 2022 zich herhaalt?",
        "stress_sc": "<b>{label}</b> (gas gem. {gas}, stroom {power} per MWh): variabel blijven {var}; "
                     "met ons advies {hw}.",
        "stress_note": "Historische stressscenario's, geen voorspellingen. Links: maandprijzen 2022 zoals ze waren. "
                       "Rechts: vaste prijs nu × (gem. 2022 ÷ gem. 2021), maandpatroon 2022. Vast deel tegen de prijs van nu.",
        "footer": "<b>HedgeWise</b> · Aannames: alleen de kosten van de energie zelf (geen netbeheerkosten, "
                  "energiebelasting of leveranciersmarge); {fixed}; het verbruik wordt als bekend aangenomen. De scenario's komen uit een "
                  "statistisch model van prijzen uit het verleden en kunnen gebeurtenissen missen die nog nooit "
                  "zijn voorgekomen. Dit is beslisondersteuning, geen financieel advies.",
        "fixed_proxy": "de vaste prijs is een benadering van echte termijnprijzen (recente marktprijs + 5%)",
        "fixed_real": "de vaste prijzen zijn echte termijnprijzen ({products})",
        "chart": {
            "title": "Kies uw afweging: deel van volgend jaar vastgezet",
            "xlabel": "Slecht scenario: gemiddelde van de 5% duurste scenario's (€ mln)",
            "ylabel": "Verwachte rekening (€ mln)",
            "pick": "HedgeWise-advies: {h} vastzetten (risicoaversie {g})",
            "pick_budget": "HedgeWise-advies: {h} vastzetten (budget)",
            "decimal": ",",
        },
    },
}
TEXT["en"]["chart"] = {"xlabel": "Bad case: average of the worst 5% of scenarios (€ m)",
                       "ylabel": "Expected bill (€ m)"}
NL_MONTHS = ["januari", "februari", "maart", "april", "mei", "juni", "juli", "augustus", "september",
             "oktober", "november", "december"]


# --------------------------------------------------------------------------------------------
# Number formatting per language
# --------------------------------------------------------------------------------------------
def _num(x: float, lang: str, decimals: int = 0) -> str:
    s = f"{x:,.{decimals}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".") if lang == "nl" else s


def money(x: float, lang: str) -> str:
    """€2.07m / € 2,07 mln for millions; €25k / € 25.000 below."""
    if abs(x) >= 1e6:
        return f"€{_num(x / 1e6, lang, 2)}m" if lang == "en" else f"€\u00a0{_num(x / 1e6, lang, 2)}\u00a0mln"
    if lang == "en":
        return f"€{_num(x / 1e3, lang, 0)}k"
    return f"€\u00a0{_num(round(x, -3), lang, 0)}"


def _eur0(x: float, lang: str) -> str:
    return f"€{x:,.0f}" if lang == "en" else f"€\u00a0{_num(x, lang)}"


def pct(x: float) -> str:
    return f"{x:.0%}"


def date_str(d: pd.Timestamp, lang: str) -> str:
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
def make_report(a: hw.Analysis, path, lang: str = "en", stress: dict | None = None,
                today: pd.Timestamp | None = None) -> None:
    """Write the one-page PDF to `path` (file path or binary buffer).
    `stress` = output of hw.stress_test(a) (optional, adds one line)."""
    if lang not in TEXT:
        raise ValueError(f"lang must be one of {list(TEXT)}")
    T = TEXT[lang]
    today = pd.Timestamp(today) if today is not None else pd.Timestamp.today().normalize()
    c, r, t = a.company, a.recommendation, a.table
    var_row, hw_row = t.loc["All variable"], t.iloc[-1]
    n_paths = len(next(iter(a.costs.values())))

    ss = {
        "title": ParagraphStyle("title", fontName=FONT_BOLD, fontSize=17, leading=21, textColor=INK,
                                spaceAfter=3),
        "sub": ParagraphStyle("sub", fontName=FONT, fontSize=8.5, leading=11, textColor=MUTED),
        "h": ParagraphStyle("h", fontName=FONT_BOLD, fontSize=11, leading=14, textColor=BRAND,
                            spaceBefore=5, spaceAfter=2),
        "body": ParagraphStyle("body", fontName=FONT, fontSize=9, leading=12.5, textColor=INK),
        "small": ParagraphStyle("small", fontName=FONT, fontSize=8, leading=10.5, textColor=MUTED),
        "big": ParagraphStyle("big", fontName=FONT_BOLD, fontSize=18, leading=21, textColor=INK),
        "lbl": ParagraphStyle("lbl", fontName=FONT, fontSize=9.5, leading=12, textColor=INK),
        "rec": ParagraphStyle("rec", fontName=FONT, fontSize=10.5, leading=14, textColor=INK),
        "foot": ParagraphStyle("foot", fontName=FONT, fontSize=7, leading=9, textColor=MUTED,
                               alignment=TA_LEFT),
    }
    W = A4[0] - 3.6 * cm
    story = []
    story.append(Paragraph(_esc(c.name), ss["title"]))   # the report title is already in the green header bar
    delivery = f"{a.delivery_start.year}"
    story.append(Paragraph(T["prepared"].format(today=date_str(today, lang), as_of=date_str(a.model.as_of, lang),
                                                delivery=delivery), ss["sub"]))

    # consumption
    story.append(Paragraph(T["consumption_h"], ss["h"]))
    gas_m3 = sum(c.gas_m3)
    line = T["consumption"].format(gas_m3=_num(gas_m3, lang), gas_mwh=_num(gas_m3 * hw.MWH_PER_M3, lang),
                                   power=_num(sum(c.power_kwh) / 1000, lang))
    if c.solar_kwp:
        line += T["solar"].format(kwp=_num(c.solar_kwp, lang))
    story.append(Paragraph(line, ss["body"]))

    # the bill if variable
    story.append(Paragraph(T["bill_h"], ss["h"]))
    cells = [[Paragraph(T["expected_lbl"], ss["lbl"]), Paragraph(T["bad_lbl"], ss["lbl"])],
             [Paragraph(money(var_row.expected, lang), ss["big"]),
              Paragraph(f"<font color='#b8433a'>{money(var_row.es95, lang)}</font>", ss["big"])]]
    tb = Table(cells, colWidths=[W / 2, W / 2])
    tb.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), FONT), ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f3f4f6")),
                            ("BACKGROUND", (1, 0), (1, -1), RED_SOFT),
                            ("VALIGN", (0, 0), (-1, -1), "TOP"),
                            ("LEFTPADDING", (0, 0), (-1, -1), 10), ("TOPPADDING", (0, 0), (-1, -1), 4),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                            ("LINEAFTER", (0, 0), (0, -1), 3, colors.white)]))
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

    # recommendation
    story.append(Paragraph(T["rec_h"], ss["h"]))
    k = len(r["tranches"])
    if r["execution"] == "now" or k == 1:
        rec_txt = T["rec_now"].format(h=pct(r["hedge_gas"]))
    else:
        rec_txt = T["rec"].format(h=pct(r["hedge_gas"]), k=k, m=pct(r["buy_now_gas"]),
                                  tranche_word=T["tranche_many"] if k > 1 else T["tranche_one"])
    d_e, d_es = tradeoff_per_10pct(a)
    rec_parts = [Paragraph(rec_txt, ss["rec"]), Spacer(1, 3),
                 Paragraph(T["rec_effect"].format(e=money(hw_row.expected, lang), es=money(hw_row.es95, lang),
                                                  es0=money(var_row.es95, lang)), ss["body"]),
                 Paragraph(T["tradeoff"].format(a=money(d_e, lang), b=money(d_es, lang)), ss["body"])]
    box = Table([[rec_parts]], colWidths=[W])
    box.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), FONT), ("BACKGROUND", (0, 0), (-1, -1), SOFT),
                             ("LINEBEFORE", (0, 0), (0, -1), 4, BRAND),
                             ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                             ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 9)]))
    story.append(box)

    if stress:   # small stress chart in the bottom part of the page
        story.append(Paragraph(T["stress_h"], ss["h"]))
        sbuf = io.BytesIO()
        fig, ax = plt.subplots(figsize=(8.2, 2.45))
        hw.plot_stress(stress, ax=ax, lang=lang, compact=True)
        fig.tight_layout(pad=0.4)
        fig.savefig(sbuf, format="png", dpi=200)
        plt.close(fig)
        sbuf.seek(0)
        story.append(Image(sbuf, width=W, height=W * 2.45 / 8.2))
        story.append(Paragraph(T["stress_note"], ss["small"]))

    def _page(canv, doc):
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
        foot = Paragraph(T["footer"].format(fixed=fixed), ss["foot"])
        _, fh = foot.wrap(W, 3 * cm)
        canv.setStrokeColor(colors.HexColor("#d1d5db"))
        canv.line(1.8 * cm, 1.2 * cm + fh + 4, A4[0] - 1.8 * cm, 1.2 * cm + fh + 4)
        foot.drawOn(canv, 1.8 * cm, 1.2 * cm)
        canv.restoreState()

    _, foot_h = Paragraph(T["footer"].format(fixed=T["fixed_proxy"]), ss["foot"]).wrap(W, 5 * cm)
    doc = SimpleDocTemplate(path, pagesize=A4, leftMargin=1.8 * cm, rightMargin=1.8 * cm,
                            topMargin=1.7 * cm, bottomMargin=1.2 * cm + foot_h + 0.45 * cm,
                            title=f"HedgeWise - {c.name}", author="HedgeWise")
    doc.build(story, onFirstPage=_page, onLaterPages=_page)
    if doc.page != 1:
        raise RuntimeError(f"Report ran over one page ({doc.page} pages); shorten the text.")


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def report_path(company_name: str, as_of) -> Path:
    slug = re.sub(r"[^a-z0-9]+", "_", company_name.lower()).strip("_") or "company"
    return hw.OUT / f"report_{slug}_{pd.Timestamp(as_of):%Y-%m-%d}.pdf"


def main(argv=None):
    import company_input
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    company_input.add_cli_args(ap)
    ap.add_argument("--lang", default="en", choices=list(TEXT))
    ap.add_argument("--as-of", help="decision date YYYY-MM-DD (default: last date with data)")
    ap.add_argument("--risk-aversion", type=float, default=0.5)
    ap.add_argument("--budget", help="EUR or 'auto'")
    ap.add_argument("--paths", type=int, default=10_000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--solar-kwp", type=float, default=0.0)
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
    make_report(a, str(out), args.lang, stress)
    print(f"Report written to {out}")
    return out


if __name__ == "__main__":
    main()
