"""
HedgeWise price-risk model  (v0.3)
==================================

What it does
------------
1. Loads daily Dutch TTF gas, Dutch day-ahead power and (optionally) EU ETS CO2 prices.
2. Fits, using ONLY data available on the as-of date:
     * gas (and CO2): GARCH(1,1) with Student-t errors on daily log returns (martingale, no drift)
     * power: log(power) = a + b*log(gas) [+ c*log(CO2)] + weekday + annual seasonality + AR(1) residual
       with Student-t innovations  ->  power inherits gas crises, but keeps its own spikes
     * correlation between the gas / CO2 / power shocks (estimated from standardised residuals)
3. Simulates N price paths (default 10,000) from the as-of date to the end of the delivery year.
4. Prices the energy bill of a company under several procurement strategies and reports
   expected cost, 95% VaR, 95% Expected Shortfall and worst case.
5. Anchors the simulated price LEVEL to today's fixed price (market sets the level, the model sets the risk),
   so the only expected cost of hedging is the fixed-price premium.
6. Finds the HedgeWise recommendation: the share of gas and power to fix (bought in monthly tranches)
   that minimises   E[cost] + (gamma/2) * Var[cost] / E[all-variable bill]   for risk aversion gamma,
   and draws the efficient frontier (expected bill vs bad-case bill) so the CFO can pick another point.

Usage
-----
    python hedgewise_model.py                        # as of the latest data, delivery = next calendar year
    python hedgewise_model.py --as-of 2021-06-30     # what the model would have said before the crisis
    python hedgewise_model.py --risk-aversion 0.8 --solar-kwp 500
    python hedgewise_model.py --company data/company_example.csv
    python hedgewise_model.py --demo                 # synthetic prices, no data files needed

Commodity cost only: network tariffs, energy tax, ODE and supplier margins are identical across
strategies and are left out on purpose.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import optimize, signal, special

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUT = ROOT / "output"

MWH_PER_M3 = 0.009769       # Dutch natural gas, 35.17 MJ/m3 (upper heating value) -> MWh
FORWARD_PREMIUM = 0.05      # stand-in for a forward curve: fixed price = 30-day mean spot * (1 + 5%)
FORWARD_LOOKBACK_DAYS = 30
POWER_FLOOR = 5.0           # EUR/MWh, floor before taking logs (11 negative-price days since 2019)
NU_MIN_SIM = 4.0            # simulate with at least 4 degrees of freedom (nu<4 has infinite kurtosis)
MAX_DAILY_VOL = 0.10        # cap simulated daily gas/CO2 volatility at 10% (~160%/yr)
POWER_FIT_DAYS = 730        # window for the power-on-gas regression
ANCHOR_TO_FORWARD = True    # market sets the price level (fixed price / (1+premium)), the model sets the risk
SAME_RATIO = True           # one hedge ratio for gas and power (clearer advice; per-commodity costs <0.5% objective)
OBJECTIVE = "mean_variance"  # "mean_variance" (interior hedge ratios) or "mean_es" (v0.2, always 0% or 100%)
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


# --------------------------------------------------------------------------------------------
# Company
# --------------------------------------------------------------------------------------------
@dataclasses.dataclass
class Company:
    """Monthly consumption profile. Replace with pilot data (12-24 months of bills) when you have it."""
    name: str = "Limburg food processor (hypothetical)"
    # m3 of natural gas per month. 1.2 million m3/yr: process steam (flat) + space heating (winter)
    gas_m3: tuple = tuple(1_200_000 * s / 100 for s in
                          (11.5, 10.5, 9.5, 8.0, 7.0, 6.0, 5.5, 5.5, 6.5, 8.0, 10.0, 12.0))
    # kWh of electricity per month. 5 GWh/yr: flat production with a small summer cooling bump
    power_kwh: tuple = tuple(5_000_000 * s / 100 for s in
                             (8.2, 7.6, 8.3, 8.1, 8.4, 8.5, 8.9, 8.8, 8.4, 8.4, 8.2, 8.2))
    solar_kwp: float = 0.0  # rooftop PV, assumed fully self-consumed

    @property
    def gas_mwh(self) -> np.ndarray:
        return np.asarray(self.gas_m3, float) * MWH_PER_M3

    @property
    def power_mwh(self) -> np.ndarray:
        return np.maximum(np.asarray(self.power_kwh, float) / 1000 - solar_mwh(self.solar_kwp), 0.0)

    @classmethod
    def from_csv(cls, path: str | Path, name: str | None = None) -> "Company":
        """CSV with columns month (1-12 or a date), gas_m3, power_kwh. If several years are given,
        the average per calendar month is used."""
        df = pd.read_csv(path)
        m = df["month"]
        month = m.astype(int) if pd.api.types.is_numeric_dtype(m) else pd.to_datetime(m).dt.month
        prof = df.assign(_m=month).groupby("_m")[["gas_m3", "power_kwh"]].mean().reindex(range(1, 13))
        if prof.isna().any().any():
            raise ValueError(f"{path}: need data for all 12 calendar months")
        return cls(name=name or Path(path).stem, gas_m3=tuple(prof.gas_m3), power_kwh=tuple(prof.power_kwh))


SOLAR_SHARE = np.array([3.0, 4.5, 8.0, 11.0, 13.0, 14.0, 14.0, 11.5, 9.0, 6.5, 3.5, 2.0])
SOLAR_YIELD_KWH_PER_KWP = 900  # typical for south-east Netherlands


def solar_mwh(kwp: float) -> np.ndarray:
    return kwp * SOLAR_YIELD_KWH_PER_KWP / 1000 * SOLAR_SHARE / SOLAR_SHARE.sum()


# --------------------------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------------------------
def _read_investing_csv(path: Path) -> pd.Series:
    """investing.com export: Date, Price, Open, High, Low, Vol., Change %  (US date format)."""
    df = pd.read_csv(path)
    date_col = next(c for c in df.columns if c.strip().lower() in ("date", "datum"))
    price_col = next(c for c in df.columns if c.strip().lower() in ("price", "close", "koers", "slot"))
    s = df[price_col]
    if s.dtype == object:
        s = pd.to_numeric(s.str.replace(",", ""), errors="coerce")
    idx = pd.to_datetime(df[date_col], format="mixed", dayfirst=False)
    return pd.Series(s.values, index=idx).sort_index().dropna()


def load_prices(data_dir: Path = DATA) -> pd.DataFrame:
    """Daily calendar-day frame with columns gas, power and (if data/co2_eua.csv exists) co2.
    Gas/CO2 are forward-filled over weekends; column 'gas_trade' flags real gas trading days."""
    g = pd.read_csv(data_dir / "gas_ttf.csv")
    gcol = "Close" if "Close" in g.columns else None
    gas = (pd.Series(g[gcol].values, index=pd.to_datetime(g["Date"])) if gcol
           else _read_investing_csv(data_dir / "gas_ttf.csv"))
    p = pd.read_csv(data_dir / "power_nl_daily.csv", index_col=0)
    pidx = pd.to_datetime(p.index, utc=True).tz_convert("Europe/Amsterdam").tz_localize(None).normalize()
    power = pd.Series(p.iloc[:, 0].values, index=pidx)

    idx = pd.date_range("2019-01-01", min(power.index.max(), gas.index.max() + pd.Timedelta(days=3)), freq="D")
    df = pd.DataFrame(index=idx)
    df["power"] = power.reindex(idx)
    df["gas_trade"] = gas.reindex(idx).notna()
    df["gas"] = gas.reindex(idx).ffill()
    co2_path = data_dir / "co2_eua.csv"
    if co2_path.exists():
        co2 = _read_investing_csv(co2_path)
        df["co2"] = co2.reindex(idx).ffill()
        df["co2_trade"] = co2.reindex(idx).notna()
    df = df.dropna(subset=["power", "gas"])
    return df


def demo_prices(seed: int = 7) -> pd.DataFrame:
    """Synthetic 2019-2024 history with a 2021-22 style crisis, so the model runs without any files."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2019-01-01", "2024-12-31", freq="D")
    t = np.arange(len(idx)) / 365
    crisis = 2.2 * np.exp(-((t - 3.6) / 0.6) ** 2)
    log_gas = np.log(15) + crisis + np.cumsum(rng.standard_t(4, len(idx)) * 0.025) * 0.6
    gas = np.exp(log_gas)
    season = 0.08 * np.cos(2 * np.pi * (idx.day_of_year - 15) / 365)
    weekend = np.where(idx.day_of_week >= 5, -0.15, 0.0)
    u = signal.lfilter([1], [1, -0.7], rng.standard_t(4, len(idx)) * 0.12)
    power = np.exp(1.2 + 0.75 * np.log(gas) + season + weekend + u)
    df = pd.DataFrame({"power": power, "gas": gas, "gas_trade": idx.day_of_week < 5}, index=idx)
    return df


