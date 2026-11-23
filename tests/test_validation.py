"""Validation framework: no look-ahead, and the scoring functions against known answers."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy import stats

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hedgewise as hw  # noqa: E402
from hedgewise import validation as val  # noqa: E402

ORIGIN = pd.Timestamp("2022-03-31")
HS = [1, 3]


@pytest.fixture(scope="module")
def df():
    return hw.load_prices()


def _all_samples(fc):
    return {(v, h, c): fc[v][h][c] for v in fc for h in fc[v] for c in fc[v][h]}


def test_no_look_ahead(df):
    """Corrupting every price AFTER the origin must not change any forecast made at the origin."""
    base = val.forecast(df, ORIGIN, HS, 200, 1)
    bad = df.copy()
    future = bad.index > ORIGIN
    bad.loc[future, ["gas", "power"]] *= 10.0
    bad.loc[future, "gas_trade"] = ~bad.loc[future, "gas_trade"]
    extra = pd.DataFrame(
        {"power": 1.0, "gas": 1.0, "gas_trade": True},
        index=pd.date_range(df.index.max() + pd.Timedelta(days=1), periods=50, freq="D"),
    )
    bad = pd.concat([bad, extra])
    other = val.forecast(bad, ORIGIN, HS, 200, 1)
    a, b = _all_samples(base), _all_samples(other)
    assert a.keys() == b.keys() and len(a) == 5 * len(HS) * 2
    for k in a:
        np.testing.assert_array_equal(a[k], b[k], err_msg=str(k))


def test_past_data_does_change_the_forecast(df):
    """Sanity check that the test above is sensitive: changing data BEFORE the origin changes the forecast."""
    base = val.forecast(df, ORIGIN, [1], 200, 1)
    bad = df.copy()
    bad.loc[ORIGIN - pd.Timedelta(days=20) : ORIGIN, ["gas", "power"]] *= 1.5
    other = val.forecast(bad, ORIGIN, [1], 200, 1)
    assert not np.array_equal(base["a_garch_t"][1]["gas"], other["a_garch_t"][1]["gas"])


def test_windows_and_realised(df):
    assert val.window("2022-03-31", 1) == (pd.Timestamp("2022-04-01"), pd.Timestamp("2022-04-30"))
    assert val.window("2022-03-31", 12)[1] == pd.Timestamp("2023-03-31")
    r = val.realised(df, "2022-03-31", 1)
    assert r["gas"] == pytest.approx(df.loc["2022-04", "gas"].mean())
    assert val.realised(df, df.index.max() - pd.Timedelta(days=5), 1) is None
    o = val.origins(df, "2020-06-30", 1)
    assert o[0] == pd.Timestamp("2020-06-30") and val.window(o[-1], 1)[1] <= df.index.max()


def test_anchoring_sets_the_window_mean(df):
    raw, anch = val.model_forecasts(df, ORIGIN, "garch_t", HS, 300, 2)
    hist = df.loc[:ORIGIN]
    assert anch[1]["gas"].mean() == pytest.approx(hist["gas"].iloc[-30:].mean())
    assert not np.isclose(raw[1]["gas"].mean(), anch[1]["gas"].mean())


def test_crps_against_closed_form():
    x = stats.norm.rvs(size=200_000, random_state=np.random.default_rng(0))
    y = 0.7  # CRPS of N(0,1) at y: y(2Phi(y)-1) + 2phi(y) - 1/sqrt(pi)
    exact = y * (2 * stats.norm.cdf(y) - 1) + 2 * stats.norm.pdf(y) - 1 / np.sqrt(np.pi)
    assert val.crps_sample(x, y) == pytest.approx(exact, abs=0.005)
    assert val.crps_sample(np.array([3.0]), 1.0) == pytest.approx(2.0)  # point forecast: absolute error


def test_pinball():
    assert val.pinball(10.0, 12.0) == pytest.approx(2.0 * 0.95)
    assert val.pinball(10.0, 8.0) == pytest.approx(2.0 * 0.05)


def test_kupiec_and_christoffersen():
    hits = np.zeros(200)
    hits[::20] = 1  # exactly 5%, evenly spread
    assert val.kupiec(hits) == pytest.approx(1.0)
    ind, cc = val.christoffersen(hits)
    assert ind > 0.1 and cc > 0.1
    assert val.kupiec(np.r_[np.ones(30), np.zeros(70)]) < 1e-6  # 30% hit rate
    clustered = np.zeros(200)
    clustered[50:60] = 1  # 5% of hits, all in a row
    assert val.kupiec(clustered) == pytest.approx(1.0)
    assert val.christoffersen(clustered)[0] < 1e-3


def test_selection_rule():
    rows = []
    for v, rel, cov in [
        ("a_garch_t", 1.0, 0.9),
        ("b_gjr_t", 0.95, 0.9),
        ("c_garch_jump", 0.90, 0.6),
        ("d_garch_t_no_anchor", 0.99, 0.9),
        ("e_bootstrap", 0.5, 0.9),
    ]:
        for c in ("gas", "power"):
            rows.append(
                {"variant": v, "commodity": c, "horizon_months": 12, "crps_rel_current": rel, "coverage_90": cov}
            )
    t = pd.DataFrame(rows)
    ch = val.choose_default(t, 0.03, (0.80, 0.95))
    assert ch.default == "b_gjr_t" and ch.changed  # c is better but fails coverage; e is a benchmark only
    t.loc[t.variant == "b_gjr_t", "crps_rel_current"] = 0.98  # only 2% better: keep the current model
    assert val.choose_default(t, 0.03, (0.80, 0.95)).default == "a_garch_t"
