"""Monte Carlo simulation of daily gas / CO2 / power price paths from a fitted model."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ._types import DateLike
from .models import FittedModel, _power_design
from .settings import DEFAULT_PATHS, DEFAULT_SEED, MAX_DAILY_VOL, NU_MIN_SIM, SPIKE_HEADROOM


def _std_t(rng: np.random.Generator, z: np.ndarray, nu: float) -> np.ndarray:
    """Turn standard normals z into unit-variance Student-t (shared chi2 per row keeps tails joint)."""
    w = rng.chisquare(nu, size=z.shape)
    return z / np.sqrt(w / nu) * np.sqrt((nu - 2) / nu)


def simulate(
    m: FittedModel, end: DateLike, n_paths: int = DEFAULT_PATHS, seed: int = DEFAULT_SEED
) -> dict[str, np.ndarray | pd.DatetimeIndex]:
    """Daily paths from as_of+1 to end. Returns dict commodity -> array (n_days, n_paths) and 'dates'."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range(m.as_of + pd.Timedelta(days=1), pd.Timestamp(end), freq="D")
    n_d, k = len(dates), len(m.drivers) + 1
    L = np.linalg.cholesky(m.corr)
    bday = np.asarray(dates.day_of_week < 5)
    # driver state
    logp = {d: np.full(n_paths, np.log(m.last[d])) for d in m.drivers}
    var = {d: np.full(n_paths, m.garch[d].last_var) for d in m.drivers}
    u = np.full(n_paths, m.power.last_resid)
    out = {d: np.empty((n_d, n_paths), np.float32) for d in m.drivers + ["power"]}
    pm = m.power
    X_det = _power_design(dates, {d: np.zeros(n_d) for d in m.drivers})  # drivers filled per day below
    drv_cols = {d: 1 + i for i, d in enumerate(sorted(m.drivers))}
    for t in range(n_d):
        z = L @ rng.standard_normal((k, n_paths))
        for i, d in enumerate(m.drivers):
            g = m.garch[d]
            if bday[t] and g.kind == "garch_t":  # the default; kept exactly as before (same random draws)
                eps = _std_t(rng, z[i], max(g.nu, NU_MIN_SIM)) * np.sqrt(var[d])
                logp[d] = logp[d] + eps - 0.5 * var[d]  # martingale in price
                var[d] = g.omega + g.alpha * eps**2 + g.beta * var[d]
                var[d] = np.minimum(var[d], MAX_DAILY_VOL**2)
            elif bday[t] and g.kind == "gjr_t":  # rises (eps > 0) raise next-day variance by gamma * eps^2 more
                eps = _std_t(rng, z[i], max(g.nu, NU_MIN_SIM)) * np.sqrt(var[d])
                logp[d] = logp[d] + eps - 0.5 * var[d]
                var[d] = g.omega + (g.alpha + g.gamma * (eps > 0)) * eps**2 + g.beta * var[d]
                var[d] = np.minimum(var[d], MAX_DAILY_VOL**2)
            elif (
                bday[t] and g.kind == "garch_jump"
            ):  # normal diffusion + Poisson jumps, compensated to stay a martingale
                eps = z[i] * np.sqrt(var[d])
                jump = np.where(rng.random(n_paths) < g.jump_lam, rng.normal(g.jump_mu, g.jump_sig, n_paths), 0.0)
                r = eps + jump
                comp = g.jump_lam * (np.exp(g.jump_mu + 0.5 * g.jump_sig**2) - 1)
                logp[d] = logp[d] + r - 0.5 * var[d] - comp
                var[d] = g.omega + g.alpha * r**2 + g.beta * var[d]
                var[d] = np.minimum(var[d], MAX_DAILY_VOL**2)
            out[d][t] = np.exp(logp[d])
        e = _std_t(rng, z[-1], max(pm.nu, NU_MIN_SIM)) * pm.sigma
        u = np.clip(pm.phi * u + e, SPIKE_HEADROOM * pm.u_min, SPIKE_HEADROOM * pm.u_max)  # spikes beyond history
        base = X_det[t] @ pm.coef
        for d in m.drivers:
            base = base - X_det[t, drv_cols[d]] * pm.coef[drv_cols[d]] + logp[d] * pm.coef[drv_cols[d]]
        out["power"][t] = np.exp(base + u)
    out["dates"] = dates
    return out
