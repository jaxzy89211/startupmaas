"""Robustness of the procurement strategies beyond the one real 2021-2025 history.

Every history = the real data up to today, followed by ~9 synthetic future years. The backtest (same strategies,
decision rules and fixed-price proxy as backtest.py; the HedgeWise strategies re-fit the model at every decision
date on the history up to that date) then runs on the 8 future delivery years.

History families:
  model      1,000 futures simulated from the default model fitted on all data up to today
  bootstrap  1,000 futures from a historical block bootstrap (30-day blocks of joint daily log changes, 2019-today)
  stress     3 named histories, defined before any result:
               "2022 repeats"      a shock as big as 2021->2022, from today: calm in 2027; in 2028-29 today's level x
                                   (real 2022-23 daily price / real 2021 average), i.e. 2028 averages 2.8x (gas) and
                                   2.3x (power) today's level with the real 2022 spikes; then calm noise
               "slow 3-year rise"  prices x3 in a straight (log) line over 3 years from mid-2027, then flat
               "spike then crash"  from Jan 2028: x4 in 3 months, hold 6 months, fall to 0.5x today's level in 6 months
             (the two designed paths carry calm day-to-day noise: real 2024-25 daily changes, demeaned)

Per strategy: median 8-year total, 95th percentile of the worst year and of the biggest year-on-year jump, and the
share of histories where it beats all-variable on the worst year AND costs < 8% more in total.

Speed: the HedgeWise strategies use ROBUSTNESS_PATHS scenarios per decision (instead of 4,000 in backtest.py), and
model fits are cached per history (a fit is deterministic, so this changes nothing but the run time).

    python robustness.py [--histories 1000] [--workers 4] [--chunk-size 20]

Runs in chunks; every finished chunk is saved under output/robustness_chunks/, so a crash loses at most one
chunk and rerunning the same command resumes.
"""

from __future__ import annotations

import argparse
import os
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd

import hedgewise as hw
from hedgewise import backtest as bt
from hedgewise.provenance import current, one_line, save_figure, write_csv
from hedgewise.settings import BACKTEST, POWER_FLOOR

ROBUSTNESS_PATHS = 300  # scenarios per HedgeWise decision (backtest.py: 4,000) - for speed, see docstring
FUTURE_YEARS = 8
EXTRA_COST = 0.08  # "costs less than 8% more in total than all-variable"
BLOCK_DAYS = 30
LAMBDAS = [BACKTEST["policy_gamma"]]  # one risk aversion for the HedgeWise optimiser column (speed)
SEED0 = 10_000

_STATE: dict = {}


# --------------------------------------------------------------------------------------------
# Building synthetic histories (real data up to today + synthetic future)
# --------------------------------------------------------------------------------------------
def future_index(real: pd.DataFrame, first_delivery_year: int) -> pd.DatetimeIndex:
    """Calendar days from the day after the real data to the end of the last delivery year."""
    end = pd.Timestamp(first_delivery_year + FUTURE_YEARS - 1, 12, 31)
    return pd.date_range(real.index.max() + pd.Timedelta(days=1), end, freq="D")


def _append(real: pd.DataFrame, idx: pd.DatetimeIndex, gas: np.ndarray, power: np.ndarray) -> pd.DataFrame:
    fut = pd.DataFrame({"power": power.astype(float), "gas": gas.astype(float)}, index=idx)
    fut["gas_trade"] = fut.index.day_of_week < 5
    return pd.concat([real[["power", "gas", "gas_trade"]], fut])


def model_history(real: pd.DataFrame, m: hw.FittedModel, idx: pd.DatetimeIndex, seed: int) -> pd.DataFrame:
    """One future simulated from the fitted default model (raw paths, no anchoring)."""
    s = hw.simulate(m, idx[-1], 1, seed)
    return _append(real, idx, s["gas"][:, 0], s["power"][:, 0])


def _log_changes(real: pd.DataFrame) -> np.ndarray:
    logs = np.column_stack([np.log(real["gas"].values), np.log(np.maximum(real["power"].values, POWER_FLOOR))])
    return np.diff(logs, axis=0)


