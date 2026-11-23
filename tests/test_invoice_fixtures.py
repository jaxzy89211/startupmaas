"""Extraction on five fake invoices in the layouts of public Dutch bill explanations (tests/fixtures/invoices)."""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent / "fixtures" / "invoices"
sys.path.insert(0, str(HERE))
import accuracy  # noqa: E402


def test_every_field_of_every_fixture_is_read_correctly():
    df = accuracy.accuracy()
    bad = df[~df.ok]
    assert bad.empty, bad.to_string()
    assert len(df) >= 58  # supplier, periods, contract, totals, 13 months x 2, prices


def test_energy_tax_and_feed_in_are_not_supply():
    from hedgewise import extract as xt

    ex = xt.extract(HERE / "essent_jaarafrekening.pdf", "essent")
    assert ex.totals["power_kwh"] == 111_900  # 64,400 + 47,500; the 7,200 kWh fed back is not consumption
    assert all("belasting" not in p["quote"].lower() for p in ex.prices)
    assert any("teruglevering" in w.lower() for w in ex.warnings)


def test_contracts_for_the_audit():
    from hedgewise import extract as xt

    fixed = xt.contracts_table([xt.extract(HERE / "budget_contract.pdf", "budget")]).set_index("commodity")
    assert fixed.loc["power", "price"] == 89.85 and fixed.loc["power", "year"] == 2026  # (98.5 + 81.2) / 2
    assert fixed.loc["gas", "unit"] == "EUR/MWh" and fixed.loc["gas", "fixed_on"] == "2025-10-03"
    var = xt.contracts_table([xt.extract(HERE / "essent_jaarafrekening.pdf", "essent")])
    assert set(var.price) == {"variable"} and var["share_%"].isna().all()  # a variable contract is not a fixed price
