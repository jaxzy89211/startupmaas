"""Price models fitted on data up to the as-of date: GARCH(1,1)-t for gas/CO2 and the power regression."""

from __future__ import annotations

import dataclasses

import numpy as np
import pandas as pd
from scipy import optimize, signal, special

from ._types import DateLike
from .settings import (
    GARCH_FATOL,
    GARCH_MAX_PERSISTENCE,
    GARCH_MAXITER,
    GARCH_NU_MAX,
    GARCH_NU_MIN,
    GARCH_START_VALUES,
    GARCH_XATOL,
    MIN_CORR_OBS,
    MIN_EIGENVALUE,
    MIN_HISTORY_DAYS,
    POWER_FIT_DAYS,
    POWER_FLOOR,
    POWER_NU_BOUNDS,
    POWER_PHI_MAX,
    TRADING_DAYS_PER_YEAR,
)

DAYS_PER_YEAR = 365.25  # calendar convention for the annual seasonality terms
WEEKEND_DAYS = (5, 6)  # Saturday, Sunday (pandas day_of_week)
_INFEASIBLE = 1e12  # negative log-likelihood returned for parameters outside the allowed region
_PERSISTENCE_FLOOR = 1e-6  # guards the long-run variance formula when alpha + beta is ~1


# --------------------------------------------------------------------------------------------
# GARCH(1,1) with standardised Student-t errors
# --------------------------------------------------------------------------------------------
@dataclasses.dataclass
class GarchT:
    """Fitted GARCH(1,1)-t parameters and in-sample diagnostics for one commodity."""

    omega: float
    alpha: float
    beta: float
    nu: float
    mu: float
    last_var: float  # conditional variance for the next day
    resid_std: np.ndarray  # standardised residuals (for correlation)
    dates: pd.DatetimeIndex
    cond_var: np.ndarray  # fitted conditional variance per day

    @property
    def annual_vol_now(self) -> float:
        """Annualised volatility implied by the current conditional variance."""
        return float(np.sqrt(self.last_var * TRADING_DAYS_PER_YEAR))

    @property
    def annual_vol_longrun(self) -> float:
        """Annualised long-run (unconditional) volatility."""
        return float(np.sqrt(self.omega / max(1 - self.alpha - self.beta, _PERSISTENCE_FLOOR) * TRADING_DAYS_PER_YEAR))


def _garch_filter(r: np.ndarray, omega: float, alpha: float, beta: float, s0: float) -> np.ndarray:
    """Conditional variance path of a GARCH(1,1) (first-order IIR filter, started at s0)."""
    x = omega + alpha * np.concatenate([[s0], r[:-1] ** 2])
    s2 = signal.lfilter([1.0], [1.0, -beta], x, zi=[beta * s0])[0]
    return s2


def _t_loglik(z: np.ndarray, s2: np.ndarray | float, nu: float) -> np.ndarray:
    """Log-density of a standardised Student-t with variance s2 and nu degrees of freedom."""
    c = special.gammaln((nu + 1) / 2) - special.gammaln(nu / 2) - 0.5 * np.log(np.pi * (nu - 2))
    return c - 0.5 * np.log(s2) - (nu + 1) / 2 * np.log1p(z**2 / (s2 * (nu - 2)))


def fit_garch_t(returns: pd.Series) -> GarchT:
    """Fit GARCH(1,1) with standardised Student-t errors by maximum likelihood (best of several starts)."""
    r_raw = returns.dropna()
    mu = float(r_raw.mean())
    r = (r_raw - mu).values
    v = r.var()

    def nll(th: np.ndarray) -> float:
        omega, alpha, beta, nu = th
        if (
            omega <= 0
            or alpha < 0
            or beta < 0
            or alpha + beta >= GARCH_MAX_PERSISTENCE
            or nu <= GARCH_NU_MIN
            or nu > GARCH_NU_MAX
        ):
            return _INFEASIBLE
        s2 = _garch_filter(r, omega, alpha, beta, v)
        if np.any(s2 <= 0):
            return _INFEASIBLE
        return -np.sum(_t_loglik(r, s2, nu))

    best = None
    for a0, b0, n0 in GARCH_START_VALUES:
        x0 = [v * (1 - a0 - b0), a0, b0, n0]
        res = optimize.minimize(
            nll,
            x0,
            method="Nelder-Mead",
            options={"maxiter": GARCH_MAXITER, "xatol": GARCH_XATOL, "fatol": GARCH_FATOL},
        )
        if best is None or res.fun < best.fun:
            best = res
    omega, alpha, beta, nu = best.x
    s2 = _garch_filter(r, omega, alpha, beta, v)
    last_var = omega + alpha * r[-1] ** 2 + beta * s2[-1]
    return GarchT(omega, alpha, beta, nu, mu, float(last_var), r / np.sqrt(s2), r_raw.index, s2)


# --------------------------------------------------------------------------------------------
# Power model: log(power) on log(gas) [+ log(co2)] + seasonality, AR(1) residual with t-errors
# --------------------------------------------------------------------------------------------
def _power_design(dates: pd.DatetimeIndex, log_drivers: dict[str, np.ndarray]) -> np.ndarray:
    doy = np.asarray(dates.day_of_year)
    cols = [np.ones(len(dates))]
    cols += [log_drivers[k] for k in sorted(log_drivers)]
    cols += [np.sin(2 * np.pi * doy / DAYS_PER_YEAR), np.cos(2 * np.pi * doy / DAYS_PER_YEAR)]
    dow = np.asarray(dates.day_of_week)
    cols += [(dow == d).astype(float) for d in WEEKEND_DAYS]
    return np.column_stack(cols)


