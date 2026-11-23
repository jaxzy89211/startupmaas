"""Out-of-sample validation of the price model.

Every month end from validation.first_origin until the last month with a complete horizon:
  1. take the data up to that date only (strict: nothing after the origin is visible to the forecast),
  2. forecast the distribution of the average gas and power price over the next 1, 3 and 12 calendar months,
  3. compare with what happened.

Scores per variant, horizon and commodity:
  * coverage of the 5-95% band (target 90%) and of the 1-99% band (target 98%)
  * Kupiec (unconditional coverage) and Christoffersen (independence; conditional coverage) p-values for the 95%
    quantile, i.e. "realised above the forecast 95th percentile" should happen 5% of the time, not in clusters
  * CRPS (continuous ranked probability score, EUR/MWh, lower is better) and the pinball loss at 95%

Caveat: 3- and 12-month windows from consecutive month ends overlap, so their hits are serially dependent by
construction; the Kupiec/Christoffersen tests assume independent forecasts and are only exact for 1 month.

Variants (same origins, same seed, same test):
  a_garch_t            current model: GARCH(1,1)-t, anchored to the fixed-price proxy
  b_gjr_t              GJR-GARCH-t (price rises raise volatility more), anchored
  c_garch_jump         GARCH with Poisson jumps, anchored
  d_garch_t_no_anchor  current model without anchoring (the model's own level)
  e_bootstrap          historical block bootstrap of daily log changes (no model, no anchoring)

Anchoring in this test applies the product's rule to each forecast window: the forecast mean of a window is set to the
fixed-price proxy for that window divided by (1 + premium), i.e. the trailing 30-day spot mean (power: seasonally
adjusted to the window). The model supplies the shape and the spread around that level.

    python validation.py            # writes output/validation_table.csv, validation_forecasts.csv, validation.png
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats

from ._types import DateLike
from .models import DAYS_PER_YEAR, PowerModel, fit
from .provenance import one_line, save_figure, write_csv
from .settings import FORWARD_LOOKBACK_DAYS, OUT, POWER_FLOOR, VALIDATION
from .simulate import simulate

COMMODITIES = ("gas", "power")
VARIANTS = {
    "a_garch_t": {"variance": "garch_t", "anchor": True, "label": "a) GARCH-t (current)"},
    "b_gjr_t": {"variance": "gjr_t", "anchor": True, "label": "b) GJR-GARCH-t"},
    "c_garch_jump": {"variance": "garch_jump", "anchor": True, "label": "c) GARCH + jumps"},
    "d_garch_t_no_anchor": {"variance": "garch_t", "anchor": False, "label": "d) GARCH-t, no anchoring"},
    "e_bootstrap": {"variance": None, "anchor": False, "label": "e) block bootstrap (no model)"},
}
MODEL_VARIANTS = ["a_garch_t", "b_gjr_t", "c_garch_jump", "d_garch_t_no_anchor"]  # candidates for the default
CURRENT = "a_garch_t"
TAIL_Q = 0.95


# --------------------------------------------------------------------------------------------
# Forecast windows and realised values
# --------------------------------------------------------------------------------------------
def window(origin: DateLike, h: int) -> tuple[pd.Timestamp, pd.Timestamp]:
    """The h calendar months after the origin month end: (first day, last day)."""
    start = pd.Timestamp(origin) + pd.Timedelta(days=1)
    return start, start + pd.DateOffset(months=h) - pd.Timedelta(days=1)


def realised(df: pd.DataFrame, origin: DateLike, h: int) -> dict[str, float] | None:
    """Average gas and power price over the window (None if the window is not complete in the data)."""
    s, e = window(origin, h)
    if e > df.index.max():
        return None
    w = df.loc[s:e]
    return {c: float(w[c].mean()) for c in COMMODITIES}


def origins(df: pd.DataFrame, first: DateLike, h_min: int) -> list[pd.Timestamp]:
    """Month ends from `first` while at least the shortest horizon is complete."""
    ends = pd.date_range(pd.Timestamp(first), df.index.max(), freq="ME")
    return [o for o in ends if window(o, h_min)[1] <= df.index.max()]


# --------------------------------------------------------------------------------------------
# Forecasts (each uses df.loc[:origin] only)
# --------------------------------------------------------------------------------------------
def _season_level(pm: PowerModel, idx: pd.DatetimeIndex) -> float:
    n = len(pm.drivers)
    cs, cc = pm.coef[1 + n], pm.coef[2 + n]
    doy = np.asarray(idx.day_of_year)
    return float(
        np.exp(cs * np.sin(2 * np.pi * doy / DAYS_PER_YEAR) + cc * np.cos(2 * np.pi * doy / DAYS_PER_YEAR)).mean()
    )


def anchor_levels(hist: pd.DataFrame, pm: PowerModel, origin: DateLike, horizons: Iterable[int]) -> dict:
    """Fixed-price proxy / (1 + premium) per window = trailing 30-day spot mean (power seasonally adjusted)."""
    origin = pd.Timestamp(origin)
    last = pd.date_range(origin - pd.Timedelta(days=FORWARD_LOOKBACK_DAYS - 1), origin, freq="D")
    out = {}
    for h in horizons:
        s, e = window(origin, h)
        adj = _season_level(pm, pd.date_range(s, e, freq="D")) / _season_level(pm, last)
        out[h] = {
            "gas": float(hist["gas"].iloc[-FORWARD_LOOKBACK_DAYS:].mean()),
            "power": float(hist["power"].iloc[-FORWARD_LOOKBACK_DAYS:].mean() * adj),
        }
    return out


def _window_means(paths: dict, origin: pd.Timestamp, horizons: Iterable[int]) -> dict:
    dates = paths["dates"]
    out = {}
    for h in horizons:
        s, e = window(origin, h)
        mask = (dates >= s) & (dates <= e)
        out[h] = {c: paths[c][mask].mean(axis=0).astype(float) for c in COMMODITIES}
    return out


def model_forecasts(
    df: pd.DataFrame, origin: DateLike, variance: str, horizons: list[int], n_paths: int, seed: int
) -> tuple[dict, dict]:
    """(unanchored, anchored) samples {h: {commodity: array}} from the model fitted on data up to origin."""
    origin = pd.Timestamp(origin)
    hist = df.loc[:origin]
    m = fit(hist, origin, variance=variance)
    paths = simulate(m, window(origin, max(horizons))[1], n_paths, seed)
    raw = _window_means(paths, origin, horizons)
    levels = anchor_levels(hist, m.power, origin, horizons)
    anchored = {h: {c: raw[h][c] * (levels[h][c] / raw[h][c].mean()) for c in COMMODITIES} for h in horizons}
    return raw, anchored


def bootstrap_forecasts(
    df: pd.DataFrame, origin: DateLike, horizons: list[int], n_paths: int, seed: int, block: int
) -> dict:
    """Historical block bootstrap (no model): paths built from random 'block'-day blocks of joint daily log changes
    of gas and power observed up to the origin, starting from the last observed levels."""
    origin = pd.Timestamp(origin)
    hist = df.loc[:origin]
    logs = np.column_stack([np.log(hist["gas"].values), np.log(np.maximum(hist["power"].values, POWER_FLOOR))])
    d = np.diff(logs, axis=0)
    rng = np.random.default_rng(seed)
    n_days = (window(origin, max(horizons))[1] - origin).days
    n_blocks = -(-n_days // block)
    starts = rng.integers(0, len(d) - block + 1, size=(n_paths, n_blocks))
    steps = d[starts[:, :, None] + np.arange(block)[None, None, :]]  # (paths, blocks, block, 2)
    steps = steps.reshape(n_paths, n_blocks * block, 2)[:, :n_days]
    level = logs[-1] + np.cumsum(steps, axis=1)  # (paths, days, 2)
    dates = pd.date_range(origin + pd.Timedelta(days=1), periods=n_days, freq="D")
    paths = {"dates": dates, "gas": np.exp(level[:, :, 0]).T, "power": np.exp(level[:, :, 1]).T}
    return _window_means(paths, origin, horizons)


def forecast(df: pd.DataFrame, origin: DateLike, horizons: list[int], n_paths: int, seed: int) -> dict:
    """All five variants for one origin: {variant: {h: {commodity: samples}}}. Uses df.loc[:origin] only."""
    hist = df.loc[: pd.Timestamp(origin)]  # nothing after the origin goes any further
    out = {}
    raw_t, anch_t = model_forecasts(hist, origin, "garch_t", horizons, n_paths, seed)
    out["a_garch_t"], out["d_garch_t_no_anchor"] = anch_t, raw_t
    out["b_gjr_t"] = model_forecasts(hist, origin, "gjr_t", horizons, n_paths, seed)[1]
    out["c_garch_jump"] = model_forecasts(hist, origin, "garch_jump", horizons, n_paths, seed)[1]
    out["e_bootstrap"] = bootstrap_forecasts(hist, origin, horizons, n_paths, seed, VALIDATION["bootstrap_block_days"])
    return out


# --------------------------------------------------------------------------------------------
# Scores
# --------------------------------------------------------------------------------------------
def crps_sample(x: np.ndarray, y: float) -> float:
    """CRPS of a sample forecast x for outcome y: E|X - y| - 0.5 E|X - X'| (exact for the empirical distribution)."""
    x = np.sort(np.asarray(x, float))
    n = len(x)
    e_xy = np.abs(x - y).mean()
    e_xx = 2.0 * np.sum((2 * np.arange(1, n + 1) - n - 1) * x) / n**2
    return float(e_xy - 0.5 * e_xx)