# --------------------------------------------------------------------------------------------
# GARCH(1,1) with standardised Student-t errors
# --------------------------------------------------------------------------------------------
@dataclasses.dataclass
class GarchT:
    omega: float
    alpha: float
    beta: float
    nu: float
    mu: float
    last_var: float       # conditional variance for the next day
    resid_std: np.ndarray  # standardised residuals (for correlation)
    dates: pd.DatetimeIndex
    cond_var: np.ndarray   # fitted conditional variance per day

    @property
    def annual_vol_now(self) -> float:
        return float(np.sqrt(self.last_var * 252))

    @property
    def annual_vol_longrun(self) -> float:
        return float(np.sqrt(self.omega / max(1 - self.alpha - self.beta, 1e-6) * 252))


def _garch_filter(r, omega, alpha, beta, s0):
    x = omega + alpha * np.concatenate([[s0], r[:-1] ** 2])
    s2 = signal.lfilter([1.0], [1.0, -beta], x, zi=[beta * s0])[0]
    return s2


def _t_loglik(z, s2, nu):
    c = special.gammaln((nu + 1) / 2) - special.gammaln(nu / 2) - 0.5 * np.log(np.pi * (nu - 2))
    return c - 0.5 * np.log(s2) - (nu + 1) / 2 * np.log1p(z ** 2 / (s2 * (nu - 2)))


def fit_garch_t(returns: pd.Series) -> GarchT:
    r_raw = returns.dropna()
    mu = float(r_raw.mean())
    r = (r_raw - mu).values
    v = r.var()

    def nll(th):
        omega, alpha, beta, nu = th
        if omega <= 0 or alpha < 0 or beta < 0 or alpha + beta >= 0.999 or nu <= 2.1 or nu > 100:
            return 1e12
        s2 = _garch_filter(r, omega, alpha, beta, v)
        if np.any(s2 <= 0):
            return 1e12
        return -np.sum(_t_loglik(r, s2, nu))

    best = None
    for a0, b0, n0 in [(0.08, 0.90, 5), (0.15, 0.80, 4), (0.05, 0.93, 8)]:
        x0 = [v * (1 - a0 - b0), a0, b0, n0]
        res = optimize.minimize(nll, x0, method="Nelder-Mead",
                                options={"maxiter": 4000, "xatol": 1e-8, "fatol": 1e-6})
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
    cols += [np.sin(2 * np.pi * doy / 365.25), np.cos(2 * np.pi * doy / 365.25)]
    dow = np.asarray(dates.day_of_week)
    cols += [(dow == d).astype(float) for d in (5, 6)]  # Saturday, Sunday
    return np.column_stack(cols)


@dataclasses.dataclass
class PowerModel:
    coef: np.ndarray
    drivers: list
    phi: float
    sigma: float
    nu: float
    last_resid: float
    innov_std: pd.Series   # standardised innovations (for correlation)
    u_min: float
    u_max: float
    r2: float


