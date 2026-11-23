"""Procurement audit: a made-up company whose numbers are checked against hand calculations.

The made-up company "Testbakkerij" uses exactly 1,000 MWh of gas and 500 MWh of power in every month.
Its contracts:
    2022 gas   50% at 10 ct/kWh (= EUR 100/MWh), the rest variable;  power all variable
    2024 gas   100% at EUR 0.50/m3 (= 0.50 / 0.009769 = EUR 51.18/MWh); power 25% at EUR 200/MWh
(2023 is not entered: partial data.) The hand calculations below use only the formula in the audit docstring and
the realised monthly average prices, recomputed here straight from the price frame.
"""

import io
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hedgewise as hw  # noqa: E402
from hedgewise import audit as au  # noqa: E402
from hedgewise import backtest as bt  # noqa: E402
from hedgewise.settings import MWH_PER_M3  # noqa: E402

GAS_MWH, POWER_MWH = 1000.0, 500.0
CONTRACTS = """jaar;product;aandeel;prijs;eenheid;datum
2022;gas;50%;10;ct/kWh;15-09-2021
2022;stroom;;variabel;;
2024;gas;100%;0,50;EUR/m3;2023-10-02
2024;power;25;200;EUR/MWh;
"""


@pytest.fixture(scope="module")
def df():
    return hw.load_prices()


@pytest.fixture(scope="module")
def company():
    return hw.Company(name="Testbakkerij", gas_m3=(GAS_MWH / MWH_PER_M3,) * 12, power_kwh=(POWER_MWH * 1000,) * 12)


@pytest.fixture(scope="module")
def contracts():
    return au.read_contracts(io.StringIO(CONTRACTS))


def monthly(df, year, c):
    """Realised monthly averages, by hand: calendar-month mean of the daily prices."""
    s = df.loc[str(year), c]
    return np.array([s[s.index.month == m].mean() for m in range(1, 13)])


def test_unit_conversions():
    assert au.to_eur_per_mwh(10, "ct/kWh", "gas") == 100
    assert au.to_eur_per_mwh(0.1, "€/kWh", "power") == 100
    assert au.to_eur_per_mwh(0.5, "EUR/m³", "gas") == pytest.approx(51.18, abs=0.01)  # 0.5 / 0.009769
    assert au.to_eur_per_mwh(10, "EUR/GJ", "gas") == pytest.approx(36.0, abs=0.01)  # 10 / 0.2778
    with pytest.raises(ValueError):
        au.to_eur_per_mwh(0.5, "EUR/m3", "power")
    with pytest.raises(ValueError):
        au.to_eur_per_mwh(1, "EUR/ton", "gas")


def test_reading_dutch_table(contracts):
    k = {(c.year, c.commodity): c for c in contracts}
    assert len(contracts) == 4
    assert k[(2022, "gas")].share == 0.5 and k[(2022, "gas")].price_eur_mwh == 100
    assert k[(2022, "gas")].fixed_on == pd.Timestamp("2021-09-15")  # Dutch day-first date
    assert k[(2022, "power")].price_eur_mwh is None  # "variabel"
    assert k[(2024, "gas")].fixed_on == pd.Timestamp("2023-10-02")  # ISO date stays ISO (not 10 Feb)
    assert k[(2024, "gas")].price_eur_mwh == pytest.approx(0.5 / MWH_PER_M3)
    assert k[(2024, "power")].share == 0.25  # "25" read as 25%


def test_actual_cost_by_hand(df, company, contracts):
    p22g, p22p = monthly(df, 2022, "gas"), monthly(df, 2022, "power")
    p24g, p24p = monthly(df, 2024, "gas"), monthly(df, 2024, "power")
    a22 = au.actual_cost(df, company, contracts, 2022)
    a24 = au.actual_cost(df, company, contracts, 2024)
    # 2022 gas: 50% x EUR 100 x 12,000 MWh + 50% x sum(1,000 x p_m)
    assert a22["gas"]["cost"] == pytest.approx(0.5 * 100 * 12_000 + 0.5 * 1000 * p22g.sum(), rel=1e-12)
    assert a22["power"]["cost"] == pytest.approx(500 * p22p.sum(), rel=1e-12)  # all variable
    assert a24["gas"]["cost"] == pytest.approx(0.5 / MWH_PER_M3 * 12_000, rel=1e-12)  # 100% fixed
    assert a24["power"]["cost"] == pytest.approx(0.25 * 200 * 6000 + 0.75 * 500 * p24p.sum(), rel=1e-12)
    # sanity on the hand numbers themselves: the 2022 gas average was about EUR 124/MWh
    assert 110 < p22g.mean() < 140 and a22["gas"]["avg_fixed_price"] == 100
    assert a24["gas"]["variable_cost"] == 0 and np.isclose(a24["gas"]["realised_avg_price"], p24g.mean())