def pinball(q: float, y: float, tau: float = TAIL_Q) -> float:
    """Pinball (quantile) loss of the tau-quantile forecast q for outcome y."""
    return float((y - q) * (tau - (y < q)))


def _xlogy(x: float, y: float) -> float:
    return 0.0 if x == 0 else x * np.log(y)


def kupiec(hits: np.ndarray, p: float = 1 - TAIL_Q) -> float:
    """Kupiec proportion-of-failures test p-value (H0: the hit rate is p)."""
    n, x = len(hits), int(np.sum(hits))
    if n == 0:
        return float("nan")
    phat = x / n
    lr = -2 * (_xlogy(n - x, 1 - p) + _xlogy(x, p) - _xlogy(n - x, 1 - phat) - _xlogy(x, phat))
    return float(stats.chi2.sf(lr, 1))


def christoffersen(hits: np.ndarray, p: float = 1 - TAIL_Q) -> tuple[float, float]:
    """Christoffersen test p-values: (independence of hits, conditional coverage = Kupiec + independence)."""
    h = np.asarray(hits, int)
    if len(h) < 2:
        return float("nan"), float("nan")
    a, b = h[:-1], h[1:]
    n00, n01 = int(np.sum((a == 0) & (b == 0))), int(np.sum((a == 0) & (b == 1)))
    n10, n11 = int(np.sum((a == 1) & (b == 0))), int(np.sum((a == 1) & (b == 1)))
    p01 = n01 / (n00 + n01) if n00 + n01 else 0.0
    p11 = n11 / (n10 + n11) if n10 + n11 else 0.0
    p1 = (n01 + n11) / (n00 + n01 + n10 + n11)
    ll0 = _xlogy(n00 + n10, 1 - p1) + _xlogy(n01 + n11, p1)
    ll1 = _xlogy(n00, 1 - p01) + _xlogy(n01, p01) + _xlogy(n10, 1 - p11) + _xlogy(n11, p11)
    lr_ind = max(-2 * (ll0 - ll1), 0.0)
    n, x = len(h), int(h.sum())
    phat = x / n
    lr_pof = -2 * (_xlogy(n - x, 1 - p) + _xlogy(x, p) - _xlogy(n - x, 1 - phat) - _xlogy(x, phat))
    return float(stats.chi2.sf(lr_ind, 1)), float(stats.chi2.sf(lr_pof + lr_ind, 2))