def fit_power(df: pd.DataFrame, drivers: list[str]) -> PowerModel:
    d = df.iloc[-POWER_FIT_DAYS:]
    y = np.log(np.maximum(d["power"].values, POWER_FLOOR))
    X = _power_design(d.index, {k: np.log(d[k].values) for k in drivers})
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    u = y - X @ coef
    phi = float(np.clip(np.dot(u[1:], u[:-1]) / np.dot(u[:-1], u[:-1]), 0, 0.99))
    e = u[1:] - phi * u[:-1]
    # Student-t degrees of freedom by MLE on the innovations
    sd = e.std()
    nu = optimize.minimize_scalar(lambda n: -np.sum(_t_loglik(e, sd ** 2, n)), bounds=(2.2, 60),
                                  method="bounded").x
    r2 = 1 - u.var() / y.var()
    return PowerModel(coef, drivers, phi, float(sd), float(nu), float(u[-1]),
                      pd.Series(e / sd, index=d.index[1:]), float(u.min()), float(u.max()), float(r2))


# --------------------------------------------------------------------------------------------
# Full fitted model + simulation
# --------------------------------------------------------------------------------------------
@dataclasses.dataclass
class FittedModel:
    as_of: pd.Timestamp
    drivers: list                     # ["gas"] or ["gas", "co2"]
    garch: dict                       # driver -> GarchT
    power: PowerModel
    corr: np.ndarray                  # correlation of shocks, order drivers + ["power"]
    last: dict                        # last observed levels
    history: pd.DataFrame             # data up to as_of


def fit(df: pd.DataFrame, as_of=None) -> FittedModel:
    as_of = pd.Timestamp(as_of) if as_of is not None else df.index.max()
    h = df.loc[:as_of]
    if len(h) < 250:
        raise ValueError(f"Only {len(h)} days of data before {as_of.date()}; need at least ~1 year.")
    drivers = ["gas"] + (["co2"] if "co2" in h and h["co2"].notna().sum() > 250 else [])
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
    corr = np.corrcoef(shocks.values.T) if len(shocks) > 60 else np.eye(len(drivers) + 1)
    corr = _nearest_pd(corr)
    last = {k: float(h[k].iloc[-1]) for k in drivers + ["power"]}
    return FittedModel(as_of, drivers, garch, pm, corr, last, h)


def _nearest_pd(c):
    w, v = np.linalg.eigh(c)
    c2 = v @ np.diag(np.maximum(w, 1e-6)) @ v.T
    d = np.sqrt(np.diag(c2))
    return c2 / np.outer(d, d)


def _std_t(rng, z, nu):
    """Turn standard normals z into unit-variance Student-t (shared chi2 per row keeps tails joint)."""
    w = rng.chisquare(nu, size=z.shape)
    return z / np.sqrt(w / nu) * np.sqrt((nu - 2) / nu)


def simulate(m: FittedModel, end, n_paths=10_000, seed=42) -> dict[str, pd.DataFrame | np.ndarray]:
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
        z = (L @ rng.standard_normal((k, n_paths)))
        for i, d in enumerate(m.drivers):
            g = m.garch[d]
            if bday[t]:
                eps = _std_t(rng, z[i], max(g.nu, NU_MIN_SIM)) * np.sqrt(var[d])
                logp[d] = logp[d] + eps - 0.5 * var[d]          # martingale in price
                var[d] = g.omega + g.alpha * eps ** 2 + g.beta * var[d]
                var[d] = np.minimum(var[d], MAX_DAILY_VOL ** 2)
            out[d][t] = np.exp(logp[d])
        e = _std_t(rng, z[-1], max(pm.nu, NU_MIN_SIM)) * pm.sigma
        u = np.clip(pm.phi * u + e, 1.25 * pm.u_min, 1.25 * pm.u_max)  # spikes up to 25% beyond history
        base = X_det[t] @ pm.coef
        for d in m.drivers:
            base = base - X_det[t, drv_cols[d]] * pm.coef[drv_cols[d]] + logp[d] * pm.coef[drv_cols[d]]
        out["power"][t] = np.exp(base + u)
    out["dates"] = dates
    return out


# --------------------------------------------------------------------------------------------
# Strategies and risk
# --------------------------------------------------------------------------------------------
def tranche_dates(as_of, delivery_start, max_tranches=6) -> list[pd.Timestamp]:
    """Tranche dates: the as-of date itself ("now"), then the first day of each following month before
    delivery starts (months starting within a week of as-of count as "now"), at most max_tranches."""
    as_of, ds = pd.Timestamp(as_of), pd.Timestamp(delivery_start)
    months = pd.date_range(as_of + pd.Timedelta(days=7), ds - pd.Timedelta(days=1), freq="MS")
    return ([as_of] + list(months))[:max_tranches]


def _monthly_mean(paths: np.ndarray, dates: pd.DatetimeIndex, start, months=12) -> np.ndarray:
    """(12, n_paths) average daily price per delivery month."""
    res = []
    for i in range(months):
        ms = pd.Timestamp(start) + pd.DateOffset(months=i)
        mask = (dates >= ms) & (dates < ms + pd.DateOffset(months=1))
        res.append(paths[mask].mean(axis=0))
    return np.vstack(res)


def _trailing_mean(paths, dates, hist: pd.Series, when, days=FORWARD_LOOKBACK_DAYS):
    """Forward proxy at date `when`: mean of the last `days` days (history + simulated)."""
    when = pd.Timestamp(when)
    lo = when - pd.Timedelta(days=days)
    h = hist.loc[(hist.index > lo) & (hist.index <= when)]
    mask = (dates > lo) & (dates <= when)
    tot = h.sum() + paths[mask].sum(axis=0)
    cnt = len(h) + mask.sum()
    return tot / cnt


