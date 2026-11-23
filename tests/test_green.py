"""Green investments: hourly shapes, profiles, battery arithmetic by hand, and the effect on the bill."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hedgewise as hw  # noqa: E402
from hedgewise import green as gr  # noqa: E402
from hedgewise.settings import GREEN, SOLAR_SHARE, SOLAR_YIELD_KWH_PER_KWP  # noqa: E402

FLAT = {m: np.ones((30, 24)) for m in range(1, 13)}  # flat hourly prices: every hour at the monthly level


@pytest.fixture(scope="module")
def analysis():
    df = hw.load_prices()
    return hw.analyse(hw.fit(df, "2025-06-30"), hw.Company(), None, 3000, 3, 0.5)


def test_hourly_data_and_day_shapes():
    h = gr.load_hourly()
    assert h.index.is_unique and h.index.max() >= pd.Timestamp("2026-11-01")  # 15-min data since Oct 2025 averaged
    assert (h.index.minute == 0).all()
    sh = gr.default_shapes()
    for m in range(1, 13):
        assert sh[m].shape[1] == 24 and 80 <= len(sh[m]) <= 100 and np.isfinite(sh[m]).all()
        assert sh[m].mean() == pytest.approx(1.0, abs=0.02)  # ratios to the monthly mean average to 1
    assert sh[7].mean(axis=0)[13] < 0.6 < 1.3 < sh[7].mean(axis=0)[20]  # summer: solar dip at noon, evening peak


def test_simulated_hourly_prices_scale_with_the_monthly_level():
    p = gr.simulate_hourly(100.0, 7, 3000, seed=1)
    assert p.shape == (3000, 24) and p.mean() == pytest.approx(100.0, rel=0.02)
    assert np.allclose(gr.simulate_hourly(200.0, 7, 10, seed=1), 2 * gr.simulate_hourly(100.0, 7, 10, seed=1))


def test_solar_profile_adds_up_to_the_monthly_yield():
    for m in (1, 6, 12):
        days = pd.Period(f"2027-{m:02d}").days_in_month
        want = 1000 * SOLAR_YIELD_KWH_PER_KWP / 1000 * SOLAR_SHARE[m - 1] / SOLAR_SHARE.sum()
        prof = gr.solar_profile(1000, m)
        assert prof.sum() * days == pytest.approx(want)
        a, b = GREEN["solar"]["sun_hours_cet"][m - 1]
        assert prof[: int(a)].sum() == 0 and prof[int(b) :].sum() == 0


def test_annuity():
    assert gr.annuity(1000, 10, 0.0) == 100
    assert gr.annuity(1000, 10, 0.05) == pytest.approx(129.50, abs=0.01)  # standard annuity factor


def test_battery_by_hand():
    r = np.array([[0.5, 0.5] + [1.0] * 20 + [2.0, 2.0]])
    big_load = np.full(24, 10.0)
    # 1 MWh / 0.5 MW -> 2 hours. Charge 0.5 MWh/h at 0.5 = 0.5; discharge 0.44 MWh/h at 2.0 = 1.76 -> 1.26
    assert gr.battery_day_value(r, big_load, 1000, 500)[0] == pytest.approx(1.76 - 0.5)
    # own load only 0.2 MWh/h: 0.2 replaces purchases, 0.24 is exported at 80% -> (0.2 + 0.8*0.24) * 2 * 2 - 0.5
    small_load = np.full(24, 0.2)
    assert gr.battery_day_value(r, small_load, 1000, 500)[0] == pytest.approx((0.2 + 0.8 * 0.24) * 4 - 0.5)
    # a flat day earns nothing (the 12% loss makes a cycle unprofitable): the battery stays idle
    assert gr.battery_day_value(np.ones((1, 24)), big_load, 1000, 500)[0] == 0


def test_effects_with_flat_prices_by_hand():
    c = hw.Company()
    sol = gr.solar_effect(c, 1000, FLAT)
    # flat load 5 GWh/yr (~570 kW) is always above a 1 MWp typical-day profile: everything is self-consumed
    assert sol.details["exported_mwh"] == pytest.approx(0) and sol.delta_mwh_eq.sum() == pytest.approx(-900)
    s = GREEN["solar"]
    assert sol.capex == 1000 * s["capex_eur_per_kwp"]
    assert sol.fixed_eur == pytest.approx(gr.annuity(sol.capex, s["lifetime_years"]) + sol.capex * 0.015)
    ppa = gr.ppa_effect(c, 0.4, 100.0, 10, FLAT)
    assert ppa.delta_mwh_eq.sum() == pytest.approx(-2000) and ppa.fixed_eur == pytest.approx(0.4 * 5000 * 100)
    with pytest.raises(ValueError):
        gr.effects(hw.Company(solar_kwp=100), gr.Investments(solar_kwp=100), 100.0)


def test_evaluate_on_scenarios(analysis):
    a = analysis
    t, arr = gr.evaluate(a, gr.Investments(solar_kwp=1000, battery_kwh=1000, ppa_share=0.4))
    assert list(t.investment) == [
        "+ solar 1 MWp",
        "+ battery 1 MWh",
        "+ PPA 40%",
        "+ solar 1 MWp + battery 1 MWh + PPA 40%",
    ]
    t = t.set_index("investment")
    # PPA at today's fixed power price = fixing 40% of power: costs the 5% premium in expectation (anchoring)
    v_power, f = a.company.power_mwh.sum(), a.fwd_now["power"]
    assert -t.loc["+ PPA 40%", "cut_expected"] == pytest.approx(0.4 * v_power * f * (1 - 1 / 1.05), rel=0.02)
    assert t.loc["+ PPA 40%", "cut_es95"] > 0 and pd.isna(t.loc["+ PPA 40%", "payback_normal"])
    # effects add up (cost is linear in the monthly level)
    single = sum(arr["costs"][k] - arr["plan"] for k in ("+ solar 1 MWp", "+ battery 1 MWh", "+ PPA 40%"))
    assert np.allclose(arr["costs"]["+ solar 1 MWp + battery 1 MWh + PPA 40%"] - arr["plan"], single)
    # payback = capex / yearly saving before capital cost; crisis saving >= normal (prices are higher)
    r = t.loc["+ solar 1 MWp"]
    assert r.payback_normal == pytest.approx(r.capex / r.saving_normal)
    assert r.saving_crisis > r.saving_normal > 0
    # the combined payback uses only the capital investments' savings (not the PPA's)
    comb = t.loc["+ solar 1 MWp + battery 1 MWh + PPA 40%"]
    sb = t.loc["+ solar 1 MWp", "saving_normal"] + t.loc["+ battery 1 MWh", "saving_normal"]
    assert comb.payback_normal == pytest.approx(comb.capex / sb)


def test_sentences_and_report(analysis, tmp_path):
    from hedgewise import report

    t, _ = gr.evaluate(analysis, gr.Investments(solar_kwp=500))
    en, nl = gr.report_line(t, "en"), gr.report_line(t, "nl")
    assert en.startswith("+ solar 0.5 MWp: expected bill €") and "payback" in en and en.endswith(".")
    assert nl.startswith("+ zon 0,5 MWp: verwachte rekening € ") and "terugverdientijd" in nl
    stress = hw.stress_test(analysis, hw.load_prices())
    for lang in ("en", "nl"):
        report.make_report(analysis, str(tmp_path / f"g_{lang}.pdf"), lang, stress, green=t)  # still one page
    hw.plot_frontier(analysis, path=tmp_path / "f.png", extra_points=[("x", 2e6, 1e6)])
    assert (tmp_path / "f.png").stat().st_size > 10_000


def test_no_look_ahead_in_hourly_shapes():
    """A historical decision date must only see hourly price shapes up to that date: corrupting every hour after
    it leaves the shapes identical."""
    h = gr.load_hourly()
    as_of = "2022-06-30"
    clean = gr.day_shapes(h, as_of=as_of)
    bad = h.copy()
    bad[bad.index >= pd.Timestamp("2022-07-01")] = 1e6
    corrupt = gr.day_shapes(bad, as_of=as_of)
    for m in range(1, 13):
        assert np.array_equal(clean[m], corrupt[m])
    # and evaluate() on a 2021 analysis uses only data up to its as-of date
    df = hw.load_prices()
    a = hw.analyse(hw.fit(df, "2021-06-30"), hw.Company(), None, 500, 1, 0.5)
    t, arr = gr.evaluate(a, gr.Investments(solar_kwp=500))
    sh = gr.default_shapes("2021-06-30")
    assert np.allclose(arr["effects"][0].delta_mwh_eq, gr.solar_effect(a.company, 500, sh, 2022).delta_mwh_eq)


def test_leap_year_days():
    c = hw.Company()
    feb28, feb29 = gr.load_profile(c, 2, 2027), gr.load_profile(c, 2, 2028)
    assert feb28.sum() * 28 == pytest.approx(feb29.sum() * 29)  # same monthly volume, spread over 28 or 29 days


def test_pdf_is_byte_identical_when_rebuilt(analysis, tmp_path):
    from hedgewise import report

    for name in ("a.pdf", "b.pdf"):
        report.make_report(analysis, str(tmp_path / name), "nl", today=pd.Timestamp("2026-11-23"))
    assert (tmp_path / "a.pdf").read_bytes() == (tmp_path / "b.pdf").read_bytes()