def bootstrap_history(real: pd.DataFrame, idx: pd.DatetimeIndex, seed: int, block: int = BLOCK_DAYS) -> pd.DataFrame:
    """One future built from random `block`-day blocks of the real joint daily log changes."""
    d = _log_changes(real)
    rng = np.random.default_rng(seed)
    n = len(idx)
    starts = rng.integers(0, len(d) - block + 1, size=-(-n // block))
    steps = np.concatenate([d[s : s + block] for s in starts])[:n]
    last = np.log([real["gas"].iloc[-1], max(real["power"].iloc[-1], POWER_FLOOR)])
    level = last + np.cumsum(steps, axis=0)
    return _append(real, idx, np.exp(level[:, 0]), np.exp(level[:, 1]))


def _calm_noise(real: pd.DataFrame, idx: pd.DatetimeIndex) -> np.ndarray:
    """Demeaned real daily log changes from 2024-25 (104 weeks, weekday-aligned with idx), repeated."""
    calm = real.loc["2024-01-01":"2025-12-31"]
    first = calm.index[calm.index.day_of_week == idx[0].day_of_week][0]
    seg = real.loc[first : first + pd.Timedelta(days=727)]
    d = _log_changes(pd.concat([real.loc[:first].iloc[-1:], seg]))
    d = d - d.mean(axis=0)
    reps = -(-len(idx) // len(d))
    return np.tile(d, (reps, 1))[: len(idx)]


def stress_history(real: pd.DataFrame, idx: pd.DatetimeIndex, name: str) -> pd.DataFrame:
    """The three named stress futures (see module docstring)."""
    last = np.log([real["gas"].iloc[-1], max(real["power"].iloc[-1], POWER_FLOOR)])
    if name == "2022 repeats":  # a shock as big as 2021 -> 2022, from today (same definition as the stress test)
        cols = ["gas", "power"]
        now = np.log(real[cols].iloc[-30:].mean().values)  # today's 30-day average
        avg21 = real.loc["2021-01-01":"2021-12-31", cols].mean().values
        shock = np.log(np.maximum(real.loc["2022-01-01":"2023-12-31", cols].values, POWER_FLOOR) / avg21)
        noise = np.cumsum(_calm_noise(real, idx), axis=0)
        s0 = int((idx < pd.Timestamp("2028-01-01")).sum())  # calm until the end of 2027
        before = now + noise[:s0]
        during = now + shock
        tail = len(idx) - s0 - len(during)
        after = np.log(np.exp(during[-30:]).mean(axis=0)) + noise[s0 + len(during) :] - noise[s0 + len(during) - 1]
        level = np.vstack([before, during, after])[: len(idx)] if tail > 0 else np.vstack([before, during])[: len(idx)]
    else:
        t = np.asarray((idx - idx[0]).days, float)
        if name == "slow 3-year rise":
            t0 = (pd.Timestamp("2027-07-01") - idx[0]).days
            trend = np.log(3.0) * np.clip((t - t0) / (3 * 365), 0, 1)
        elif name == "spike then crash":
            t0 = (pd.Timestamp("2028-01-01") - idx[0]).days
            pts = [t0, t0 + 91, t0 + 274, t0 + 456]  # up 3 months, hold 6, down 6
            trend = np.interp(t, pts, [0.0, np.log(4.0), np.log(4.0), np.log(0.5)], left=0.0, right=np.log(0.5))
        else:
            raise ValueError(f"unknown stress history {name!r}")
        level = last + trend[:, None] + np.cumsum(_calm_noise(real, idx), axis=0)
    return _append(real, idx, np.exp(level[:, 0]), np.exp(level[:, 1]))


STRESS_NAMES = ["2022 repeats", "slow 3-year rise", "spike then crash"]


# --------------------------------------------------------------------------------------------
# Running the backtest on one history
# --------------------------------------------------------------------------------------------
def run_history(
    df: pd.DataFrame, years: list[int], company: hw.Company, n_paths: int = ROBUSTNESS_PATHS, seed: int = 1
) -> pd.DataFrame:
    """Realised bill per delivery year and strategy (rows: years) - same rules as backtest.py.

    Model fits are cached per decision date for this history (deterministic, so results are unchanged)."""
    cache: dict = {}
    real_fit = hw.fit

    def cached_fit(d: pd.DataFrame, as_of=None, variance=None) -> hw.FittedModel:  # noqa: ANN001
        key = (pd.Timestamp(as_of) if as_of is not None else d.index.max(), len(d.loc[:as_of]), variance)
        if key not in cache:
            cache[key] = real_fit(d, as_of, variance)
        return cache[key]

    hw.fit = cached_fit
    try:
        rows, prev = {}, None
        for y in years:
            rows[y], info = bt.run_year(
                df,
                company,
                y,
                LAMBDAS,
                n_paths,
                seed,
                BACKTEST["typical_month"],
                verbose=False,
                prev_ladder_target=prev,
            )
            prev = info["ladder_final_target"]
    finally:
        hw.fit = real_fit
    return pd.DataFrame(rows).T


def history_metrics(res: pd.DataFrame) -> pd.DataFrame:
    """Per strategy: 8-year total, worst year, biggest year-on-year increase."""
    return pd.DataFrame({"total": res.sum(), "worst_year": res.max(), "max_jump": res.diff().max()})


def _init_worker() -> None:
    real = hw.load_prices()
    _STATE["real"] = real
    _STATE["model"] = hw.fit(real)
    _STATE["company"] = hw.Company()
    first = real.index.max().year + 2
    _STATE["years"] = list(range(first, first + FUTURE_YEARS))
    _STATE["idx"] = future_index(real, first)


def _task(task: tuple[str, int | str]) -> pd.DataFrame:
    family, key = task
    real, idx = _STATE["real"], _STATE["idx"]
    if family == "model":
        df = model_history(real, _STATE["model"], idx, SEED0 + int(key))
    elif family == "bootstrap":
        df = bootstrap_history(real, idx, SEED0 + int(key))
    else:
        df = stress_history(real, idx, str(key))
    met = history_metrics(run_history(df, _STATE["years"], _STATE["company"]))
    met["family"], met["history"] = family, str(key)
    return met.reset_index(names="strategy")


# --------------------------------------------------------------------------------------------
# Summary and chart
# --------------------------------------------------------------------------------------------
def summarise(per: pd.DataFrame) -> pd.DataFrame:
    """Per family and strategy: median total, P95 worst year, P95 jump, % 'beats all-variable' (definition above)."""
    base = per[per.strategy == "All variable"].set_index(["family", "history"])
    p = per.join(base[["total", "worst_year"]].add_prefix("var_"), on=["family", "history"])
    p["wins"] = (p.worst_year < p.var_worst_year) & (p.total < (1 + EXTRA_COST) * p.var_total)
    g = p.groupby(["family", "strategy"], sort=False)
    out = g.agg(
        n=("total", "size"),
        median_total=("total", "median"),
        total_p05=("total", lambda x: x.quantile(0.05)),
        total_p95=("total", lambda x: x.quantile(0.95)),
        median_worst_year=("worst_year", "median"),
        worst_year_p05=("worst_year", lambda x: x.quantile(0.05)),
        p95_worst_year=("worst_year", lambda x: x.quantile(0.95)),
        p95_max_jump=("max_jump", lambda x: x.quantile(0.95)),
        pct_beats_variable=("wins", "mean"),
    )
    return out.reset_index()


COLORS = {
    "All variable": "#b8433a",
    "Fix 100% in Oct": "#6b7280",
    "Fix 60% at once (Jul)": "#c9a227",
    "Tranches 6x (100%)": "#2a6fdb",
    "Tranches 6x (60%)": "#93c5fd",
    "Layered 2 years (40+40%)": "#1b9e5a",
    "HedgeWise policy (gamma 0.5)": "#7c3aed",
    "HedgeWise ladder (40% + capped top-up)": "#0e7490",
    "HedgeWise (risk aversion 0.5)": "#f472b6",
}


def plot_robustness(summary: pd.DataFrame, per: pd.DataFrame, path: str) -> None:
    """Cost vs risk: one dot per strategy (median 8-year total vs median worst year), 5-95% whiskers both ways."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fams = [f for f in ("model", "bootstrap") if f in set(summary.family)]
    fig, axes = plt.subplots(
        1, len(fams) + 1, figsize=(7.2 * len(fams) + 6.5, 8.2), gridspec_kw={"width_ratios": [1] * len(fams) + [0.95]}
    )
    from matplotlib.ticker import FuncFormatter, LogLocator, NullFormatter

    titles = {
        "model": "{n:,} futures from the default model",
        "bootstrap": "{n:,} futures from a block bootstrap\nWARNING: price paths fail the sanity check",
    }
    for ax, fam in zip(axes, fams, strict=False):
        s = summary[summary.family == fam]
        for r in s.itertuples():
            c = COLORS.get(r.strategy, "k")
            x, y = r.median_total / 1e6, r.median_worst_year / 1e6
            ax.errorbar(
                x,
                y,
                xerr=[[x - r.total_p05 / 1e6], [r.total_p95 / 1e6 - x]],
                yerr=[[y - r.worst_year_p05 / 1e6], [r.p95_worst_year / 1e6 - y]],
                fmt="o",
                color=c,
                ms=9,
                capsize=3,
                lw=1.2,
                alpha=0.9,
                label=f"{r.strategy}  ({r.pct_beats_variable:.0%})",
            )
        ax.set_xscale("log")
        ax.set_yscale("log")
        ax.set_xlabel("8-year total bill, EUR m (dot = median, whiskers = 5-95%)")
        ax.set_ylabel("Worst single year, EUR m (dot = median, whiskers = 5-95%)")
        ax.set_title(
            titles[fam].format(n=int(s.n.iloc[0])) + "\n(legend: % of histories that beat all-variable)", fontsize=10
        )
        ax.grid(alpha=0.25, which="both")
        for axis in (ax.xaxis, ax.yaxis):
            axis.set_major_locator(LogLocator(subs=(1, 2, 5) if fam == "model" else (1,)))
            axis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}" if v >= 1 else f"{v:g}"))
            axis.set_minor_formatter(NullFormatter())
        ax.legend(fontsize=7.5, frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=2)
    ax = axes[-1]
    st = per[per.family == "stress"]
    names = list(dict.fromkeys(st.history))
    strategies = list(dict.fromkeys(st.strategy))
    w = 0.8 / len(strategies)
    for i, strat in enumerate(strategies):
        vals = [st[(st.history == n) & (st.strategy == strat)].total.iloc[0] / 1e6 for n in names]
        ax.barh(
            np.arange(len(names)) + (i - len(strategies) / 2 + 0.5) * w,
            vals,
            w * 0.92,
            color=COLORS.get(strat, "k"),
            label=strat,
        )
    ax.set_yticks(np.arange(len(names)), names)
    ax.invert_yaxis()
    ax.set_xlabel("8-year total bill, EUR m")
    ax.set_title("Named stress histories (one path each)", fontsize=11)
    ax.grid(alpha=0.25, axis="x")
    fig.suptitle(
        "Robustness: cost vs risk of each procurement strategy over 8 future delivery years", fontsize=13, weight="bold"
    )
    fig.tight_layout()
    save_figure(fig, path, dpi=130)
    plt.close(fig)


def _run_dir(n_paths: int, chunk_size: int) -> Path:
    """Where finished chunks are saved; the name pins everything that changes a result, so stale chunks are
    never mixed in (config, data, scenarios per decision, chunk size)."""
    prov = current()
    tag = f"cfg{prov['config_hash']}_data{prov.get('data_hash', 'none')}_paths{n_paths}_chunk{chunk_size}"
    return hw.OUT / "robustness_chunks" / tag


def make_tasks(histories: int) -> list[tuple[str, int | str]]:
    """Stress histories first, then model/bootstrap interleaved. The order only grows at the end when
    `histories` grows, so chunks saved by a smaller run are reused by a bigger one."""
    tasks: list[tuple[str, int | str]] = [("stress", n) for n in STRESS_NAMES]
    return tasks + [(f, i) for i in range(histories) for f in ("model", "bootstrap")]


def _task_ref(task: tuple[str, int | str]) -> pd.DataFrame:
    """Module-level indirection so tests can replace `_task` (workers=1)."""
    return _task(task)


def run_chunked(histories: int, workers: int, chunk_size: int) -> pd.DataFrame:
    """Run all tasks in chunks of `chunk_size`, saving each finished chunk to disk before starting the next.
    A crash loses at most the chunk in progress; rerunning the same command resumes from the saved chunks."""
    tasks = make_tasks(histories)
    chunks = [tasks[i : i + chunk_size] for i in range(0, len(tasks), chunk_size)]
    run_dir = _run_dir(ROBUSTNESS_PATHS, chunk_size)
    run_dir.mkdir(parents=True, exist_ok=True)

    def keys(chunk: list[tuple[str, int | str]]) -> set[tuple[str, str]]:
        return {(f, str(h)) for f, h in chunk}

    def done(k: int) -> bool:  # saved, and covers every task of this chunk (a smaller run saves a shorter last one)
        f = run_dir / f"chunk_{k:04d}.csv"
        if not f.exists():
            return False
        saved = pd.read_csv(f, usecols=["family", "history"], dtype=str)
        return keys(chunks[k]) <= set(zip(saved.family, saved.history, strict=True))

    todo = [k for k in range(len(chunks)) if not done(k)]
    print(
        f"{len(tasks)} histories in {len(chunks)} chunks of {chunk_size}; {len(chunks) - len(todo)} chunks "
        f"already saved in {run_dir}; {workers} worker processes",
        flush=True,
    )
    t0 = time.time()
    if todo:
        if workers == 1:  # in-process (tests, debugging)
            if not _STATE:
                _init_worker()
            pool, mapper = None, lambda f, xs: list(map(f, xs))
        else:
            pool = Pool(workers, initializer=_init_worker, maxtasksperchild=50)
            mapper = lambda f, xs: pool.map(f, xs, chunksize=1)  # noqa: E731
        try:
            for j, k in enumerate(todo):
                part = pd.concat(mapper(_task_ref, chunks[k]), ignore_index=True)
                tmp = run_dir / f"chunk_{k:04d}.csv.tmp"
                part.to_csv(tmp, index=False)
                tmp.replace(run_dir / f"chunk_{k:04d}.csv")  # atomic: a half-written chunk never counts as done
                el = time.time() - t0
                print(
                    f"  chunk {k + 1}/{len(chunks)} saved ({j + 1}/{len(todo)} this run, {el:.0f}s, "
                    f"~{el / (j + 1) * (len(todo) - j - 1) / 60:.0f} min left)",
                    flush=True,
                )
        finally:
            if pool is not None:
                pool.close()
                pool.join()
    per = pd.concat(
        [pd.read_csv(run_dir / f"chunk_{k:04d}.csv", dtype={"history": str}) for k in range(len(chunks))],
        ignore_index=True,
    )
    want = keys(tasks)
    return per[[k in want for k in zip(per.family, per.history, strict=True)]].reset_index(drop=True)


def main(argv: list[str] | None = None) -> pd.DataFrame:
    """Run all histories (chunked, resumable), write per-history and summary CSVs and the chart."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--histories", type=int, default=1000, help="per family (model and bootstrap)")
    ap.add_argument("--workers", type=int, default=min(4, os.cpu_count() or 1), help="worker processes (max 4)")
    ap.add_argument("--chunk-size", type=int, default=20, help="histories per saved chunk")
    args = ap.parse_args(argv)
    workers = max(1, min(args.workers, 4))
    hw.OUT.mkdir(exist_ok=True)
    hw.load_prices()  # registers provenance in this process
    per = run_chunked(args.histories, workers, args.chunk_size)
    summary = summarise(per)
    write_csv(per, hw.OUT / "robustness_histories.csv", index=False)
    write_csv(summary, hw.OUT / "robustness_table.csv", index=False)
    plot_robustness(summary, per, hw.OUT / "robustness.png")
    pd.set_option("display.width", 220)
    show = summary.copy()
    for c in (
        "median_total",
        "total_p05",
        "total_p95",
        "median_worst_year",
        "worst_year_p05",
        "p95_worst_year",
        "p95_max_jump",
    ):
        show[c] = (show[c] / 1e3).round(0).astype(int)
    show["pct_beats_variable"] = (show.pct_beats_variable * 100).round(0).astype(int).astype(str) + "%"
    print("\nEUR k; HedgeWise strategies use", ROBUSTNESS_PATHS, "scenarios per decision")
    print(show.to_string(index=False))
    print("Provenance: " + one_line())
    return summary