def season_adjustment(pm: PowerModel, when, delivery_start) -> float:
    """Factor that turns a trailing-30-day power price into a calendar-delivery price, using the
    fitted annual seasonality (a November spot price overstates a full-year baseload price)."""
    n = len(pm.drivers)
    cs, cc = pm.coef[1 + n], pm.coef[2 + n]

    def level(idx):
        doy = np.asarray(idx.day_of_year)
        return np.exp(cs * np.sin(2 * np.pi * doy / 365.25) + cc * np.cos(2 * np.pi * doy / 365.25)).mean()

    when, ds = pd.Timestamp(when), pd.Timestamp(delivery_start)
    win = pd.date_range(when - pd.Timedelta(days=FORWARD_LOOKBACK_DAYS - 1), when, freq="D")
    dlv = pd.date_range(ds, ds + pd.DateOffset(years=1) - pd.Timedelta(days=1), freq="D")
    return float(level(dlv) / level(win))


def risk_stats(cost: np.ndarray, q=0.95) -> dict:
    var = float(np.quantile(cost, q))
    return {"expected": float(cost.mean()), "p50": float(np.median(cost)), f"var{int(q*100)}": var,
            f"es{int(q*100)}": float(cost[cost >= var].mean()), "worst": float(cost.max()),
            "best": float(cost.min())}


@dataclasses.dataclass
class Analysis:
    model: FittedModel
    company: Company
    delivery_start: pd.Timestamp
    tranches: list
    spot_month: dict           # commodity -> (12, n) simulated monthly average spot
    fwd_now: dict              # commodity -> fixed price available today
    fwd_tranche_avg: dict      # commodity -> (n,) average price over all tranches
    costs: dict                # strategy -> (n,) total cost in EUR
    table: pd.DataFrame
    frontier: pd.DataFrame
    recommendation: dict
    sims: dict


def analyse(m: FittedModel, company: Company, delivery_start=None, n_paths=10_000, seed=42,
            risk_aversion=0.5, max_tranches=6, sims=None, allow_timing=False, budget=None) -> Analysis:
    """allow_timing=False (default): HedgeWise always executes in monthly tranches (a policy, no market timing).
    allow_timing=True: the optimiser may also choose to fix everything immediately.
    budget=None: minimise (1-lambda)*E + lambda*ES95.
    budget=<EUR> or "auto": hedge as LITTLE as possible such that the 95% VaR stays within budget
    ("auto" = last 12 months' realised bill on this consumption profile + BUDGET_MARGIN). If even a
    full hedge cannot meet the budget, take the hedge with the lowest ES95."""
    ds = (pd.Timestamp(delivery_start) if delivery_start is not None
          else pd.Timestamp(year=m.as_of.year + 1, month=1, day=1))
    de = ds + pd.DateOffset(years=1) - pd.Timedelta(days=1)
    sims = sims or simulate(m, de, n_paths, seed)
    dates = sims["dates"]
    tr = tranche_dates(m.as_of, ds, max_tranches)
    vol = {"gas": company.gas_mwh, "power": company.power_mwh}
    # align volumes with delivery months (delivery may start mid-year)
    order = [(ds + pd.DateOffset(months=i)).month - 1 for i in range(12)]
    vol = {k: v[order] for k, v in vol.items()}

    spot_m, fwd_now, fwd_tr, c_var, c_fix, c_tr, anchor_k = {}, {}, {}, {}, {}, {}, {}
    sims = dict(sims)  # never modify the (cached) input paths in place
    for c in ("gas", "power"):
        hist = m.history[c]
        adj = (lambda t: season_adjustment(m.power, t, ds)) if c == "power" else (lambda t: 1.0)
        fwd_now[c] = float(hist.iloc[-FORWARD_LOOKBACK_DAYS:].mean() * adj(m.as_of) * (1 + FORWARD_PREMIUM))
        if ANCHOR_TO_FORWARD:
            # The market (forward / fixed price) sets the LEVEL, the model sets the RISK.
            # Scale the paths so that E[profile-weighted delivery spot] = fixed price / (1 + premium),
            # ramping the scale from 1 today to k at delivery start (a smooth "forward curve" bend).
            raw = _monthly_mean(sims[c], dates, ds)
            k = vol[c].sum() * fwd_now[c] / (1 + FORWARD_PREMIUM) / float((vol[c] @ raw).mean())
            span = max((ds - m.as_of).days, 1)
            ramp = 1 + (k - 1) * np.clip((dates - m.as_of).days.values / span, 0, 1)
            sims[c] = (sims[c] * ramp[:, None]).astype(np.float32)
            anchor_k[c] = float(k)
        spot_m[c] = _monthly_mean(sims[c], dates, ds)
        fw = []
        for t in tr:
            if t == m.as_of:
                fw.append(np.full(spot_m[c].shape[1], fwd_now[c]))
                continue
            x = _trailing_mean(sims[c], dates, hist, t) * adj(t) * (1 + FORWARD_PREMIUM)
            # a fixed price quoted later is a martingale: its expected value is today's fixed price
            fw.append(fwd_now[c] * x / x.mean() if ANCHOR_TO_FORWARD else x)
        fwd_tr[c] = np.mean(fw, axis=0)
        c_var[c] = vol[c] @ spot_m[c]
        c_fix[c] = vol[c].sum() * fwd_now[c]
        c_tr[c] = vol[c].sum() * fwd_tr[c]

    def total(hg, hp, fixed):
        return ((1 - hg) * c_var["gas"] + hg * fixed["gas"]) + ((1 - hp) * c_var["power"] + hp * fixed["power"])

    bill_scale = float(c_var["gas"].mean() + c_var["power"].mean())  # expected all-variable bill

    costs = {
        "All variable": total(0, 0, c_fix),
        "Fix 60% now": total(0.6, 0.6, c_fix),
        "Fix 100% now": total(1, 1, c_fix) * np.ones_like(c_var["gas"]),
        f"Tranches ({len(tr)}x, 100%)": total(1, 1, c_tr),
    }
    # HedgeWise: optimise hedge ratio per commodity AND execution (all now vs monthly tranches)
    grid = np.round(np.arange(0, 1.0001, 0.05), 2)
    if len(tr) == 1:
        execs = {"now": c_fix}
    else:
        execs = {"now": c_fix, "tranches": c_tr} if allow_timing else {"tranches": c_tr}
    rows = []
    for ex, fixed in execs.items():
        for hg in grid:
            for hp in grid:
                if SAME_RATIO and hg != hp:
                    continue
                cst = total(hg, hp, fixed)
                st = risk_stats(cst)
                if OBJECTIVE == "mean_variance":
                    # E[C] + (gamma/2) * Var[C] / B. Variance is quadratic in the hedge ratio, so the optimum is
                    # interior: h* ~ 1 - premium / (gamma * CV^2). (A mean-ES objective is LINEAR in h because
                    # ES is positively homogeneous, so it can only ever answer 0% or 100%.)
                    obj = st["expected"] + 0.5 * risk_aversion * float(cst.var()) / bill_scale
                else:
                    obj = (1 - risk_aversion) * st["expected"] + risk_aversion * st["es95"]
                rows.append({"execution": ex, "hedge_gas": hg, "hedge_power": hp, "expected": st["expected"],
                             "es95": st["es95"], "var95": st["var95"], "objective": obj})
    frontier = pd.DataFrame(rows)
    budget_eur = None
    if budget is not None:
        budget_eur = trailing_bill(m.history, company) * (1 + BUDGET_MARGIN) if budget == "auto" else float(budget)
        frontier["hedge_avg"] = (frontier.hedge_gas * vol["gas"].sum() + frontier.hedge_power * vol["power"].sum()
                                 ) / (vol["gas"].sum() + vol["power"].sum())
        ok = frontier[frontier.var95 <= budget_eur]
        best = (ok.sort_values(["hedge_avg", "expected"]).iloc[0] if len(ok)
                else frontier.loc[frontier.es95.idxmin()])
    else:
        best = frontier.loc[frontier.objective.idxmin()]
    ex = best.execution
    label = "now" if ex == "now" else f"{len(tr)} tranches"
    costs[f"HedgeWise (gas {best.hedge_gas:.0%}, power {best.hedge_power:.0%}, {label})"] = total(
        best.hedge_gas, best.hedge_power, execs[ex])
    table = pd.DataFrame({k: risk_stats(v) for k, v in costs.items()}).T
    table["cost_at_risk"] = table["es95"] - table["expected"]
    n_exec = 1 if ex == "now" else len(tr)
    rec = {"hedge_gas": float(best.hedge_gas), "hedge_power": float(best.hedge_power), "execution": ex,
           "risk_aversion": risk_aversion, "tranches": [str(t.date()) for t in (tr[:1] if ex == "now" else tr)],
           "buy_now_gas": float(best.hedge_gas / n_exec), "buy_now_power": float(best.hedge_power / n_exec),
           "fixed_price_now": fwd_now, "budget": budget_eur,
           "mode": "budget" if budget_eur is not None else "risk_aversion",
           "objective": OBJECTIVE, "anchor_scale": anchor_k}
    return Analysis(m, company, ds, tr, spot_m, fwd_now, fwd_tr, costs, table, frontier, rec, sims)


