"""Each model variant recovers known parameters from synthetic data; the bootstrap reproduces known volatility."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hedgewise import models  # noqa: E402
from hedgewise import validation as val  # noqa: E402

N = 6000


def _series(r):
    return pd.Series(r, index=pd.bdate_range("2000-01-03", periods=len(r)))


def simulate_returns(kind, omega, alpha, beta, nu=np.inf, gamma=0.0, lam=0.0, jmu=0.0, jsig=0.0, n=N, seed=0):
    """Synthetic daily returns from a known GARCH-t / GJR-t / GARCH-jump process."""
    rng = np.random.default_rng(seed)
    persistence = alpha + gamma / 2 + beta
    s2 = (omega + alpha * lam * (jmu**2 + jsig**2)) / (1 - persistence)
    r = np.empty(n)
    for t in range(n):
        if np.isfinite(nu):
            z = rng.standard_t(nu) * np.sqrt((nu - 2) / nu)
        else:
            z = rng.standard_normal()
        jump = rng.normal(jmu, jsig) if (lam and rng.random() < lam) else 0.0
        r[t] = np.sqrt(s2) * z + jump
        s2 = omega + (alpha + gamma * (r[t] > 0)) * r[t] ** 2 + beta * s2
    return r


def test_garch_t_recovers_parameters():
    true = {"omega": 4e-5, "alpha": 0.08, "beta": 0.88, "nu": 6.0}
    g = models.fit_garch_t(_series(simulate_returns("garch_t", **true)))
    assert g.alpha == pytest.approx(true["alpha"], abs=0.03)
    assert g.beta == pytest.approx(true["beta"], abs=0.04)
    assert g.nu == pytest.approx(true["nu"], abs=2.0)
    assert g.kind == "garch_t" and g.gamma == 0


def test_gjr_t_recovers_the_asymmetry():
    true = {"omega": 4e-5, "alpha": 0.03, "gamma": 0.12, "beta": 0.87, "nu": 7.0}
    g = models.fit_gjr_t(_series(simulate_returns("gjr_t", **true)))
    assert g.kind == "gjr_t"
    assert g.gamma == pytest.approx(true["gamma"], abs=0.05)
    assert g.alpha == pytest.approx(true["alpha"], abs=0.03)
    assert g.beta == pytest.approx(true["beta"], abs=0.04)
    # and on symmetric data the asymmetry is ~0
    sym = models.fit_gjr_t(_series(simulate_returns("garch_t", 4e-5, 0.08, 0.88, nu=7.0, seed=3)))
    assert sym.gamma < 0.05


def test_garch_jump_recovers_jumps():
    true = {"omega": 2e-5, "alpha": 0.06, "beta": 0.90, "lam": 0.03, "jmu": 0.04, "jsig": 0.08}
    g = models.fit_garch_jump(_series(simulate_returns("garch_jump", **true, seed=5)))
    assert g.kind == "garch_jump"
    assert g.jump_lam == pytest.approx(true["lam"], abs=0.015)
    assert g.jump_mu == pytest.approx(true["jmu"], abs=0.03)
    assert g.jump_sig == pytest.approx(true["jsig"], abs=0.03)
    assert g.beta == pytest.approx(true["beta"], abs=0.05)


def test_bootstrap_reproduces_known_volatility():
    """iid daily log changes with sd sigma: the log of a 1-month (30-day) path average has
    sd ~ sigma * sqrt((n+1)(2n+1)/(6n)) for a random walk; the block bootstrap must match within 10%."""
    sigma, n_hist = 0.01, 3000
    rng = np.random.default_rng(1)
    idx = pd.date_range("2010-01-01", periods=n_hist, freq="D")
    logs = np.cumsum(rng.normal(0, sigma, (n_hist, 2)), axis=0) + np.log(50)
    df = pd.DataFrame({"gas": np.exp(logs[:, 0]), "power": np.exp(logs[:, 1]), "gas_trade": True}, index=idx)
    origin = pd.Timestamp("2017-12-31")
    fc = val.bootstrap_forecasts(df, origin, [1], 4000, 2, block=30)
    n = (val.window(origin, 1)[1] - origin).days
    expected = sigma * np.sqrt((n + 1) * (2 * n + 1) / (6 * n))
    for c in ("gas", "power"):
        assert np.log(fc[1][c]).std() == pytest.approx(expected, rel=0.10)


@pytest.mark.parametrize("variance", ["gjr_t", "garch_jump"])
def test_variants_simulate_martingale_prices(variance):
    """Simulated gas from each variant keeps the expected price ~ today's price (martingale) over 3 months."""
    from hedgewise import data
    from hedgewise.simulate import simulate

    df = data.load_prices()
    m = models.fit(df, "2024-06-30", variance=variance)
    assert m.garch["gas"].kind == variance and variance.split("_")[0].upper()[:3] in models.describe(m).upper()
    s = simulate(m, "2024-09-30", 4000, 3)
    assert s["gas"][-1].mean() == pytest.approx(m.last["gas"], rel=0.06)