def test_strategies_by_hand_and_partial_years(df, company, contracts):
    r = au.run_audit(contracts, company, df, n_paths=200)  # fewer scenarios: only the HedgeWise ladder uses them
    assert r.years == [2022, 2024]  # partial data: 2023 not entered, not audited
    t = r.table
    for y in (2022, 2024):
        pg, pp = monthly(df, y, "gas"), monthly(df, y, "power")
        var = 1000 * pg.sum() + 500 * pp.sum()
        assert t.loc[y, "All variable"] == pytest.approx(var, rel=1e-12)
        # Fix 100% in October = fixed on 30 Sep of Y-1. Gas price by hand: last 30 calendar days' mean x 1.05.
        fg = df.loc[: f"{y - 1}-09-30", "gas"].iloc[-30:].mean() * 1.05
        fp = bt.fixed_price(
            df, "power", pd.Timestamp(f"{y - 1}-09-30"), pd.Timestamp(y, 1, 1), hw.fit(df, f"{y - 1}-09-30")
        )
        assert t.loc[y, "Fix 100% in October"] == pytest.approx(12_000 * fg + 6000 * fp, rel=1e-9)
        # 60% in tranches: 10% at each of the 6 month ends Jun-Nov of Y-1, 40% variable
        dates = [pd.Timestamp(y - 1, m, 1) + pd.offsets.MonthEnd(0) for m in range(6, 12)]
        gas_fix = sum(df.loc[:d, "gas"].iloc[-30:].mean() * 1.05 for d in dates)
        pow_fix = sum(bt.fixed_price(df, "power", d, pd.Timestamp(y, 1, 1), hw.fit(df, d)) for d in dates)
        hand = 0.1 * (12_000 * gas_fix + 6000 * pow_fix) + 0.4 * var
        assert t.loc[y, "60% in monthly tranches"] == pytest.approx(hand, rel=1e-9)
    act = sum(au.actual_cost(df, company, contracts, 2022)[c]["cost"] for c in ("gas", "power"))
    assert t.loc[2022, au.OURS] == pytest.approx(act)


def test_strategies_equal_the_backtest_for_the_default_company(df):
    """Like for like: same rules, seed and scenario count as backtest.py -> the same numbers (golden file),
    including the HedgeWise ladder, whose state is carried from 2021 as in the backtest."""
    import json

    golden = json.loads((Path(__file__).parent / "golden_backtest.json").read_text())["per_year_eur"]
    s = au.strategy_costs(hw.Company(), 2023, df)
    for col, label in au.COMPARE.items():
        for y in (2021, 2022, 2023):
            assert s.loc[y, label] == pytest.approx(golden[col][str(y)], abs=1), (label, y)


def test_sentence_never_hides_a_better_result(df, company, contracts):
    t = pd.DataFrame(
        {
            au.OURS: [1.0e6, 2.0e6],
            "All variable": [1.5e6, 1.0e6],
            "Fix 100% in October": [1.2e6, 3.0e6],
            "60% in monthly tranches": [1.1e6, 1.9e6],
            au.LADDER_2Y: [1.3e6, 1.5e6],
            "HedgeWise ladder": [1.4e6, 1.6e6],
        },
        index=pd.Index([2022, 2023], name="year"),
    )
    r = au.AuditResult(t, pd.DataFrame(), pd.DataFrame())
    s = au.sentence(r)
    assert s.startswith("You paid €3.00m over 2022-2023; the 2-year ladder would have cost €2.80m.")
    assert "Your worst year (2023, €2.00m) would have been €500k lower" in s
    assert s.endswith("In total you did better than: Fix 100% in October.")  # 3.0 < 4.2; a tie (3.0) is not "better"
    nl = au.sentence(r, "nl")
    assert "€ 3,00 mln" in nl and "€ 500.000 lager" in nl and nl.endswith(".")
    t2 = t.copy()
    t2[au.OURS] = [0.5e6, 0.9e6]  # the company beat every strategy in every year
    s2 = au.sentence(au.AuditResult(t2, pd.DataFrame(), pd.DataFrame()))
    assert "€600k higher with the 2-year ladder" in s2 and "HedgeWise ladder" in s2


@pytest.mark.parametrize(
    "csv, msg",
    [
        ("year,commodity,share,price,unit\n2022,gas,70%,50,EUR/MWh\n2022,gas,40%,60,EUR/MWh\n", "more than 100%"),
        ("year,commodity,share,price,unit\n2018,gas,50%,20,EUR/MWh\n", "can't be audited"),
        ("year,commodity,share,price,unit\n2022,oil,50%,20,EUR/MWh\n", "gas or power"),
        ("year,commodity,share,price,unit\n2022,gas,,20,EUR/MWh\n", "needs a share"),
        ("year,commodity,share,price,unit,fixed_on\n2022,gas,50%,20,EUR/MWh,2023-02-01\n", "after the end"),
    ],
)
def test_bad_input_is_refused(df, company, csv, msg):
    with pytest.raises(ValueError, match=msg):
        au.run_audit(au.read_contracts(io.StringIO(csv)), company, df, n_paths=50)


def test_cli_and_chart(tmp_path, monkeypatch):
    monkeypatch.setattr(hw, "OUT", tmp_path)
    r = au.main([str(Path(__file__).resolve().parents[1] / "data" / "audit_example.csv"), "--lang", "nl"])
    assert r.years == [2021, 2022, 2023, 2024, 2025]
    assert (tmp_path / "audit_nl.png").stat().st_size > 10_000 and (tmp_path / "audit_table.csv").exists()


def test_report_with_audit_page(tmp_path, df):
    from hedgewise import report

    comp = hw.Company()
    a = hw.analyse(hw.fit(df, "2025-06-30"), comp, None, 500, 1, 0.5)
    r = au.run_audit(au.read_contracts(Path(__file__).resolve().parents[1] / "data" / "audit_example.csv"), comp, df)
    for lang in ("en", "nl"):
        out = tmp_path / f"r_{lang}.pdf"
        report.make_report(a, str(out), lang, audit=r)  # raises if it isn't exactly 2 pages
        assert out.stat().st_size > 50_000