# --------------------------------------------------------------------------------------------
# Historical stress scenario: "what if 2022 happens again?"  (NOT a forecast)
# --------------------------------------------------------------------------------------------
STRESS_BASE = ("2021-01-01", "2021-06-30")   # the calm before the crisis
STRESS_YEAR = 2022


def stress_test(a: Analysis, df: pd.DataFrame) -> dict:
    """Replay the 2022 shock on today's price level.

    For gas and power separately: ratio[m] = realised average price in calendar month m of 2022 divided by the
    average price of H1 2021. The stressed price for delivery month m = today's fixed price x ratio[m].
    Bills for: all variable, the HedgeWise recommendation, fix 100% now, fix 60% now; the hedged part is
    priced at today's fixed price. A historical stress scenario, not a forecast."""
    ds = a.delivery_start
    order = [(ds + pd.DateOffset(months=i)).month for i in range(12)]           # calendar month per delivery month
    vol = {"gas": a.company.gas_mwh[[m - 1 for m in order]], "power": a.company.power_mwh[[m - 1 for m in order]]}
    out, ratios, stressed_avg = {}, {}, {}
    var_cost, fix_cost = {}, {}
    for c in ("gas", "power"):
        base = df.loc[STRESS_BASE[0]:STRESS_BASE[1], c].mean()
        y = df.loc[f"{STRESS_YEAR}-01-01":f"{STRESS_YEAR}-12-31", c]
        monthly = y.groupby(y.index.month).mean().reindex(range(1, 13))
        if not np.isfinite(base) or monthly.isna().any():
            raise ValueError("price data for H1 2021 and all of 2022 is needed for the stress test")
        r = (monthly / base).values
        ratios[c] = r
        stressed = a.fwd_now[c] * r[[m - 1 for m in order]]
        var_cost[c] = float(vol[c] @ stressed)
        fix_cost[c] = float(vol[c].sum() * a.fwd_now[c])
        stressed_avg[c] = float(vol[c] @ stressed / vol[c].sum()) if vol[c].sum() else float(stressed.mean())

    def bill(hg, hp):
        return ((1 - hg) * var_cost["gas"] + hg * fix_cost["gas"]) + ((1 - hp) * var_cost["power"] + hp * fix_cost["power"])

    rec = a.recommendation
    out = {"All variable": bill(0, 0), "HedgeWise": bill(rec["hedge_gas"], rec["hedge_power"]),
           "Fix 100% now": bill(1, 1), "Fix 60% now": bill(0.6, 0.6)}
    w = {c: float(vol[c] @ ratios[c][[m - 1 for m in order]] / vol[c].sum()) if vol[c].sum() else float("nan")
         for c in vol}
    return {**out, "multiplier": w, "stressed_avg_price": stressed_avg, "fixed_price": dict(a.fwd_now),
            "hedge": rec["hedge_gas"], "label": f"{STRESS_YEAR} repeat (historical stress, not a forecast)"}