def score_row(x: np.ndarray, y: float) -> dict[str, float]:
    """Per-forecast scores: quantiles, band hits, 95% exceedance, CRPS and pinball loss."""
    q01, q05, q95, q99 = np.quantile(x, [0.01, 0.05, TAIL_Q, 0.99])
    return {
        "realised": y,
        "mean": float(np.mean(x)),
        "q01": q01,
        "q05": q05,
        "q95": q95,
        "q99": q99,
        "in_90": float(q05 <= y <= q95),
        "in_98": float(q01 <= y <= q99),
        "hit_95": float(y > q95),
        "crps": crps_sample(x, y),
        "pinball_95": pinball(q95, y),
    }


def summarise(fc: pd.DataFrame) -> pd.DataFrame:
    """Aggregate per-forecast scores into the validation table (one row per variant, commodity, horizon)."""
    rows = []
    for (v, c, h), g in fc.sort_values("origin").groupby(["variant", "commodity", "horizon"]):
        hits = g["hit_95"].values.astype(int)
        ind_p, cc_p = christoffersen(hits)
        rows.append(
            {
                "variant": v,
                "label": VARIANTS[v]["label"],
                "commodity": c,
                "horizon_months": h,
                "n": len(g),
                "coverage_90": g["in_90"].mean(),
                "coverage_98": g["in_98"].mean(),
                "hit_rate_95": hits.mean(),
                "kupiec_p": kupiec(hits),
                "christoffersen_ind_p": ind_p,
                "christoffersen_cc_p": cc_p,
                "crps": g["crps"].mean(),
                "pinball_95": g["pinball_95"].mean(),
            }
        )
    t = pd.DataFrame(rows)
    cur = t[t.variant == CURRENT].set_index(["commodity", "horizon_months"])["crps"]
    t["crps_rel_current"] = [r.crps / cur[(r.commodity, r.horizon_months)] for r in t.itertuples()]
    return t


