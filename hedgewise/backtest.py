"""
HedgeWise strict backtest
=========================

For each delivery year Y, a company buys its energy for Y during the second half of Y-1.
Every decision uses ONLY data available on that date (the model is re-fitted on each decision date).

Decision dates: the last day of Jun, Jul, Aug, Sep, Oct, Nov of Y-1 (6 monthly tranches).
Fixed price on a decision date = 30-day mean spot (power: seasonally adjusted) x (1 + 5% premium),
the same stand-in for a forward curve as in hedgewise_model.py.

Strategies
  All variable           pay the monthly average day-ahead / TTF price
  Fix 100% in October    typical behaviour: renew a one-year fixed contract once a year (month set by --typical-month)
  Fix 60% at once        60% fixed on the first decision date, rest variable
  Tranches 6x            1/6 of the volume fixed on each of the 6 decision dates
  Tranches 6x (60%)      the same, but only 60% of the volume (1/10 per date); 40% stays variable
  Layered 2 years        40% fixed in 6 monthly tranches in H2 of Y-2, another 40% in H2 of Y-1, 20% variable
                         (standard utility practice; the fixed price two years ahead uses the same spot proxy)
  HedgeWise ladder       40% in 6 monthly tranches in H2 of Y-2; in H2 of Y-1 top up (equal tranches) to the model's
                         target, capped at the previous season's target + 10 points. Designed after seeing the
                         2021-2025 results, so its backtest is NOT independent evidence.
  HedgeWise (gamma)      on each decision date: re-fit, simulate, buy what the optimiser recommends now
  HedgeWise policy       on each decision date: re-fit, take the optimiser's share (gamma 0.5) as the target,
                         cap its rise at 20 points per month, buy the rest in equal monthly tranches (policy_plan)

Usage
  python backtest.py                                   # years 2021-2025, risk aversion 0.3 / 0.5 / 0.8
  python backtest.py --years 2022 --risk-aversion 0.8
  python backtest.py --company data/company_example.csv
"""

from __future__ import annotations

import argparse
import time

import numpy as np
import pandas as pd

import hedgewise as hw
from hedgewise import company_input
from hedgewise._types import DateLike, PathOrBuffer
from hedgewise.provenance import one_line, save_figure, write_csv, write_json
from hedgewise.settings import BACKTEST

_B = BACKTEST  # config.yaml: backtest
DECISION_MONTHS = _B["decision_months"]  # as-of = last day of these months in year Y-1
SOURCES = {"forward": 0, "proxy": 0}  # how many fixed prices came from real forwards vs the proxy
LADDER_NAME = "HedgeWise ladder (40% + capped top-up)"
LADDER_BASE = _B["ladder_base"]  # bought in 6 monthly tranches in H2 of Y-2
LADDER_MAX_STEP = _B["ladder_max_step"]  # target <= previous season's target + this
# NOTE: the ladder rule was designed AFTER seeing the 2021-2025 backtest results, so its backtest numbers are not
# independent evidence that it works (see RESULTS.md).
POLICY_GAMMA = _B["policy_gamma"]  # risk aversion used by the HedgeWise policy and ladder
# Benchmark strategies are defined by their names ("Tranches 6x (60%)" = 0.1 per date, ...), so their shares are
# part of the definition, not tunable assumptions; they stay here as named constants.
SHARE_PER_DATE_100 = 1 / 6  # Tranches 6x (100%)
SHARE_PER_DATE_60 = 0.1  # Tranches 6x (60%)
LAYER_PER_DATE = 0.4 / 6  # Layered 2 years (40+40%): 40% per season over 6 dates
FIX_ONCE_60 = 0.6  # Fix 60% at once (Jul)


def realised_monthly(df: pd.DataFrame, year: int) -> dict[str, np.ndarray]:
    """Realised monthly average gas and power prices of `year` (12 values each)."""
    out = {}
    for c in ("gas", "power"):
        s = df.loc[f"{year}-01-01" : f"{year}-12-31", c]
        m = s.groupby(s.index.month).mean().reindex(range(1, 13))
        if m.isna().any():
            raise ValueError(f"incomplete {c} data for {year}")
        out[c] = m.values
    return out