def stress_sentence(s: dict, lang: str = "en") -> str:
    g, p = s["multiplier"]["gas"], s["multiplier"]["power"]
    if lang == "nl":
        nl = lambda x: f"{x:.2f}".replace(".", ",")
        gx, px = f"{g:.1f}".replace(".", ","), f"{p:.1f}".replace(".", ",")
        return (f"Historisch stressscenario, geen voorspelling: als de prijzen vanaf vandaag net zo stijgen als van "
                f"begin 2021 naar 2022 (gas ×{gx}, stroom ×{px}), kost variabel blijven "
                f"€ {nl(s['All variable']/1e6)} mln, het HedgeWise-advies € {nl(s['HedgeWise']/1e6)} mln en "
                f"100% vastzetten € {nl(s['Fix 100% now']/1e6)} mln.")
    f = lambda x: f"€{x/1e6:.2f}m"
    return (f"Historical stress scenario, not a forecast: if prices rose from today's level as much as they did from "
            f"early 2021 to 2022 (gas ×{g:.1f}, power ×{p:.1f}), staying variable would cost {f(s['All variable'])}, "
            f"the HedgeWise plan {f(s['HedgeWise'])} and fixing 100% now {f(s['Fix 100% now'])}.")


def plot_stress(s: dict, path=None, ax=None, lang: str = "en"):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    own = ax is None
    if own:
        fig, ax = plt.subplots(figsize=(8, 4))
    keys = ["All variable", "HedgeWise", "Fix 60% now", "Fix 100% now"]
    lbl = {"en": ["All variable", f"HedgeWise\n(fix {s['hedge']:.0%})", "Fix 60%\nnow", "Fix 100%\nnow"],
           "nl": ["Alles variabel", f"HedgeWise\n({s['hedge']:.0%} vast)", "60% vast\nnu", "100% vast\nnu"]}[lang]
    vals = [s[k] / 1e6 for k in keys]
    bars = ax.bar(lbl, vals, color=["#b8433a", "#1b9e5a", "#2a6fdb", "#6b7280"], width=0.6)
    for b, v in zip(bars, vals):
        txt = f"€{v:.2f}m" if lang == "en" else f"€ {v:.2f} mln".replace(".", ",")
        ax.text(b.get_x() + b.get_width() / 2, v, txt, ha="center", va="bottom", fontsize=10, weight="bold")
    ax.set_ylim(0, max(vals) * 1.15)
    ax.set_ylabel("€ m" if lang == "en" else "€ mln")
    ax.set_title("What if 2022 happens again? (historical stress, not a forecast)" if lang == "en"
                 else "Wat als 2022 zich herhaalt? (historisch stressscenario, geen voorspelling)", fontsize=11)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", alpha=0.25)
    if own:
        fig.tight_layout()
        fig.savefig(path, dpi=140, format="png")
        plt.close(fig)


BUDGET_MARGIN = 0.20


def trailing_bill(history: pd.DataFrame, company: Company) -> float:
    """What the company's consumption profile would have cost over the last 12 months, all variable."""
    h = history.iloc[-365:]
    total = 0.0
    for c, v in (("gas", company.gas_mwh), ("power", company.power_mwh)):
        mm = h[c].groupby(h.index.month).mean().reindex(range(1, 13)).ffill().bfill().values
        total += float(v @ mm)
    return total