@dataclass
class Choice:
    """Outcome of the pre-registered selection rule."""

    default: str
    changed: bool
    scores: pd.DataFrame
    reason: str


def choose_default(table: pd.DataFrame, margin: float, coverage_ok: tuple[float, float]) -> Choice:
    """Pre-registered rule: lowest mean 12-month CRPS relative to the current model (gas & power averaged) among the
    model variants, if more than `margin` better than current AND 12-month 5-95% coverage within `coverage_ok` for
    both commodities; else keep the current model. The bootstrap benchmark is never chosen."""
    t12 = table[table.horizon_months == max(table.horizon_months)]
    sc = (
        t12.groupby("variant")
        .agg(crps_rel=("crps_rel_current", "mean"), cov_min=("coverage_90", "min"), cov_max=("coverage_90", "max"))
        .reset_index()
    )
    sc["coverage_ok"] = (sc.cov_min >= coverage_ok[0]) & (sc.cov_max <= coverage_ok[1])
    cand = sc[sc.variant.isin(MODEL_VARIANTS) & sc.coverage_ok].sort_values("crps_rel")
    if cand.empty:
        return Choice(CURRENT, False, sc, "no model variant has acceptable 12-month coverage: keep the current model")
    best = cand.iloc[0]
    if best.variant != CURRENT and best.crps_rel < 1 - margin:
        return Choice(
            best.variant, True, sc, f"{best.variant}: 12-month CRPS {1 - best.crps_rel:.1%} better, coverage acceptable"
        )
    return Choice(CURRENT, False, sc, f"no acceptable variant is more than {margin:.0%} better on 12-month CRPS")


# --------------------------------------------------------------------------------------------
# Run
# --------------------------------------------------------------------------------------------
def run(
    df: pd.DataFrame, first: DateLike, horizons: list[int], n_paths: int, seed: int, verbose: bool = True
) -> pd.DataFrame:
    """Per-forecast scores for every origin, variant, horizon and commodity."""
    rows = []
    origs = origins(df, first, min(horizons))
    for i, o in enumerate(origs):
        fc = forecast(df, o, horizons, n_paths, seed)
        for h in horizons:
            y = realised(df, o, h)
            if y is None:
                continue
            for v, by_h in fc.items():
                for c in COMMODITIES:
                    rows.append(
                        {"variant": v, "origin": o, "horizon": h, "commodity": c, **score_row(by_h[h][c], y[c])}
                    )
        if verbose and (i % 6 == 0 or i == len(origs) - 1):
            print(f"  {i + 1}/{len(origs)} origins done (last: {o.date()})", flush=True)
    return pd.DataFrame(rows)


