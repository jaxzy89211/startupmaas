"""Five FAKE invoices in the layouts of publicly published Dutch example bills and bill explanations (invented
company 'Testfabriek Zuid BV', invented numbers). Layout sources (public explanations, not copied documents):
- Consumind: annotated Eneco and Essent annual statements (jaarnota) - meter readings per register, normal/off-peak
  lines, supply price separate from energy tax, network costs and VAT
- Greenchoice: 'Toelichting jaarrekening' - usage = end reading - start reading per register
- Gaslicht: 'Zo leest u de zakelijke energierekening' - business bill building blocks (EAN, contract, tariffs)
- Zonneplan / Radar: feed-in (teruglevering) registers on the annual statement

Run: python tests/fixtures/invoices/make_fixtures.py   (writes the PDFs and expected.json next to this file)
"""

from __future__ import annotations

import json
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.pdfgen.canvas import Canvas

HERE = Path(__file__).resolve().parent
H = A4[1]


def _page(c: Canvas, title: str, lines: list[str], size: float = 9.5) -> None:
    c.setFont("Helvetica-Bold", 14)
    c.drawString(2 * cm, H - 2 * cm, title)
    c.setFont("Helvetica", size)
    y = H - 3 * cm
    for ln in lines:
        c.drawString(2 * cm, y, ln)
        y -= size * 1.55


def eneco_jaarnota(p: Path) -> dict:
    c = Canvas(str(p), pagesize=A4, invariant=1)
    _page(
        c,
        "Eneco Zakelijk - Jaarnota",
        [
            "Testfabriek Zuid BV, Industrieweg 1, Venlo        Klantnummer 1234567",
            "Periode: 01-01-2025 t/m 31-12-2025",
            "Contract: Vast tarief, looptijd t/m 31-12-2026",
            "",
            "Uw meterstanden en verbruik",
            "Telwerk                    Beginstand     Eindstand     Verbruik",
            "Elektriciteit normaal      1.204.330      1.324.730     120.400 kWh",
            "Elektriciteit dal            845.120        955.120     110.000 kWh",
            "Gas                          512.004        610.504      98.500 m3",
        ],
    )
    c.showPage()
    _page(
        c,
        "Specificatie kosten",
        [
            "Levering elektriciteit normaal   120.400 kWh x € 0,10230   € 12.316,92",
            "Levering elektriciteit dal       110.000 kWh x € 0,08910   €  9.801,00",
            "Energiebelasting elektriciteit   230.400 kWh x € 0,10154   € 23.394,82",
            "Netbeheerkosten elektriciteit                              €  4.120,00",
            "Levering gas                      98.500 m3 x € 0,45210    € 44.531,85",
            "Energiebelasting gas              98.500 m3 x € 0,57816    € 56.948,76",
            "Totaal excl. btw                                           €151.113,35",
            "Betaalde termijnbedragen                                   €148.000,00",
        ],
    )
    c.save()
    return {
        "supplier": "Eneco",
        "period_start": "2025-01-01",
        "period_end": "2025-12-31",
        "contract_type": "fixed",
        "contract_end": "2026-12-31",
        "totals": {"power_kwh": 230400.0, "gas_m3": 98500.0},
        "prices_eur_mwh": {
            "power": round((120400 * 102.30 + 110000 * 89.10) / 230400, 2),
            "gas": round(0.45210 / 0.009769, 2),
        },
    }


def vattenfall_maand(p: Path) -> dict:
    c = Canvas(str(p), pagesize=A4, invariant=1)
    _page(
        c,
        "Vattenfall Zakelijk - Factuur",
        [
            "Testfabriek Zuid BV                       Factuurdatum 08-04-2025",
            "EAN elektriciteit 871687120000123456     EAN gas 871687120000654321",
            "Factuurperiode: 01-03-2025 t/m 31-03-2025",
            "Product: Zakelijk Vast 2 jaar",
            "",
            "Verbruik deze periode",
            "Elektriciteit normaal      18.250 kWh",
            "Elektriciteit dal          15.900 kWh",
            "Gas                         9.420 m3",
            "",
            "Leveringstarief elektriciteit: € 0,0985 per kWh",
            "Leveringstarief gas: € 0,4410 per m3",
            "Energiebelasting elektriciteit: € 0,1015 per kWh",
        ],
    )
    c.save()
    return {
        "supplier": "Vattenfall",
        "period_start": "2025-03-01",
        "period_end": "2025-03-31",
        "contract_type": "fixed",
        "monthly": {"2025-03": {"power_kwh": 34150.0, "gas_m3": 9420.0}},
        "prices_eur_mwh": {"power": 98.5, "gas": round(0.4410 / 0.009769, 2)},
    }