def fixed_price(
    df: pd.DataFrame, c: str, as_of: DateLike, delivery_start: DateLike, model: hw.FittedModel | None = None
) -> float:
    """Real forward (data/forwards.csv, latest quote on or before as_of) if available, else the proxy."""
    q = hw.real_forward(c, as_of, delivery_start)
    if q:
        SOURCES["forward"] += 1
        return float(q["price"])
    SOURCES["proxy"] += 1
    hist = df.loc[:as_of, c]
    p = hist.iloc[-hw.FORWARD_LOOKBACK_DAYS :].mean()
    if c == "power" and model is not None:
        p *= hw.season_adjustment(model.power, as_of, delivery_start)
    return float(p * (1 + hw.FORWARD_PREMIUM))


def _fp_safe(df: pd.DataFrame, c: str, as_of: DateLike, delivery_start: DateLike) -> float:
    """Fixed-price proxy when a model can't be fitted yet (less than ~1 year of data): no seasonal adjustment."""
    try:
        m = hw.fit(df, as_of)
    except ValueError:
        m = None
    return fixed_price(df, c, as_of, delivery_start, m)


def run_year(
    df: pd.DataFrame,
    company: hw.Company,
    year: int,
    lambdas: list[float],
    n_paths: int,
    seed: int,
    typical_month: int,
    verbose: bool = True,
    allow_timing: bool = False,
    prev_ladder_target: float | None = None,
) -> tuple[dict[str, float], dict]:
    """Backtest one delivery year: every strategy's realised bill, plus details (trades, forecasts, logs).

    Each decision date re-fits the model on data up to that date only. prev_ladder_target is the HedgeWise ladder's
    final target for delivery year `year`-1 (None if that year is not in this run; the 40% base layer is then used
    as the starting point of the cap)."""
    ds = pd.Timestamp(f"{year}-01-01")
    dates = [pd.Timestamp(year - 1, m, 1) + pd.offsets.MonthEnd(0) for m in DECISION_MONTHS]
    vol = {"gas": company.gas_mwh, "power": company.power_mwh}
    real = realised_monthly(df, year)
    var_cost = {c: float(vol[c] @ real[c]) for c in vol}

    fp, models, analyses = {}, {}, {}
    hedged = {lam: {"gas": [], "power": []} for lam in lambdas}  # list of (fraction, price)
    policy = {"gas": [], "power": []}  # HedgeWise policy (gamma POLICY_GAMMA)
    policy_log, prev_target = [], None
    # HedgeWise ladder: 40% base layer bought in H2 of Y-2 (priced after the loop), top-up to the capped target here
    ladder_top = {"gas": [], "power": []}
    ladder_log = []
    ladder_cap = (LADDER_BASE if prev_ladder_target is None else prev_ladder_target) + LADDER_MAX_STEP
    ladder_target = LADDER_BASE
    calib = None
    for k, d in enumerate(dates):
        t0 = time.time()
        m = hw.fit(df, d)
        models[d] = m
        fp[d] = {c: fixed_price(df, c, d, ds, m) for c in vol}
        sims = hw.simulate(m, ds + pd.DateOffset(years=1) - pd.Timedelta(days=1), n_paths, seed)
        n_left = len(dates) - k
        a_by_lam = {}
        for lam in lambdas:
            a = hw.analyse(
                m, company, ds, n_paths, seed, lam, max_tranches=n_left, sims=sims, allow_timing=allow_timing
            )
            a_by_lam[lam] = a
            if k == 0 and calib is None:
                v = a.costs["All variable"]
                calib = {
                    "p05": float(np.quantile(v, 0.05)),
                    "p50": float(np.median(v)),
                    "p95": float(np.quantile(v, 0.95)),
                    "es95": float(v[v >= np.quantile(v, 0.95)].mean()),
                    "expected": float(v.mean()),
                }
            r = a.recommendation
            for c in ("gas", "power"):
                done = sum(f for f, _ in hedged[lam][c])
                target = r[f"hedge_{c}"]
                n_exec = 1 if r["execution"] == "now" else n_left
                buy = max(0.0, min(1.0 - done, (target - done) / n_exec))
                if buy > 1e-9:
                    hedged[lam][c].append((buy, fp[d][c]))
            analyses[(d, lam)] = r
        a_pol = (
            hw.analyse(m, company, ds, n_paths, seed, POLICY_GAMMA, max_tranches=n_left, sims=sims)
            if POLICY_GAMMA not in lambdas
            else a_by_lam[POLICY_GAMMA]
        )
        done = sum(f for f, _ in policy["gas"])
        plan = hw.policy_plan(a_pol, n_left, prev_target, done)
        prev_target = plan["target"]
        if plan["buy_now"] > 1e-9:
            for c in ("gas", "power"):
                policy[c].append((plan["buy_now"], fp[d][c]))
        policy_log.append(
            {
                "date": str(d.date()),
                "optimiser": plan["raw_target"],
                "target": plan["target"],
                "capped": plan["capped"],
                "bought": round(plan["buy_now"], 4),
            }
        )
        raw = float(a_pol.recommendation["hedge_gas"])
        ladder_target = min(1.0, max(LADDER_BASE, min(raw, ladder_cap)))
        l_done = LADDER_BASE + sum(f for f, _ in ladder_top["gas"])
        l_buy = max(0.0, ladder_target - l_done) / n_left  # equal monthly tranches, never front-loaded
        if l_buy > 1e-9:
            for c in ("gas", "power"):
                ladder_top[c].append((l_buy, fp[d][c]))
        ladder_log.append(
            {
                "date": str(d.date()),
                "optimiser": raw,
                "cap": round(ladder_cap, 4),
                "target": ladder_target,
                "bought": round(l_buy, 4),
            }
        )
        if verbose:
            print(
                f"  {year}: decision {d.date()} fitted+simulated in {time.time() - t0:.0f}s | "
                f"fixed price gas {fp[d]['gas']:.1f}, power {fp[d]['power']:.1f}"
            )

    def cost_with(fixes: dict[str, list]) -> float:
        """Realised bill when `fixes` = {commodity: [(share, fixed price), ...]} and the rest is variable."""
        tot = 0.0
        for c in vol:
            f_tot = sum(f for f, _ in fixes[c])
            fixed_part = sum(f * p for f, p in fixes[c]) * vol[c].sum()
            tot += fixed_part + (1 - f_tot) * var_cost[c]
        return tot

    typ_date = pd.Timestamp(year - 1, typical_month - 1, 1) + pd.offsets.MonthEnd(0)
    res = {
        "All variable": cost_with({c: [] for c in vol}),
        f"Fix 100% in {hw.MONTHS[typical_month - 1]}": cost_with({c: [(1.0, fp[typ_date][c])] for c in vol}),
        "Fix 60% at once (Jul)": cost_with({c: [(FIX_ONCE_60, fp[dates[0]][c])] for c in vol}),
        "Tranches 6x (100%)": cost_with({c: [(SHARE_PER_DATE_100, fp[d][c]) for d in dates] for c in vol}),
        "Tranches 6x (60%)": cost_with({c: [(SHARE_PER_DATE_60, fp[d][c]) for d in dates] for c in vol}),
    }
    dates2 = [pd.Timestamp(year - 2, m_, 1) + pd.offsets.MonthEnd(0) for m_ in DECISION_MONTHS]
    fp2 = {d: {c: _fp_safe(df, c, d, ds) for c in vol} for d in dates2}
    res["Layered 2 years (40+40%)"] = cost_with(
        {c: [(LAYER_PER_DATE, fp2[d][c]) for d in dates2] + [(LAYER_PER_DATE, fp[d][c]) for d in dates] for c in vol}
    )
    res[f"HedgeWise policy (gamma {POLICY_GAMMA})"] = cost_with(policy)
    res[LADDER_NAME] = cost_with({c: [(LADDER_BASE / 6, fp2[d][c]) for d in dates2] + ladder_top[c] for c in vol})
    hw_detail = {}
    for lam in lambdas:
        res[f"HedgeWise (risk aversion {lam})"] = cost_with(hedged[lam])
        hw_detail[lam] = {c: [(round(f, 3), round(p, 1)) for f, p in hedged[lam][c]] for c in vol}
    # sensitivity: fix 100% once, in each possible month
    once = {hw.MONTHS[d.month]: cost_with({c: [(1.0, fp[d][c])] for c in vol}) for d in dates}
    info = {
        "realised_avg_price": {c: float(vol[c] @ real[c] / vol[c].sum()) for c in vol},
        "fixed_prices": {str(d.date()): fp[d] for d in dates},
        "hedgewise_trades": hw_detail,
        "fix_once_by_month": once,
        "model_forecast_all_variable": calib,
        "policy": policy_log,
        "ladder": ladder_log,
        "ladder_final_target": ladder_target,
        "ladder_fixed_share": LADDER_BASE + sum(f for f, _ in ladder_top["gas"]),
        "ladder_prev_target_used": prev_ladder_target,
    }
    return res, info


