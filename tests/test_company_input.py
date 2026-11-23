"""Tests for company_input.py   -   run:  python -m pytest -q"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import company_input as ci  # noqa: E402
from hedgewise_model import MWH_PER_M3  # noqa: E402


# ---------------------------------------------------------------- numbers and months
@pytest.mark.parametrize("vals, expected", [
    (["1.234,5", "12.345,67", "800,0"], [1234.5, 12345.67, 800.0]),     # Dutch
    (["1,234.5", "12,345.67", "800.0"], [1234.5, 12345.67, 800.0]),     # English
    (["1.234", "12.345", "800"], [1234, 12345, 800]),                    # Dutch thousands only
    (["1234,5", "99,25"], [1234.5, 99.25]),                              # Dutch decimal comma only
    (["1,234", "12,345"], [1234, 12345]),                                # English thousands only
    (["12.5", "7.25"], [12.5, 7.25]),                                    # plain decimals
    (["1.233 kWh", "€ 2.000", " 1 500 "], [1233, 2000, 1500]),           # units / currency / spaces
    ([1200, 3.5, None], [1200, 3.5, np.nan]),                            # already numeric (Excel)
])
def test_parse_numbers(vals, expected):
    np.testing.assert_allclose(ci.parse_numbers(vals), expected, equal_nan=True)


@pytest.mark.parametrize("cell, month", [
    ("januari 2025", 1), ("Februari 2025", 2), ("mrt-25", 3), ("mei 2024", 5), ("okt 2025", 10),
    ("Oct 2025", 10), ("december", 12), ("2025-07", 7), ("2025-07-31", 7), ("31-01-2025", 1),
    ("01-2025", 1), ("5", 5), (11, 11), (pd.Timestamp("2025-09-01"), 9), ("totaal", None), ("13", None),
])
def test_parse_month(cell, month):
    assert ci.parse_month(cell) == month


def test_units():
    assert ci.gas_to_m3(1000, "m³")[()] == 1000
    np.testing.assert_allclose(ci.gas_to_m3(9769, "kWh"), 1000, rtol=1e-6)
    np.testing.assert_allclose(ci.gas_to_m3(1, "MWh"), 1 / MWH_PER_M3)
    np.testing.assert_allclose(ci.gas_to_m3(36, "GJ"), 36 * 0.2778 / MWH_PER_M3)
    assert ci.power_to_kwh(5, "MWh")[()] == 5000
    with pytest.raises(ValueError):
        ci.gas_to_m3(1, "liters")


# ---------------------------------------------------------------- (a) our template
def test_template_csv():
    res = ci.load_company(ROOT / "data" / "company_example.csv")
    tmpl = pd.read_csv(ROOT / "data" / "company_example.csv")
    assert res.mapping["date"] == "month" and res.mapping["gas"] == "gas_m3" and res.mapping["power"] == "power_kwh"
    np.testing.assert_allclose(res.company.gas_m3, tmpl.gas_m3)
    np.testing.assert_allclose(res.company.power_kwh, tmpl.power_kwh)
    assert not [w for w in res.warnings if "filled" in w]


# ---------------------------------------------------------------- (b) any export
def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_dutch_supplier_csv(tmp_path):
    rows = ["Verbruiksoverzicht Bakkerij De Vries", "Klantnummer;123456", "",
            "Periode;Aardgas (m³);Elektriciteit (kWh);Kosten (€)"]
    nl = ["januari", "februari", "maart", "april", "mei", "juni", "juli", "augustus", "september",
          "oktober", "november", "december"]
    for i, m in enumerate(nl):
        rows.append(f"{m} 2025;{10_000 + 1000 * i:,.0f};{150_000 + 500 * i:,.1f};999".replace(",", "X")
                    .replace(".", ",").replace("X", "."))
    p = _write(tmp_path, "nl_export.csv", "\n".join(rows))
    res = ci.load_company(p, name="De Vries")
    assert res.mapping["gas"] == "Aardgas (m³)" and res.mapping["power"] == "Elektriciteit (kWh)"
    assert res.mapping["gas_unit"] == "m3" and res.mapping["power_unit"] == "kWh"
    assert res.company.gas_m3[0] == pytest.approx(10_000) and res.company.gas_m3[11] == pytest.approx(21_000)
    assert res.company.power_kwh[3] == pytest.approx(151_500)


def test_excel_with_title_rows_mwh_and_gj(tmp_path):
    p = tmp_path / "portal.xlsx"
    dates = pd.date_range("2024-01-01", periods=12, freq="MS")
    table = pd.DataFrame({"Datum": dates, "Gas verbruik GJ": [360.0] * 12, "Stroom MWh": [40.0] * 12})
    with pd.ExcelWriter(p) as xw:
        pd.DataFrame([["Export klantportaal"], ["Aansluiting 871234"]]).to_excel(xw, header=False, index=False)
        table.to_excel(xw, startrow=3, index=False)
    res = ci.load_company(p)
    assert res.mapping["gas_unit"] == "GJ" and res.mapping["power_unit"] == "MWh"
    np.testing.assert_allclose(res.company.gas_m3, 360 * 0.2778 / MWH_PER_M3)
    np.testing.assert_allclose(res.company.power_kwh, 40_000)


def test_mapping_override(tmp_path):
    p = _write(tmp_path, "odd.csv", "when,a,b\n" + "\n".join(f"2025-{m:02d},{m},{100*m}" for m in range(1, 13)))
    with pytest.raises(ValueError):
        ci.load_company(p)   # columns 'a'/'b' can't be detected
    res = ci.load_company(p, mapping={"date": "when", "gas": "a", "power": "b", "gas_unit": "MWh",
                                      "power_unit": "kWh"})
    np.testing.assert_allclose(res.company.gas_m3[2], 3 / MWH_PER_M3)
    assert res.company.power_kwh[11] == 1200


def test_several_years_are_averaged(tmp_path):
    lines = ["maand;gas_m3;power_kwh"]
    for y, f in ((2023, 1.0), (2024, 3.0)):
        lines += [f"{y}-{m:02d};{1000 * f:.0f};{2000 * f:.0f}" for m in range(1, 13)]
    res = ci.load_company(_write(tmp_path, "two_years.csv", "\n".join(lines)))
    np.testing.assert_allclose(res.company.gas_m3, 2000)
    np.testing.assert_allclose(res.company.power_kwh, 4000)
    assert res.years == [2023, 2024]
    assert any("average" in w for w in res.warnings)


def test_few_missing_months_filled_with_warning(tmp_path):
    lines = ["month,gas_m3,power_kwh"] + [f"2025-{m:02d},1000,5000" for m in range(1, 11)]   # Nov, Dec missing
    res = ci.load_company(_write(tmp_path, "gap.csv", "\n".join(lines)), sector="offices")
    assert all(np.isfinite(res.company.gas_m3)) and len(res.company.gas_m3) == 12
    assert any("Nov, Dec" in w and "profile" in w for w in res.warnings)
    # offices use more gas in Dec than in an average Jan-Oct month
    assert res.company.gas_m3[11] > 1000


def test_too_many_missing_months_is_an_error(tmp_path):
    lines = ["month,gas_m3,power_kwh"] + [f"2025-{m:02d},1000,5000" for m in range(1, 8)]
    with pytest.raises(ValueError, match="only 7 of 12"):
        ci.load_company(_write(tmp_path, "short.csv", "\n".join(lines)))


def test_gas_only(tmp_path):
    lines = ["Maand;Gas (m3)"] + [f"{m}-2025;1.000" for m in range(1, 13)]
    res = ci.load_company(_write(tmp_path, "gas_only.csv", "\n".join(lines)))
    np.testing.assert_allclose(res.company.gas_m3, 1000)
    assert sum(res.company.power_kwh) == 0
    assert any("No power column" in w for w in res.warnings)


# ---------------------------------------------------------------- (c) yearly totals + sector
@pytest.mark.parametrize("sector", ci.SECTORS)
def test_yearly_totals(sector):
    res = ci.from_yearly(gas=500_000, gas_unit="m3", power=2_000, power_unit="MWh", sector=sector, name="X")
    assert sum(res.company.gas_m3) == pytest.approx(500_000)
    assert sum(res.company.power_kwh) == pytest.approx(2_000_000)
    assert len(res.warnings) == 1 and sector in res.warnings[0]


def test_sector_shapes_differ():
    off = ci.profile("offices", "gas")
    metal = ci.profile("metal", "gas")
    assert off[0] / off[6] > metal[0] / metal[6]          # offices are far more winter-heavy
    for s in ci.SECTORS:
        for c in ("gas", "power"):
            assert ci.profile(s, c).sum() == pytest.approx(1)
    with pytest.raises(ValueError):
        ci.profile("bakery", "gas")