def plot_validation(table: pd.DataFrame, fc: pd.DataFrame, path: str) -> None:
    """Coverage and CRPS per variant and horizon, plus the current model's 12-month bands over time."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    colors = {
        "a_garch_t": "#1b9e5a",
        "b_gjr_t": "#2a6fdb",
        "c_garch_jump": "#c9a227",
        "d_garch_t_no_anchor": "#7c3aed",
        "e_bootstrap": "#8a8f98",
    }
    hs = sorted(table.horizon_months.unique())
    fig, axes = plt.subplots(3, 2, figsize=(15, 13))
    w = 0.16
    for j, c in enumerate(COMMODITIES):
        t = table[table.commodity == c]
        for row, (metric, title) in enumerate(
            [
                ("coverage_90", "coverage of the 5-95% band (target 90%)"),
                ("crps_rel_current", "CRPS relative to the current model (lower = better)"),
            ]
        ):
            ax = axes[row, j]
            for i, v in enumerate(VARIANTS):
                vals = [t[(t.variant == v) & (t.horizon_months == h)][metric].iloc[0] for h in hs]
                xs = np.arange(len(hs)) + (i - 2) * w
                ax.bar(xs, vals, w * 0.92, color=colors[v], label=VARIANTS[v]["label"])
                for x_, v_ in zip(xs, vals, strict=False):
                    ax.text(
                        x_,
                        v_,
                        f"{v_:.0%}" if metric == "coverage_90" else f"{v_:.2f}",
                        ha="center",
                        va="bottom",
                        fontsize=7,
                    )
            if metric == "coverage_90":
                ax.axhspan(0.80, 0.95, color="#1b9e5a", alpha=0.08, label="acceptable (80-95%)")
                ax.axhline(0.90, color="k", lw=0.8, ls="--")
                ax.set_ylim(0, 1.12)
            else:
                ax.axhline(1.0, color="k", lw=0.8, ls="--")
            ax.set_xticks(np.arange(len(hs)), [f"{h} month{'s' if h > 1 else ''}" for h in hs])
            ax.set_title(f"{c.title()}: {title}", fontsize=11)
            ax.grid(axis="y", alpha=0.25)
            ax.spines[["top", "right"]].set_visible(False)
        ax = axes[2, j]
        g = fc[(fc.variant == CURRENT) & (fc.commodity == c) & (fc.horizon == max(hs))].sort_values("origin")
        ax.fill_between(g.origin, g.q05, g.q95, color="#1b9e5a", alpha=0.2, label="current model 5-95%")
        ax.plot(g.origin, g["mean"], color="#1b9e5a", lw=1, ls="--", label="forecast mean (anchored)")
        ax.plot(g.origin, g.realised, color="#b8433a", lw=1.8, label="realised next-12-month average")
        miss = g[g.in_90 == 0]
        ax.scatter(miss.origin, miss.realised, color="#b8433a", zorder=3, s=18, label="outside the band")
        ax.set_title(f"{c.title()}: 12-month forecast made at each month end vs what happened (EUR/MWh)", fontsize=11)
        ax.set_yscale("log")
        ax.grid(alpha=0.25)
        ax.spines[["top", "right"]].set_visible(False)
    axes[0, 0].legend(fontsize=8, frameon=False, loc="upper left", ncol=2)
    axes[2, 0].legend(fontsize=8, frameon=False, loc="upper right")
    fig.suptitle(
        "HedgeWise out-of-sample validation (each forecast uses only data available on its date)",
        fontsize=13,
        weight="bold",
    )
    fig.tight_layout()
    save_figure(fig, path, dpi=130)
    plt.close(fig)


def main(argv: list[str] | None = None) -> pd.DataFrame:
    """Run the validation, write the table, per-forecast scores and chart; print the selection-rule outcome."""
    from .data import load_prices

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--first-origin", default=VALIDATION["first_origin"])
    ap.add_argument("--paths", type=int, default=VALIDATION["n_paths"])
    ap.add_argument("--seed", type=int, default=VALIDATION["seed"])
    args = ap.parse_args(argv)
    OUT.mkdir(exist_ok=True)
    df = load_prices()
    hs = list(VALIDATION["horizons_months"])
    fc = run(df, args.first_origin, hs, args.paths, args.seed)
    table = summarise(fc)
    write_csv(fc, OUT / "validation_forecasts.csv", index=False)
    write_csv(table, OUT / "validation_table.csv", index=False)
    plot_validation(table, fc, OUT / "validation.png")
    pd.set_option("display.width", 220)
    show = table.copy()
    for col in ("coverage_90", "coverage_98", "hit_rate_95"):
        show[col] = (show[col] * 100).round(0).astype(int).astype(str) + "%"
    for col in ("kupiec_p", "christoffersen_ind_p", "christoffersen_cc_p", "crps_rel_current"):
        show[col] = show[col].round(3)
    for col in ("crps", "pinball_95"):
        show[col] = show[col].round(2)
    print(show.drop(columns=["label"]).to_string(index=False))
    ch = choose_default(table, VALIDATION["crps_margin"], tuple(VALIDATION["coverage_ok"]))
    print("\n12-month selection scores:\n" + ch.scores.round(3).to_string(index=False))
    print(f"\nDefault: {ch.default} ({'CHANGED' if ch.changed else 'unchanged'}) - {ch.reason}")
    print("Provenance: " + one_line())
    return table
