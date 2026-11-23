"""Sanity checks for the 'what if 2022 happens again' stress test."""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hedgewise_model as hw  # noqa: E402


@pytest.fixture(scope="module")
def setup():
    df = hw.load_prices()
    m = hw.fit(df, "2024-06-30")
    a = hw.analyse(m, hw.Company(), None, n_paths=500, seed=1)
    return df, a, hw.stress_test(a, df)


def test_fix_100_is_todays_fixed_price(setup):
    _, a, s = setup
    fixed = a.company.gas_mwh.sum() * a.fwd_now["gas"] + a.company.power_mwh.sum() * a.fwd_now["power"]
    assert s["Fix 100% now"] == pytest.approx(fixed)


def test_ordering_and_linearity(setup):
    _, a, s = setup
    assert s["multiplier"]["gas"] > 1 and s["multiplier"]["power"] > 1       # 2022 was far above H1 2021
    assert s["All variable"] > s["Fix 60% now"] > s["Fix 100% now"]
    h = a.recommendation["hedge_gas"]
    assert s["HedgeWise"] == pytest.approx((1 - h) * s["All variable"] + h * s["Fix 100% now"])


def test_sentences_say_not_a_forecast(setup):
    _, _, s = setup
    assert "not a forecast" in hw.stress_sentence(s, "en")
    assert "geen voorspelling" in hw.stress_sentence(s, "nl")