# --------------------------------------------------------------------------------------------
def plot_pitch(results: pd.DataFrame, info: dict, year: int, company: hw.Company, path: PathOrBuffer) -> None:
    """Step 13 pitch chart: all variable vs fix in tranches vs fix 100% (with the timing range)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    r = results.loc[year]
    fix_key = [k for k in r.index if k.startswith("Fix 100%")][0]
    once = info[year]["fix_once_by_month"]
    vals = [r["All variable"] / 1e6, r[fix_key] / 1e6, r["Tranches 6x (100%)"] / 1e6]
    labels = [
        "All variable\n(no hedge)",
        fix_key.replace("Fix 100% in", "Fix 100% at once\nin") + "\n(typical renewal)",
        "Fix in 6 monthly\ntranches\n(HedgeWise policy)",
    ]
    colors = ["#b8433a", "#8a8f98", "#1b9e5a"]
    fig, ax = plt.subplots(figsize=(10, 6.2))
    bars = ax.bar(labels, vals, color=colors, width=0.6)
    lo, hi = min(once.values()) / 1e6, max(once.values()) / 1e6
    ax.errorbar(1, vals[1], yerr=[[vals[1] - lo], [hi - vals[1]]], color="k", capsize=10, lw=1.5)
    ax.text(
        1.33,
        hi,
        f"fixing once in Jul...Dec:\nanywhere from EUR {lo:.2f}m\nto EUR {hi:.2f}m",
        fontsize=9,
        va="top",
        color="#333",
    )
    for b_, v in zip(bars, vals, strict=False):
        ax.text(b_.get_x() + 0.04, v * 1.01, f"EUR {v:.2f}m", ha="left", va="bottom", fontsize=12, weight="bold")
    save = vals[0] - vals[2]
    ax.annotate(
        f"EUR {save:.2f}m saved\n({save / vals[0]:.0%} of the unhedged bill)",
        xy=(2.2, vals[2] * 1.08),
        xytext=(1.55, vals[0] * 0.93),
        fontsize=12,
        color="#1b9e5a",
        weight="bold",
        arrowprops=dict(arrowstyle="->", color="#1b9e5a", lw=1.5),
    )
    ax.set_ylabel(f"Gas + power commodity cost in {year} (EUR m)")
    gas_m3 = sum(company.gas_m3) / 1e6
    pw = company.power_mwh.sum() / 1e3
    ax.set_title(
        f"{year} energy bill of a {company.name.replace(' (hypothetical)', '')}\n"
        f"({gas_m3:.1f}m m3 gas + {pw:.0f} GWh power; every decision uses only data available at the time)",
        fontsize=12,
    )
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_ylim(0, max(vals + [hi]) * 1.15)
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    save_figure(fig, path, dpi=150)
    plt.close(fig)


def plot_all_years(results: pd.DataFrame, path: PathOrBuffer) -> None:
    """All strategies per year, plus total vs worst year."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    cols = list(results.columns)
    fig, axes = plt.subplots(1, 2, figsize=(15, 6.6), gridspec_kw={"width_ratios": [3, 1.2]})
    ax = axes[0]
    x = np.arange(len(results))
    w = 0.8 / len(cols)
    pal = [
        "#b8433a",
        "#8a8f98",
        "#c9a227",
        "#2a6fdb",
        "#93c5fd",
        "#1b9e5a",
        "#7c3aed",
        "#0e7490",
        "#fbbf24",
        "#a7f3d0",
        "#0b5d33",
    ]
    for i, c in enumerate(cols):
        ax.bar(x + i * w - 0.4 + w / 2, results[c] / 1e6, w, label=c, color=pal[i % len(pal)])
    ax.set_xticks(x, results.index)
    ax.set_ylabel("EUR m")
    ax.set_title("Annual energy bill by strategy (realised)")
    ax.legend(fontsize=8, frameon=False, ncol=3, loc="upper center", bbox_to_anchor=(0.5, -0.06))
    ax.grid(axis="y", alpha=0.25)
    ax = axes[1]
    tot = results.sum() / 1e6
    worst = results.max() / 1e6
    y = np.arange(len(cols))
    ax.barh(y - 0.2, tot, 0.4, color="#94a3b8", label=f"total {results.index.min()}-{results.index.max()}")
    ax.barh(y + 0.2, worst, 0.4, color="#ef8a62", label="worst single year")
    ax.set_yticks(y, cols, fontsize=8)
    ax.invert_yaxis()
    ax.legend(fontsize=8, frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.07), ncol=2)
    ax.set_title("Total vs worst year (EUR m)")
    ax.grid(axis="x", alpha=0.25)
    fig.tight_layout()
    save_figure(fig, path, dpi=140)
    plt.close(fig)


