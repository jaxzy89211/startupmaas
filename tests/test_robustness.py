"""Robustness: synthetic history builders, the fit cache, and the summary definitions."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hedgewise import backtest as bt  # noqa: E402
from hedgewise import robustness as rb  # noqa: E402


@pytest.fixture(scope="module")
def state():
    rb._init_worker()
    return rb._STATE


def _check(h, real, idx):
    assert len(h) == len(real) + len(idx) and h.notna().all().all()
    pd.testing.assert_frame_equal(h.loc[: real.index.max(), ["power", "gas"]], real[["power", "gas"]])
    assert h.index[len(real)] == real.index.max() + pd.Timedelta(days=1) and h.index.is_monotonic_increasing
    assert (h.loc[idx[0] :, ["gas", "power"]] > 0).all().all()  # real data has genuine negative power days


def test_model_and_bootstrap_histories(state):
    real, idx = state["real"], state["idx"]
    assert idx[-1] == pd.Timestamp(state["years"][-1], 12, 31) and len(state["years"]) == rb.FUTURE_YEARS
    a = rb.model_history(real, state["model"], idx, 1)
    b = rb.bootstrap_history(real, idx, 1)
    for h in (a, b):
        _check(h, real, idx)
    assert not a.equals(rb.model_history(real, state["model"], idx, 2))  # different seeds, different futures


def test_stress_histories_match_their_definitions(state):
    real, idx = state["real"], state["idx"]
    now = real[["gas", "power"]].iloc[-30:].mean()
    yearly = {}
    for n in rb.STRESS_NAMES:
        h = rb.stress_history(real, idx, n)
        _check(h, real, idx)
        f = h.loc[idx[0] :]
        yearly[n] = f.groupby(f.index.year)[["gas", "power"]].mean()
    r = yearly["2022 repeats"]
    avg21, avg22 = real.loc["2021", ["gas", "power"]].mean(), real.loc["2022", ["gas", "power"]].mean()
    np.testing.assert_allclose(r.loc[2028].values / now.values, (avg22 / avg21).values, rtol=0.02)
    s = yearly["spike then crash"]
    assert s.loc[2028, "gas"] > 3 * s.loc[2027, "gas"] * 0.8 and s.loc[2030, "gas"] < s.loc[2027, "gas"]
    u = yearly["slow 3-year rise"]
    assert u.loc[2031, "power"] > 2.3 * u.loc[2027, "power"]
    with pytest.raises(ValueError):
        rb.stress_history(real, idx, "nope")


def test_fit_cache_changes_nothing(state):
    df = rb.model_history(state["real"], state["model"], state["idx"], 5)
    y = state["years"][0]
    cached = rb.run_history(df, [y], state["company"])
    plain, _ = bt.run_year(df, state["company"], y, rb.LAMBDAS, rb.ROBUSTNESS_PATHS, 1, 10, verbose=False)
    assert np.array_equal(cached.loc[y].values, pd.Series(plain)[cached.columns].values)


def test_summary_definitions():
    rows = []
    for h, (vt, vw), (st, sw) in [
        ("1", (100, 30), (105, 25)),  # wins: lower worst year, +5% total
        ("2", (100, 30), (110, 20)),  # loses: +10% total
        ("3", (100, 30), (100, 31)),
    ]:  # loses: higher worst year
        rows += [
            {
                "family": "model",
                "history": h,
                "strategy": "All variable",
                "total": vt,
                "worst_year": vw,
                "max_jump": 1.0,
            },
            {"family": "model", "history": h, "strategy": "X", "total": st, "worst_year": sw, "max_jump": 2.0},
        ]
    s = rb.summarise(pd.DataFrame(rows)).set_index("strategy")
    assert s.loc["X", "pct_beats_variable"] == pytest.approx(1 / 3)
    assert s.loc["All variable", "pct_beats_variable"] == 0
    assert s.loc["X", "median_total"] == 105
