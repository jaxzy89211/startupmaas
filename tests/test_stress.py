"""Sanity checks for the two historical stress scenarios (2022 repeat; 2021->2022-sized shock from today)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hedgewise_model as hw  # noqa: E402


@pytest.fixture(scope="module")
def setup():
    df = hw.load_prices()
    m = hw.fit(df, "2024-06-30")
    a = hw.analyse(m, hw.Company(), None, n_paths=500, seed=1)
    return df, a, hw.stress_test(a, df)


def _sc(s, key):
    return next(x for x in s["scenarios"] if x["key"] == key)


def test_repeat_uses_realised_2022_prices(setup):
    df, a, s = setup
    rep = _sc(s, "repeat")
    y = df.loc["2022", "gas"]
    monthly = y.groupby(y.index.month).mean().values
    v = a.company.gas_mwh
    assert rep["avg_price"]["gas"] == pytest.approx(float(v @ monthly / v.sum()))
    assert 100 < rep["avg_price"]["gas"] < 150 and 200 < rep["avg_price"]["power"] < 290  # 2022 levels


def test_shock_multiplier_is_2022_over_2021_annual_average(setup):
    df, a, s = setup
    sh = _sc(s, "shock")
    for c in ("gas", "power"):
        expect = df.loc["2022", c].mean() / df.loc["2021", c].mean()
        assert sh["multiplier"][c] == pytest.approx(expect)
    # with a flat consumption profile the implied average would be exactly fixed price x multiplier;
    # with the real (seasonal) profile it stays within 15% of that
    for c in ("gas", "power"):
        assert sh["avg_price"][c] == pytest.approx(a.fwd_now[c] * sh["multiplier"][c], rel=0.15)


def test_bills_consistent(setup):
    _, a, s = setup
    fixed = a.company.gas_mwh.sum() * a.fwd_now["gas"] + a.company.power_mwh.sum() * a.fwd_now["power"]
    h = s["hedge"]
    for sc in s["scenarios"]:
        b = sc["bills"]
        assert b["Fix 100% now"] == pytest.approx(fixed)
        assert b["HedgeWise"] == pytest.approx((1 - h) * b["All variable"] + h * b["Fix 100% now"])
        assert b["Fix 60% now"] == pytest.approx(0.4 * b["All variable"] + 0.6 * b["Fix 100% now"])
    sh = _sc(s, "shock")["bills"]
    assert sh["All variable"] > sh["Fix 60% now"] > sh["Fix 100% now"]  # a shock raises prices


def test_sentences_say_not_a_forecast_and_show_prices(setup):
    _, _, s = setup
    en, nl = hw.stress_sentence(s, "en"), hw.stress_sentence(s, "nl")
    assert "not forecasts" in en and "geen voorspellingen" in nl
    assert "per MWh" in en and "per MWh" in nl