def plot_cycle(results: pd.DataFrame, typical_key: str, path: PathOrBuffer) -> None:
    """Pitch chart 2: the whole cycle. Disciplined partial hedging vs the two habits SMEs actually have."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    series = [
        ("All variable", "All variable (no hedge)", "#b8433a", "o"),
        (typical_key, typical_key.replace("Fix 100% in", "Fix 100% once a year in") + " (typical)", "#6b7280", "s"),
        ("Tranches 6x (60%)", "Fix 60% in 6 monthly tranches", "#2a6fdb", "^"),
        ("Layered 2 years (40+40%)", "Layered over 2 years (40% + 40%)", "#1b9e5a", "D"),
    ]
    pol = [k for k in results.columns if k.startswith("HedgeWise policy")]
    if pol:
        series.append((pol[0], "HedgeWise policy (capped, monthly tranches)", "#7c3aed", "v"))
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.8), gridspec_kw={"width_ratios": [1.6, 1]})
    ax = axes[0]
    yrs = results.index.values
    for key, lbl, col, mk in series:
        v = results[key].values / 1e6
        ax.plot(yrs, v, "-", marker=mk, color=col, lw=2, ms=8, label=lbl)
    ax.set_xticks(yrs)
    ax.set_ylabel("Realised gas + power bill (EUR m)")
    ax.set_title("Annual bill, 2021-2025 (every decision uses only data available at the time)", fontsize=11)
    ax.legend(frameon=False, fontsize=9, loc="upper right")
    ax.grid(alpha=0.25)
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_ylim(0, results[[k for k, *_ in series]].values.max() / 1e6 * 1.1)
    ax = axes[1]
    metrics = [
        ("Worst year", results.max()),
        ("Biggest jump\nyear-on-year", results.diff().max()),
        ("Total 5 years\n/ 5 (avg year)", results.sum() / len(results)),
    ]
    w = 0.8 / len(series)
    x = np.arange(len(metrics))
    for i, (key, lbl, col, _) in enumerate(series):
        vals = [m[key] / 1e6 for _, m in metrics]
        off = (i - (len(series) - 1) / 2) * w
        ax.bar(x + off, vals, w * 0.92, color=col, label=lbl)
        for xi, v in zip(x + off, vals, strict=False):
            ax.text(xi, v + 0.04, f"{v:.2f}", ha="center", fontsize=6.2, color="#333")
    ax.set_xticks(x, [m for m, _ in metrics])
    ax.set_ylabel("EUR m")
    ax.set_title("Risk vs cost over the whole cycle", fontsize=11)
    ax.grid(alpha=0.25, axis="y")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    save_figure(fig, path, dpi=150)
    plt.close(fig)


def plot_calibration(info: dict, results: pd.DataFrame, path: PathOrBuffer) -> None:
    """Model forecast range (as of 30 Jun of the year before) vs the realised all-variable bill."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    years = sorted(info)
    fig, ax = plt.subplots(figsize=(9, 5))
    for i, y in enumerate(years):
        c = info[y]["model_forecast_all_variable"]
        ax.bar(
            i,
            (c["p95"] - c["p05"]) / 1e6,
            bottom=c["p05"] / 1e6,
            width=0.18,
            color="#2a6fdb",
            alpha=0.3,
            label="model 5-95% range (as of 30 Jun, year before)" if i == 0 else None,
        )
        ax.plot(i, c["p50"] / 1e6, "o", color="#2a6fdb", label="model median" if i == 0 else None)
        ax.plot(
            i, c["es95"] / 1e6, "v", color="#2a6fdb", ms=7, label="model ES95 (avg of worst 5%)" if i == 0 else None
        )
        ax.plot(
            i,
            results.loc[y, "All variable"] / 1e6,
            "D",
            color="#b8433a",
            ms=9,
            label="realised (all variable)" if i == 0 else None,
        )
    ax.set_xticks(range(len(years)), years)
    ax.set_ylabel("All-variable bill, EUR m")
    ax.set_title("Calibration: did the realised bill fall inside the model's forecast range?")
    ax.legend(fontsize=8, frameon=False)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    save_figure(fig, path, dpi=140)
    plt.close(fig)


