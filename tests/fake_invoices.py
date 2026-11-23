"""Five FAKE energy documents for the extraction tests (made with reportlab; no real company data).

1 Dutch monthly invoice (variable tariff, one month's gas and power totals)
2 English annual statement over two pages (12-month table, English numbers, fixed prices, contract dates)
3 Dutch contract confirmation (fixed prices in EUR/kWh and EUR/m3, term, date the price was fixed; no use)
4 Dutch annual overview with a 12-row table in Dutch number formats, power in MWh, one month missing and one
  month 3x the others (for the plausibility flags)
5 A "scanned" invoice: the page is only an image (no text layer), with some numbers missing
"""

from __future__ import annotations

import io
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.pdfgen.canvas import Canvas

H = A4[1]


def _lines(c: Canvas, lines: list[str], y0: float = H - 3 * cm, size: float = 10, x: float = 2 * cm) -> None:
    c.setFont("Helvetica", size)
    y = y0
    for line in lines:
        c.drawString(x, y, line)
        y -= size * 1.6


def invoice_monthly_nl(path: Path) -> None:
    c = Canvas(str(path), pagesize=A4, invariant=1)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(2 * cm, H - 2 * cm, "Vattenfall Zakelijk - Factuur")
    _lines(
        c,
        [
            "Klant: Bakkerij De Test BV          Factuurnummer: 2025-000123",
            "Periode: 01-01-2025 t/m 31-01-2025",
            "Contracttype: Variabel tarief (maandprijs)",
            "",
            "Gasverbruik                         142.350 m³",
            "Elektriciteitsverbruik              412.800 kWh",
            "",
            "Leveringskosten gas                 € 61.235,10",
            "Leveringskosten elektriciteit       € 39.218,44",
        ],
    )
    c.save()


def statement_annual_en(path: Path) -> None:
    c = Canvas(str(path), pagesize=A4, invariant=1)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(2 * cm, H - 2 * cm, "Eneco - Annual statement 2024")
    _lines(
        c,
        [
            "Customer: Test Foods Ltd",
            "Billing period: 1 January 2024 - 31 December 2024",
            "Contract type: Fixed price",
            "Fixed price electricity: 9.85 ct/kWh",
            "Fixed price gas: EUR 0.52 per m3",
            "Contract start: 1 January 2024",
            "Contract end: 31 December 2025",
            "Price fixed on: 15 October 2023",
            "Monthly consumption: see page 2",
        ],
    )
    c.showPage()
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    gas = [138000, 126000, 114000, 96000, 84000, 72000, 66000, 66000, 78000, 96000, 120000, 144000]
    pw = [410000, 380000, 415000, 405000, 420000, 425000, 445000, 440000, 420000, 420000, 410000, 410000]
    rows = ["Month        Gas (m3)      Electricity (kWh)"]
    rows += [f"{m} 2024     {g:,}        {p:,}" for m, g, p in zip(months, gas, pw, strict=True)]
    _lines(c, rows)
    c.save()


def contract_nl(path: Path) -> None:
    c = Canvas(str(path), pagesize=A4, invariant=1)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(2 * cm, H - 2 * cm, "Essent - Bevestiging leveringsovereenkomst")
    _lines(
        c,
        [
            "Klant: Metaalbewerking Zuid BV",
            "Looptijd: 01-01-2026 t/m 31-12-2026",
            "Vast tarief voor de hele looptijd",
            "Leveringstarief elektriciteit: € 0,0892 per kWh",
            "Leveringstarief gas: € 0,4120 per m³",
            "Prijs vastgelegd op 12-09-2025",
            "Alle prijzen exclusief energiebelasting en btw.",
        ],
    )
    c.save()


def overview_table_nl(path: Path) -> None:
    c = Canvas(str(path), pagesize=A4, invariant=1)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(2 * cm, H - 2 * cm, "Greenchoice - Jaaroverzicht verbruik 2025")
    names = [
        "januari",
        "februari",
        "maart",
        "april",
        "mei",
        "juni",
        "juli",
        "augustus",
        "september",
        "oktober",
        "november",
        "december",
    ]
    gas = [11850.5, 10420.0, 9875.5, 8120.0, 7015.5, 6010.0, 5560.5, None, 6450.0, 8010.5, 29999.0, 11990.0]
    pw = [34.4, 31.2, 34.0, 33.1, 34.5, 34.9, 36.6, None, 34.4, 34.6, 33.7, 33.9]
    rows = ["Leverancier: Greenchoice", "Contracttype: variabel", "", "Maand            Gas (m³)       Stroom (MWh)"]
    for n, g, p in zip(names, gas, pw, strict=True):
        if g is None:
            continue  # August missing
        gs = f"{g:,.1f}".replace(",", "X").replace(".", ",").replace("X", ".")
        ps = f"{p:.1f}".replace(".", ",")
        rows.append(f"{n} 2025      {gs}         {ps}")
    _lines(c, rows)
    c.save()


def scanned(path: Path) -> None:
    """The page is a picture of an invoice: no text layer (as from a scanner); some values are illegible."""
    from PIL import Image, ImageDraw, ImageFilter

    img = Image.new("L", (1240, 1754), 245)
    d = ImageDraw.Draw(img)
    y = 120
    for line in [
        "ENGIE - Factuur",
        "Periode: 01-02-2025 t/m 28-02-2025",
        "Gasverbruik: ??? m3",
        "Elektriciteitsverbruik: 39x.1xx kWh",
    ]:
        d.text((120, y), line, fill=40)
        y += 60
    img = img.rotate(1.2, fillcolor=245).filter(ImageFilter.GaussianBlur(1.2))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    c = Canvas(str(path), pagesize=A4, invariant=1)
    from reportlab.lib.utils import ImageReader

    c.drawImage(ImageReader(buf), 0, 0, width=A4[0], height=A4[1])
    c.save()


ALL = {
    "1_vattenfall_monthly_nl.pdf": invoice_monthly_nl,
    "2_eneco_annual_en.pdf": statement_annual_en,
    "3_essent_contract_nl.pdf": contract_nl,
    "4_greenchoice_overview_nl.pdf": overview_table_nl,
    "5_engie_scanned.pdf": scanned,
}


def make_all(folder: Path) -> dict[str, Path]:
    """Write all five fake documents into `folder`; returns {name: path}."""
    folder.mkdir(parents=True, exist_ok=True)
    out = {}
    for name, fn in ALL.items():
        fn(folder / name)
        out[name] = folder / name
    return out