def recommendation_text(a: Analysis) -> str:
    r, t = a.recommendation, a.table
    var_row, hw_row = t.loc["All variable"], t.iloc[-1]
    n = len(a.tranches)
    lines = [
        f"Delivery {a.delivery_start:%b %Y} - {(a.delivery_start + pd.DateOffset(months=11)):%b %Y}, "
        f"{a.company.name}",
        f"If you stay fully variable: expected bill EUR {var_row.expected/1e6:.2f}m, but in the worst 5% of "
        f"scenarios EUR {var_row.es95/1e6:.2f}m on average (worst simulated: EUR {var_row.worst/1e6:.2f}m).",
        (f"Budget EUR {r['budget']/1e6:.2f}m: the smallest hedge that keeps the bill within budget in 95% of "
         f"scenarios is to " if r["mode"] == "budget" else f"Recommendation (risk aversion {r['risk_aversion']}): ") +
        (f"fix {r['hedge_gas']:.0%} of next year's gas and power, " if np.isclose(r['hedge_gas'], r['hedge_power'])
         else f"fix {r['hedge_gas']:.0%} of gas and {r['hedge_power']:.0%} of power, ") + (
            "all of it now." if r["execution"] == "now" else
            f"spread over {n} monthly tranches ({', '.join(d[:7] for d in r['tranches'])}); "
            f"buy about {r['buy_now_gas']:.0%} gas / {r['buy_now_power']:.0%} power this month."),
        f"That cuts the bad-case bill (ES95) by EUR {(var_row.es95 - hw_row.es95)/1e3:,.0f}k " + (
            f"for an expected extra cost of EUR {(hw_row.expected - var_row.expected)/1e3:,.0f}k (the hedging premium)."
            if hw_row.expected >= var_row.expected else
            f"and is also EUR {(var_row.expected - hw_row.expected)/1e3:,.0f}k cheaper in expectation "
            f"(today's fixed price is below the model's expected spot price)."),
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------------------------
# Charts
# --------------------------------------------------------------------------------------------
COLORS = {"gas": "#2a6fdb", "power": "#e07b00", "co2": "#4f8a3a"}
STRAT_COLORS = ["#b8433a", "#8a8f98", "#4b5563", "#2a6fdb", "#1b9e5a"]


def plot_analysis(a: Analysis, path: Path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(2, 2, figsize=(14, 9))
    dates = a.sims["dates"]
    for ax, c in zip(axes[0], ("gas", "power")):
        hist = a.model.history[c].iloc[-730:]
        ax.plot(hist.index, hist.rolling(7).mean(), color=COLORS[c], lw=1.2, label="history (7-day avg)")
        # prepend the last 6 observed days so the 7-day average is a full window from day 1
        # (v0.2 used min_periods=1, which made the first simulated days look like a spike)
        raw = a.sims[c][:, :4000]
        pre = np.repeat(a.model.history[c].iloc[-6:].values[:, None], raw.shape[1], axis=1)
        p = pd.DataFrame(np.vstack([pre, raw])).rolling(7).mean().values[6:]
        qs = np.quantile(p, [0.05, 0.25, 0.5, 0.75, 0.95], axis=1)
        ax.fill_between(dates, qs[0], qs[4], color=COLORS[c], alpha=0.15, label="5-95% of scenarios")
        ax.fill_between(dates, qs[1], qs[3], color=COLORS[c], alpha=0.3, label="25-75%")
        ax.plot(dates, qs[2], color=COLORS[c], ls="--", lw=1, label="median")
        ax.axvspan(a.delivery_start, dates[-1], color="k", alpha=0.04)
        ax.set_title(f"{c.title()} price, EUR/MWh, 7-day avg (as of {a.model.as_of:%d %b %Y}); grey = delivery")
        ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%b %y"))
        ax.set_ylim(0, np.nanmax([hist.max(), qs[4].max()]) * 1.05)
        ax.legend(loc="upper left", fontsize=8, frameon=False)
        ax.grid(alpha=0.25)
    plot_frontier(a, axes[1, 0])
    ax = axes[1, 1]
    t = a.table
    y = np.arange(len(t))
    ax.barh(y, t.expected / 1e6, color="#cbd5e1", label="Expected")
    ax.barh(y, (t.es95 - t.expected) / 1e6, left=t.expected / 1e6, color="#ef8a62", label="extra in worst 5% (ES95)")
    for i, (e, es) in enumerate(zip(t.expected, t.es95)):
        ax.text(es / 1e6, i, f"  {es/1e6:.2f}", va="center", fontsize=8)
    ax.set_yticks(y, [n.replace(" (", "\n(") for n in t.index], fontsize=8)
    ax.invert_yaxis()
    ax.set_title("Expected bill vs bad-case bill (EUR m)")
    ax.legend(fontsize=8, frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.06), ncol=2)
    ax.set_xlim(0, t.es95.max() / 1e6 * 1.12)
    ax.grid(alpha=0.25, axis="x")
    fig.suptitle(f"HedgeWise - {a.company.name}", fontsize=13, weight="bold")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


FRONTIER_TEXT_EN = {
    "title": "Pick your trade-off: share of next year's energy fixed",
    "xlabel": "Bad-case bill: average of worst 5% of scenarios (EUR m)",
    "ylabel": "Expected bill (EUR m)",
    "pick": "HedgeWise pick: fix {h} (risk aversion {g})",
    "pick_budget": "HedgeWise pick: fix {h} (budget)",
    "tradeoff": "Each extra 10% fixed: about EUR {e}k more on average,\nEUR {es}k less in the bad case",
    "decimal": ".",
}


def plot_frontier(a: Analysis, ax=None, path=None, text: dict | None = None, show_tradeoff: bool = True):
    """Efficient frontier: expected bill vs bad-case bill (ES95) for one hedge ratio 0-100% (gas and power),
    executed the recommended way. The CFO picks a point; HedgeWise marks the one for their risk appetite.
    `text` overrides the labels (e.g. Dutch, see report.py); `path` may be a file path or a binary buffer."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter
    tx = {**FRONTIER_TEXT_EN, **(text or {})}
    own = ax is None
    if own:
        fig, ax = plt.subplots(figsize=(8, 5.5))
    r = a.recommendation
    f = a.frontier
    f = f[(f.execution == r["execution"]) & (np.isclose(f.hedge_gas, f.hedge_power))].sort_values("hedge_gas")
    ax.plot(f.es95 / 1e6, f.expected / 1e6, "-", color="#2a6fdb", lw=1.8, zorder=2)
    for _, row in f.iterrows():
        h = row.hedge_gas
        is_lbl = np.isclose(h % 0.25, 0) or np.isclose(h % 0.25, 0.25)
        ax.plot(row.es95 / 1e6, row.expected / 1e6, "o", ms=6 if is_lbl else 3, color="#2a6fdb", zorder=3)
        if is_lbl:
            ax.annotate(f"{h:.0%}", (row.es95 / 1e6, row.expected / 1e6), textcoords="offset points",
                        xytext=(4, 6), fontsize=9, color="#1e3a8a")
    rec = f[np.isclose(f.hedge_gas, r["hedge_gas"])]
    if len(rec):
        h_txt = f"{r['hedge_gas']:.0%}"
        lbl = (tx["pick_budget"] if r["mode"] == "budget" else tx["pick"]).format(
            h=h_txt, g=str(r["risk_aversion"]).replace(".", tx["decimal"]))
        ax.plot(rec.es95.iloc[0] / 1e6, rec.expected.iloc[0] / 1e6, "*", ms=18, color="#1b9e5a", zorder=4, label=lbl)
    if show_tradeoff and len(f) > 2:
        lo, hi = f.iloc[0], f.iloc[-1]
        d_e = (hi.expected - lo.expected) / 10 / 1e3
        d_es = (lo.es95 - hi.es95) / 10 / 1e3
        ax.text(0.03, 0.05, tx["tradeoff"].format(e=f"{d_e:,.0f}", es=f"{d_es:,.0f}"),
                transform=ax.transAxes, fontsize=9, color="#333")
    if tx["decimal"] == ",":
        fmt = FuncFormatter(lambda v, _: f"{v:.2f}".replace(".", ","))
        ax.xaxis.set_major_formatter(fmt)
        ax.yaxis.set_major_formatter(fmt)
    ax.margins(x=0.07, y=0.12)   # room for the 0% / 100% labels at the ends
    ax.set_xlabel(tx["xlabel"])
    ax.set_ylabel(tx["ylabel"])
    ax.set_title(tx["title"])
    ax.legend(fontsize=8, frameon=False, loc="upper right")
    ax.grid(alpha=0.25)
    if own:
        fig.tight_layout()
        fig.savefig(path, dpi=140, format="png")
        plt.close(fig)


def plot_fit(m: FittedModel, path: Path):
    """In-sample check: actual vs fitted power, and GARCH volatility over time."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    h = m.history
    d = h.iloc[-POWER_FIT_DAYS:]
    X = _power_design(d.index, {k: np.log(d[k].values) for k in m.drivers})
    fitted = np.exp(X @ m.power.coef)
    fig, ax = plt.subplots(1, 2, figsize=(14, 4.5))
    ax[0].plot(d.index, d.power, color=COLORS["power"], lw=0.7, label="actual day-ahead")
    ax[0].plot(d.index, fitted, color="k", lw=1, label=f"model (R2 = {m.power.r2:.2f})")
    ax[0].set_title("Power: actual vs explained by gas + seasonality")
    ax[0].legend(frameon=False)
    for k in m.drivers:
        g = m.garch[k]
        ax[1].plot(g.dates, np.sqrt(g.cond_var * 252) * 100, color=COLORS[k], lw=1, label=f"{k} (GARCH-t, nu={g.nu:.1f})")
    ax[1].set_title("Annualised volatility, %")
    ax[1].legend(frameon=False)
    for a_ in ax:
        a_.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def describe(m: FittedModel) -> str:
    lines = [f"Model fitted on data up to {m.as_of:%Y-%m-%d} ({len(m.history)} days)"]
    for k in m.drivers:
        g = m.garch[k]
        lines.append(f"  {k:5s} GARCH-t: alpha={g.alpha:.3f} beta={g.beta:.3f} nu={g.nu:.1f} | "
                     f"vol now {g.annual_vol_now:.0%}/yr, long-run {g.annual_vol_longrun:.0%}/yr | "
                     f"last price {m.last[k]:.1f}")
    pm = m.power
    b = dict(zip(sorted(m.drivers), pm.coef[1:1 + len(m.drivers)]))
    lines.append(f"  power: log P = ... + {' + '.join(f'{v:.2f}*log {k}' for k, v in b.items())} | "
                 f"AR(1) phi={pm.phi:.2f}, nu={pm.nu:.1f}, R2={pm.r2:.2f} | last price {m.last['power']:.1f}")
    names = m.drivers + ["power"]
    lines.append("  shock correlation: " + ", ".join(
        f"{names[i]}-{names[j]} {m.corr[i, j]:+.2f}" for i in range(len(names)) for j in range(i + 1, len(names))))
    return "\n".join(lines)


# --------------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--as-of", help="decision date YYYY-MM-DD (default: last date with data)")
    ap.add_argument("--delivery-start", help="first delivery month YYYY-MM-01 (default: next 1 January)")
    ap.add_argument("--risk-aversion", type=float, default=0.5, help="0 = only expected cost, 1 = only bad case")
    ap.add_argument("--paths", type=int, default=10_000)
    ap.add_argument("--seed", type=int, default=42)
    import company_input
    company_input.add_cli_args(ap)
    ap.add_argument("--solar-kwp", type=float, default=0.0)
    ap.add_argument("--demo", action="store_true", help="run on synthetic data")
    ap.add_argument("--show", action="store_true", help="open the chart window")
    ap.add_argument("--allow-timing", action="store_true", help="let HedgeWise fix everything at once")
    ap.add_argument("--budget", help="energy budget in EUR, or 'auto' (last 12 months + 20%%); "
                                     "switches to: smallest hedge that keeps VaR95 within budget")
    args = ap.parse_args(argv)

    OUT.mkdir(exist_ok=True)
    df = demo_prices() if args.demo else load_prices()
    company = company_input.company_from_args(args)
    company.solar_kwp = args.solar_kwp
    as_of = args.as_of or (str(df.index.max().date()) if not args.demo else "2021-06-30")
    m = fit(df, as_of)
    print(describe(m), "\n")
    a = analyse(m, company, args.delivery_start, args.paths, args.seed, args.risk_aversion,
                allow_timing=args.allow_timing, budget=args.budget)
    pd.set_option("display.width", 160)
    print((a.table / 1e3).round(0).astype(int).rename(columns=lambda c: c + " (kEUR)").to_string(), "\n")
    print("Fixed price available today (proxy): " +
          ", ".join(f"{k} {v:.1f} EUR/MWh" for k, v in a.fwd_now.items()))
    print(recommendation_text(a))
    if not args.demo:
        st_ = stress_test(a, df)
        print("\n" + stress_sentence(st_))
    tag = ("demo_" if args.demo else "") + f"{m.as_of:%Y-%m-%d}"
    plot_analysis(a, OUT / f"analysis_{tag}.png")
    plot_fit(m, OUT / f"fit_{tag}.png")
    if not args.demo:
        plot_stress(st_, OUT / f"stress_{tag}.png")
    a.table.to_csv(OUT / f"strategies_{tag}.csv")
    (OUT / f"recommendation_{tag}.json").write_text(json.dumps(a.recommendation, indent=2))
    print(f"\nCharts and tables written to {OUT}/  (analysis_{tag}.png, fit_{tag}.png)")
    if args.show:
        import matplotlib.pyplot as plt
        img = plt.imread(OUT / f"analysis_{tag}.png")
        plt.figure(figsize=(14, 9)); plt.imshow(img); plt.axis("off"); plt.show()
    return a


if __name__ == "__main__":
    main()