@dataclasses.dataclass
class PowerModel:
    """Fitted power regression (coefficients, AR(1) residual and its Student-t innovations)."""

    coef: np.ndarray
    drivers: list
    phi: float
    sigma: float
    nu: float
    last_resid: float
    innov_std: pd.Series  # standardised innovations (for correlation)
    u_min: float
    u_max: float
    r2: float


def fit_power(df: pd.DataFrame, drivers: list[str]) -> PowerModel:
    """Fit log(power) on log(drivers) + seasonality + weekend dummies, AR(1) residual with t-innovations."""
    d = df.iloc[-POWER_FIT_DAYS:]
    y = np.log(np.maximum(d["power"].values, POWER_FLOOR))
    X = _power_design(d.index, {k: np.log(d[k].values) for k in drivers})
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    u = y - X @ coef
    phi = float(np.clip(np.dot(u[1:], u[:-1]) / np.dot(u[:-1], u[:-1]), 0, POWER_PHI_MAX))
    e = u[1:] - phi * u[:-1]
    # Student-t degrees of freedom by MLE on the innovations
    sd = e.std()
    nu = optimize.minimize_scalar(lambda n: -np.sum(_t_loglik(e, sd**2, n)), bounds=POWER_NU_BOUNDS, method="bounded").x
    r2 = 1 - u.var() / y.var()
    return PowerModel(
        coef,
        drivers,
        phi,
        float(sd),
        float(nu),
        float(u[-1]),
        pd.Series(e / sd, index=d.index[1:]),
        float(u.min()),
        float(u.max()),
        float(r2),
    )


# --------------------------------------------------------------------------------------------
# Full fitted model + simulation
# --------------------------------------------------------------------------------------------
@dataclasses.dataclass
class FittedModel:
    """Everything fitted on one as-of date: GARCH per driver, the power model and the shock correlation."""

    as_of: pd.Timestamp
    drivers: list  # ["gas"] or ["gas", "co2"]
    garch: dict  # driver -> GarchT
    power: PowerModel
    corr: np.ndarray  # correlation of shocks, order drivers + ["power"]
    last: dict  # last observed levels
    history: pd.DataFrame  # data up to as_of


def fit(df: pd.DataFrame, as_of: DateLike | None = None) -> FittedModel:
    """Fit all price models using only data up to `as_of` (default: the last date in `df`)."""
    as_of = pd.Timestamp(as_of) if as_of is not None else df.index.max()
    h = df.loc[:as_of]
    if len(h) < MIN_HISTORY_DAYS:
        raise ValueError(f"Only {len(h)} days of data before {as_of.date()}; need at least ~1 year.")
    drivers = ["gas"] + (["co2"] if "co2" in h and h["co2"].notna().sum() > MIN_HISTORY_DAYS else [])
    if "co2" in drivers:
        h = h.dropna(subset=["co2"])
    garch = {}
    for k in drivers:
        trade = h[h[f"{k}_trade"]][k] if f"{k}_trade" in h else h[k]
        garch[k] = fit_garch_t(np.log(trade).diff())
    pm = fit_power(h, drivers)
    # correlation of shocks on common dates
    shocks = pd.DataFrame({k: pd.Series(garch[k].resid_std, index=garch[k].dates) for k in drivers})
    shocks["power"] = pm.innov_std
    shocks = shocks.dropna().iloc[-POWER_FIT_DAYS:]
    corr = np.corrcoef(shocks.values.T) if len(shocks) > MIN_CORR_OBS else np.eye(len(drivers) + 1)
    corr = _nearest_pd(corr)
    last = {k: float(h[k].iloc[-1]) for k in drivers + ["power"]}
    return FittedModel(as_of, drivers, garch, pm, corr, last, h)


def _nearest_pd(c: np.ndarray) -> np.ndarray:
    """Closest positive-definite correlation matrix (eigenvalue floor, then rescale to unit diagonal)."""
    w, v = np.linalg.eigh(c)
    c2 = v @ np.diag(np.maximum(w, MIN_EIGENVALUE)) @ v.T
    d = np.sqrt(np.diag(c2))
    return c2 / np.outer(d, d)


def describe(m: FittedModel) -> str:
    """Human-readable summary of the fitted parameters."""
    lines = [f"Model fitted on data up to {m.as_of:%Y-%m-%d} ({len(m.history)} days)"]
    for k in m.drivers:
        g = m.garch[k]
        lines.append(
            f"  {k:5s} GARCH-t: alpha={g.alpha:.3f} beta={g.beta:.3f} nu={g.nu:.1f} | "
            f"vol now {g.annual_vol_now:.0%}/yr, long-run {g.annual_vol_longrun:.0%}/yr | "
            f"last price {m.last[k]:.1f}"
        )
    pm = m.power
    b = dict(zip(sorted(m.drivers), pm.coef[1 : 1 + len(m.drivers)], strict=False))
    lines.append(
        f"  power: log P = ... + {' + '.join(f'{v:.2f}*log {k}' for k, v in b.items())} | "
        f"AR(1) phi={pm.phi:.2f}, nu={pm.nu:.1f}, R2={pm.r2:.2f} | last price {m.last['power']:.1f}"
    )
    names = m.drivers + ["power"]
    lines.append(
        "  shock correlation: "
        + ", ".join(
            f"{names[i]}-{names[j]} {m.corr[i, j]:+.2f}" for i in range(len(names)) for j in range(i + 1, len(names))
        )
    )
    return "\n".join(lines)