def main(argv: list[str] | None = None) -> pd.DataFrame:
    """Run the backtest, print the tables and write the CSV/JSON/PNG outputs; returns the results table."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--years", type=int, nargs="+", default=_B["years"])
    ap.add_argument("--risk-aversion", type=float, nargs="+", default=_B["risk_aversions"])
    ap.add_argument("--paths", type=int, default=_B["n_paths"])
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument(
        "--typical-month", type=int, default=_B["typical_month"], help="month of the once-a-year renewal (1-12)"
    )
    company_input.add_cli_args(ap)
    ap.add_argument("--pitch-year", type=int, default=_B["pitch_year"])
    ap.add_argument("--allow-timing", action="store_true", help="HedgeWise may fix everything at once")
    args = ap.parse_args(argv)
    if not 7 <= args.typical_month <= 12:
        ap.error("--typical-month must be 7-12 (decisions are made in Jul-Dec of the year before)")

    hw.OUT.mkdir(exist_ok=True)
    df = hw.load_prices()
    company = company_input.company_from_args(args)
    print(
        f"Company: {company.name} | gas {sum(company.gas_m3):,.0f} m3/yr "
        f"({company.gas_mwh.sum():,.0f} MWh), power {company.power_mwh.sum():,.0f} MWh/yr\n"
    )
    rows, infos = {}, {}
    for y in args.years:
        prev = infos[y - 1]["ladder_final_target"] if (y - 1) in infos else None
        rows[y], infos[y] = run_year(
            df,
            company,
            y,
            args.risk_aversion,
            args.paths,
            args.seed,
            args.typical_month,
            allow_timing=args.allow_timing,
            prev_ladder_target=prev,
        )
    res = pd.DataFrame(rows).T
    res.index.name = "delivery_year"
    write_csv(res, hw.OUT / "backtest_results.csv")
    write_json({str(k): v for k, v in infos.items()}, hw.OUT / "backtest_details.json", indent=2, default=float)
    print("Provenance: " + one_line())

    from . import forwards

    print(
        f"\nFixed prices: {SOURCES['forward']} from real forwards "
        f"({forwards.FORWARDS_PATH.name if forwards.available() else 'no data/forwards.csv'}), "
        f"{SOURCES['proxy']} from the proxy (30-day spot + 5%)."
    )
    pd.set_option("display.width", 200)
    print("\nRealised annual bill, EUR k")
    show = (res / 1e3).round(0).astype(int)
    show.loc["TOTAL"] = (res.sum() / 1e3).round(0).astype(int)  # exact total (not a sum of rounded years)
    show.loc["WORST YEAR"] = (res.max() / 1e3).round(0).astype(int)
    show.loc["STDEV"] = (res.std() / 1e3).round(0).astype(int)
    show.loc["MAX JUMP"] = (res.diff().max() / 1e3).round(0).astype(int)  # biggest year-on-year increase
    stab = pd.DataFrame({"total": res.sum(), "worst_year": res.max(), "stdev": res.std(), "max_jump": res.diff().max()})
    write_csv(stab, hw.OUT / "backtest_stability.csv")
    print(show.to_string())
    print("\nRealised average prices (EUR/MWh):")
    for y in args.years:
        rp = infos[y]["realised_avg_price"]
        print(f"  {y}: gas {rp['gas']:.1f}, power {rp['power']:.1f}")
    print(
        f"\nHedgeWise policy (gamma {POLICY_GAMMA}, cap +{hw.POLICY_MAX_STEP:.0%}/month): optimiser -> capped target "
        "-> bought, per decision date"
    )
    for y in args.years:
        print(
            f"  {y}: "
            + " | ".join(
                f"{p['optimiser']:.0%}->{p['target']:.0%}{'*' if p['capped'] else ''} buy {p['bought']:.1%}"
                for p in infos[y]["policy"]
            )
        )
    print("  (* = capped)")
    print(
        f"\nHedgeWise ladder: {LADDER_BASE:.0%} in H2 of Y-2, then top-up to the model's target "
        f"(capped at previous season's target + {LADDER_MAX_STEP:.0%}) in H2 of Y-1. "
        "DESIGNED AFTER SEEING THESE RESULTS."
    )
    for y in args.years:
        lg = infos[y]["ladder"]
        prev = infos[y]["ladder_prev_target_used"]
        print(
            f"  {y}: prev season target {'none -> 40% base' if prev is None else f'{prev:.0%}'}, "
            f"cap {lg[0]['cap']:.0%} | "
            + " | ".join(f"{p['optimiser']:.0%}->{p['target']:.0%} buy {p['bought']:.1%}" for p in lg)
            + f" | fixed {infos[y]['ladder_fixed_share']:.0%}"
        )
    print("\nFix 100% once - result depends on the month you pick (EUR k):")
    print((pd.DataFrame({y: infos[y]["fix_once_by_month"] for y in args.years}).T / 1e3).round(0).to_string())
    print("\nModel forecast of the all-variable bill (as of 30 Jun of the year before) vs realised (EUR k):")
    outside = 0
    for y in args.years:
        c = infos[y]["model_forecast_all_variable"]
        r = res.loc[y, "All variable"]
        print(
            f"  {y}: median {c['p50'] / 1e3:,.0f}, 5-95% [{c['p05'] / 1e3:,.0f} - {c['p95'] / 1e3:,.0f}], "
            f"ES95 {c['es95'] / 1e3:,.0f} | realised {r / 1e3:,.0f}"
            f"{'  <-- outside 5-95%' if r > c['p95'] or r < c['p05'] else ''}"
        )
        outside += r > c["p95"] or r < c["p05"]
    print(f"  Coverage: {outside} of {len(args.years)} years outside the 5-95% range (a calibrated model: ~10%).")

    py = args.pitch_year if args.pitch_year in res.index else res.index[0]
    lam_main = 0.5 if 0.5 in args.risk_aversion else args.risk_aversion[0]
    plot_pitch(res, infos, py, company, hw.OUT / f"pitch_{py}.png")
    plot_all_years(res, hw.OUT / "backtest_all_years.png")
    typ_key = [k for k in res.columns if k.startswith("Fix 100%")][0]
    plot_cycle(res, typ_key, hw.OUT / "pitch_cycle.png")
    plot_calibration(infos, res, hw.OUT / "backtest_calibration.png")
    r = res.loc[py]
    hwk = f"HedgeWise (risk aversion {lam_main})"
    tr = r["Tranches 6x (100%)"]
    print(
        f"\nHEADLINE: in {py} an unhedged {company.name} paid EUR {r['All variable'] / 1e6:.2f}m; "
        f"fixing in 6 monthly tranches it would have paid EUR {tr / 1e6:.2f}m "
        f"(saving EUR {(r['All variable'] - tr) / 1e6:.2f}m, {(r['All variable'] - tr) / r['All variable']:.0%}). "
        f"HedgeWise optimiser (risk aversion {lam_main}): EUR {r[hwk] / 1e6:.2f}m."
    )
    print(f"Charts: {hw.OUT}/pitch_{py}.png, pitch_cycle.png, backtest_all_years.png, backtest_calibration.png")