def essent_jaarafrekening(p: Path) -> dict:
    c = Canvas(str(p), pagesize=A4, invariant=1)
    _page(
        c,
        "Essent - Jaarafrekening",
        [
            "Testfabriek Zuid BV",
            "Periode: 1 januari 2025 tot en met 31 december 2025",
            "Contractvorm: Variabel",
            "",
            "Meterstanden",
            "Stroom normaal       beginstand 77.120   eindstand 141.520   verbruik 64.400 kWh",
            "Stroom dal           beginstand 51.800   eindstand  99.300   verbruik 47.500 kWh",
            "Teruglevering normaal beginstand 3.100   eindstand  10.300   teruggeleverd 7.200 kWh",
            "Gas                  beginstand 20.010   eindstand  61.810   verbruik 41.800 m3",
            "",
            "Kosten",
            "Leveringskosten stroom normaal 64.400 kWh x € 0,11420",
            "Leveringskosten stroom dal 47.500 kWh x € 0,10120",
            "Leveringskosten gas 41.800 m3 x € 0,51330",
            "Energiebelasting stroom 111.900 kWh x € 0,10154",
        ],
    )
    c.save()
    return {
        "supplier": "Essent",
        "period_start": "2025-01-01",
        "period_end": "2025-12-31",
        "contract_type": "variable",
        "totals": {"power_kwh": 111900.0, "gas_m3": 41800.0},
        "prices_eur_mwh": {
            "power": round((64400 * 114.20 + 47500 * 101.20) / 111900, 2),
            "gas": round(0.51330 / 0.009769, 2),
        },
    }


def greenchoice_overzicht(p: Path) -> dict:
    c = Canvas(str(p), pagesize=A4, invariant=1)
    months = [
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
    gas = [14210, 12980, 11040, 8020, 5110, 3020, 2400, 2510, 4880, 8130, 11750, 13930]
    nor = [21400, 20100, 21950, 20800, 21300, 20600, 19900, 18750, 21100, 22000, 21500, 20200]
    dal = [17200, 16300, 17500, 16900, 17400, 17000, 18100, 17950, 17200, 17600, 17300, 18900]
    rows = [
        "Leverancier: Greenchoice Zakelijk",
        "Contracttype: vast",
        "Overzicht verbruik 2025",
        "",
        "Maand            Gas (m³)    Stroom normaal (kWh)    Stroom dal (kWh)",
    ]
    for m, g, n, d in zip(months, gas, nor, dal, strict=True):
        f = lambda v: f"{v:,}".replace(",", ".")  # noqa: E731
        rows.append(f"{m} 2025      {f(g)}        {f(n)}               {f(d)}")
    _page(c, "Greenchoice - Jaaroverzicht", rows)
    c.save()
    return {
        "supplier": "Greenchoice",
        "contract_type": "fixed",
        "monthly": {
            f"2025-{i + 1:02d}": {"gas_m3": float(gas[i]), "power_kwh": float(nor[i] + dal[i])} for i in range(12)
        },
    }


def budget_contract(p: Path) -> dict:
    c = Canvas(str(p), pagesize=A4, invariant=1)
    _page(
        c,
        "Budget Energie Zakelijk - Bevestiging van uw contract",
        [
            "Testfabriek Zuid BV",
            "Looptijd: 1 januari 2026 tot en met 31 december 2026",
            "Uw tarieven zijn vast voor de hele looptijd.",
            "Leveringstarief elektriciteit normaal: 9,85 ct/kWh",
            "Leveringstarief elektriciteit dal: 8,12 ct/kWh",
            "Leveringstarief gas: 48,50 ct/m3",
            "Energiebelasting elektriciteit 2026: € 0,1088 per kWh (wordt apart berekend)",
            "Prijs vastgelegd op 03-10-2025",
        ],
    )
    c.save()
    return {
        "supplier": "Budget Energie",
        "contract_type": "fixed",
        "contract_start": "2026-01-01",
        "contract_end": "2026-12-31",
        "fixed_on": "2025-10-03",
        "prices_eur_mwh": {"power": round((98.5 + 81.2) / 2, 2), "gas": round(0.4850 / 0.009769, 2)},
    }


ALL = {
    "eneco_jaarnota.pdf": eneco_jaarnota,
    "vattenfall_maandfactuur.pdf": vattenfall_maand,
    "essent_jaarafrekening.pdf": essent_jaarafrekening,
    "greenchoice_jaaroverzicht.pdf": greenchoice_overzicht,
    "budget_contract.pdf": budget_contract,
}

if __name__ == "__main__":
    expected = {name: fn(HERE / name) for name, fn in ALL.items()}
    (HERE / "expected.json").write_text(json.dumps(expected, indent=2, ensure_ascii=False) + "\n")
    print("wrote", ", ".join(expected))
