"""Procurement strategies, risk metrics, the HedgeWise optimiser and policy, stress tests and advice text."""

from __future__ import annotations

import dataclasses
import re

import numpy as np
import pandas as pd

from ._types import DateLike
from .company import Company
from .models import DAYS_PER_YEAR, FittedModel, PowerModel
from .settings import (
    ANCHOR_TO_FORWARD,
    BACKTEST,
    BUDGET_LOOKBACK_DAYS,
    BUDGET_MARGIN,
    DEFAULT_PATHS,
    DEFAULT_RISK_AVERSION,
    DEFAULT_SEED,
    FORWARD_LOOKBACK_DAYS,
    FORWARD_PREMIUM,
    HEDGE_GRID_STEP,
    MAX_TRANCHES,
    OBJECTIVE,
    PARTIAL_FIXED_SHARE,
    POLICY_MAX_STEP,
    RISK_QUANTILE,
    SAME_MONTH_DAYS,
    SAME_RATIO,
    STRESS_LABELS,
    STRESS_PREV_YEAR,
    STRESS_YEAR,
)
from .simulate import simulate

_GRID_END = 1.0001  # np.arange end point just above 100% so that 100% is included


# --------------------------------------------------------------------------------------------
# Strategies and risk
# --------------------------------------------------------------------------------------------
def tranche_dates(as_of: DateLike, delivery_start: DateLike, max_tranches: int = MAX_TRANCHES) -> list[pd.Timestamp]:
    """Tranche dates: the as-of date itself ("now"), then the first day of each following month before
    delivery starts (months starting within a week of as-of count as "now"), at most max_tranches."""
    as_of, ds = pd.Timestamp(as_of), pd.Timestamp(delivery_start)
    months = pd.date_range(as_of + pd.Timedelta(days=SAME_MONTH_DAYS), ds - pd.Timedelta(days=1), freq="MS")
    return ([as_of] + list(months))[:max_tranches]


def _monthly_mean(paths: np.ndarray, dates: pd.DatetimeIndex, start: DateLike, months: int = 12) -> np.ndarray:
    """(12, n_paths) average daily price per delivery month."""
    res = []
    for i in range(months):
        ms = pd.Timestamp(start) + pd.DateOffset(months=i)
        mask = (dates >= ms) & (dates < ms + pd.DateOffset(months=1))
        res.append(paths[mask].mean(axis=0))
    return np.vstack(res)


def _trailing_mean(
    paths: np.ndarray, dates: pd.DatetimeIndex, hist: pd.Series, when: DateLike, days: int = FORWARD_LOOKBACK_DAYS
) -> np.ndarray:
    """Forward proxy at date `when`: mean of the last `days` days (history + simulated)."""
    when = pd.Timestamp(when)
    lo = when - pd.Timedelta(days=days)
    h = hist.loc[(hist.index > lo) & (hist.index <= when)]
    mask = (dates > lo) & (dates <= when)
    tot = h.sum() + paths[mask].sum(axis=0)
    cnt = len(h) + mask.sum()
    return tot / cnt


def season_adjustment(pm: PowerModel, when: DateLike, delivery_start: DateLike) -> float:
    """Factor that turns a trailing-30-day power price into a calendar-delivery price, using the
    fitted annual seasonality (a November spot price overstates a full-year baseload price)."""
    n = len(pm.drivers)
    cs, cc = pm.coef[1 + n], pm.coef[2 + n]

    def level(idx: pd.DatetimeIndex) -> float:
        doy = np.asarray(idx.day_of_year)
        return np.exp(
            cs * np.sin(2 * np.pi * doy / DAYS_PER_YEAR) + cc * np.cos(2 * np.pi * doy / DAYS_PER_YEAR)
        ).mean()

    when, ds = pd.Timestamp(when), pd.Timestamp(delivery_start)
    win = pd.date_range(when - pd.Timedelta(days=FORWARD_LOOKBACK_DAYS - 1), when, freq="D")
    dlv = pd.date_range(ds, ds + pd.DateOffset(years=1) - pd.Timedelta(days=1), freq="D")
    return float(level(dlv) / level(win))


def real_forward(commodity: str, as_of: DateLike, delivery_start: DateLike) -> dict | None:
    """Real calendar-year forward quote (forwards.py, data/forwards.csv) on or before as_of, if the file exists
    and delivery starts on 1 January; otherwise None and the proxy is used."""
    ds = pd.Timestamp(delivery_start)
    if (ds.month, ds.day) != (1, 1):
        return None
    from . import forwards

    if not forwards.available():
        return None
    return forwards.forward_quote(commodity, as_of, ds.year)


def risk_stats(cost: np.ndarray, q: float = RISK_QUANTILE) -> dict:
    """Expected cost, median, VaR, Expected Shortfall (mean of the worst 1-q), worst and best of a cost sample."""
    var = float(np.quantile(cost, q))
    return {
        "expected": float(cost.mean()),
        "p50": float(np.median(cost)),
        f"var{int(q * 100)}": var,
        f"es{int(q * 100)}": float(cost[cost >= var].mean()),
        "worst": float(cost.max()),
        "best": float(cost.min()),
    }


@dataclasses.dataclass
class Analysis:
    """Result of analyse(): simulated bills per strategy, risk table, frontier and recommendation."""

    model: FittedModel
    company: Company
    delivery_start: pd.Timestamp
    tranches: list
    spot_month: dict  # commodity -> (12, n) simulated monthly average spot
    fwd_now: dict  # commodity -> fixed price available today
    fwd_tranche_avg: dict  # commodity -> (n,) average price over all tranches
    costs: dict  # strategy -> (n,) total cost in EUR
    table: pd.DataFrame
    frontier: pd.DataFrame
    recommendation: dict
    sims: dict
    bad_case_source: dict = dataclasses.field(default_factory=dict)  # strategy -> "model" / "stress" (see below)


def analyse(
    m: FittedModel,
    company: Company,
    delivery_start: DateLike | None = None,
    n_paths: int = DEFAULT_PATHS,
    seed: int = DEFAULT_SEED,
    risk_aversion: float = DEFAULT_RISK_AVERSION,
    max_tranches: int = MAX_TRANCHES,
    sims: dict | None = None,
    allow_timing: bool = False,
    budget: float | str | None = None,
) -> Analysis:
    """allow_timing=False (default): HedgeWise always executes in monthly tranches (a policy, no market timing).
    allow_timing=True: the optimiser may also choose to fix everything immediately.
    budget=None: minimise (1-lambda)*E + lambda*ES95.
    budget=<EUR> or "auto": hedge as LITTLE as possible such that the 95% VaR stays within budget
    ("auto" = last 12 months' realised bill on this consumption profile + BUDGET_MARGIN). If even a
    full hedge cannot meet the budget, take the hedge with the lowest ES95."""
    ds = (
        pd.Timestamp(delivery_start)
        if delivery_start is not None
        else pd.Timestamp(year=m.as_of.year + 1, month=1, day=1)
    )
    de = ds + pd.DateOffset(years=1) - pd.Timedelta(days=1)
    sims = sims or simulate(m, de, n_paths, seed)
    dates = sims["dates"]
    tr = tranche_dates(m.as_of, ds, max_tranches)
    vol = {"gas": company.gas_mwh, "power": company.power_mwh}
    # align volumes with delivery months (delivery may start mid-year)
    order = [(ds + pd.DateOffset(months=i)).month - 1 for i in range(12)]
    vol = {k: v[order] for k, v in vol.items()}

    spot_m, fwd_now, fwd_tr, c_var, c_fix, c_tr, anchor_k = {}, {}, {}, {}, {}, {}, {}
    price_src = {}
    sims = dict(sims)  # never modify the (cached) input paths in place
    for c in ("gas", "power"):
        hist = m.history[c]

        def adj(t: DateLike, _power: bool = c == "power") -> float:
            """Seasonal factor for power (spot window -> calendar delivery); 1 for gas."""
            return season_adjustment(m.power, t, ds) if _power else 1.0

        q = real_forward(c, m.as_of, ds)
        if q:  # real calendar forward: it IS the fixed price (and, below, the anchor)
            fwd_now[c] = q["price"]
            price_src[c] = f"forward {q['product']} quoted {q['date']:%Y-%m-%d}"
        else:
            fwd_now[c] = float(hist.iloc[-FORWARD_LOOKBACK_DAYS:].mean() * adj(m.as_of) * (1 + FORWARD_PREMIUM))
            price_src[c] = "proxy (30-day spot" + (", seasonally adjusted" if c == "power" else "") + " + 5%)"
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

    def total(hg: float, hp: float, fixed: dict[str, np.ndarray]) -> np.ndarray:
        return ((1 - hg) * c_var["gas"] + hg * fixed["gas"]) + ((1 - hp) * c_var["power"] + hp * fixed["power"])

    bill_scale = float(c_var["gas"].mean() + c_var["power"].mean())  # expected all-variable bill

    costs = {
        "All variable": total(0, 0, c_fix),
        "Fix 60% now": total(PARTIAL_FIXED_SHARE, PARTIAL_FIXED_SHARE, c_fix),
        "Fix 100% now": total(1, 1, c_fix) * np.ones_like(c_var["gas"]),
        f"Tranches ({len(tr)}x, 100%)": total(1, 1, c_tr),
    }
    # HedgeWise: optimise hedge ratio per commodity AND execution (all now vs monthly tranches)
    grid = np.round(np.arange(0, _GRID_END, HEDGE_GRID_STEP), 2)
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
                rows.append(
                    {
                        "execution": ex,
                        "hedge_gas": hg,
                        "hedge_power": hp,
                        "expected": st["expected"],
                        "es95": st["es95"],
                        "var95": st["var95"],
                        "objective": obj,
                    }
                )
    frontier = pd.DataFrame(rows)
    budget_eur = None
    if budget is not None:
        budget_eur = trailing_bill(m.history, company) * (1 + BUDGET_MARGIN) if budget == "auto" else float(budget)
        frontier["hedge_avg"] = (frontier.hedge_gas * vol["gas"].sum() + frontier.hedge_power * vol["power"].sum()) / (
            vol["gas"].sum() + vol["power"].sum()
        )
        ok = frontier[frontier.var95 <= budget_eur]
        best = ok.sort_values(["hedge_avg", "expected"]).iloc[0] if len(ok) else frontier.loc[frontier.es95.idxmin()]
    else:
        best = frontier.loc[frontier.objective.idxmin()]
    ex = best.execution
    label = "now" if ex == "now" else f"{len(tr)} tranches"
    costs[f"HedgeWise (gas {best.hedge_gas:.0%}, power {best.hedge_power:.0%}, {label})"] = total(
        best.hedge_gas, best.hedge_power, execs[ex]
    )
    table = pd.DataFrame({k: risk_stats(v) for k, v in costs.items()}).T
    table["cost_at_risk"] = table["es95"] - table["expected"]
    n_exec = 1 if ex == "now" else len(tr)
    rec = {
        "hedge_gas": float(best.hedge_gas),
        "hedge_power": float(best.hedge_power),
        "execution": ex,
        "risk_aversion": risk_aversion,
        "tranches": [str(t.date()) for t in (tr[:1] if ex == "now" else tr)],
        "buy_now_gas": float(best.hedge_gas / n_exec),
        "buy_now_power": float(best.hedge_power / n_exec),
        "fixed_price_now": fwd_now,
        "budget": budget_eur,
        "mode": "budget" if budget_eur is not None else "risk_aversion",
        "objective": OBJECTIVE,
        "anchor_scale": anchor_k,
        "price_source": price_src,
    }
    return Analysis(m, company, ds, tr, spot_m, fwd_now, fwd_tr, costs, table, frontier, rec, sims)


# Why 20 percentage points: the buying season has 6 monthly decision dates, i.e. 5 steps between them, and
# 5 x 20pp = 100%. So the cap never stops the plan from reaching a full hedge within one season; it only stops
# the target from jumping (e.g. 30% -> 90%) in one month right after a volatility spike, when GARCH volatility -
# and therefore the optimiser's target - is highest. The number follows from the calendar, not from 2021-2025
# backtest outcomes, and it is NOT to be tuned on the backtest.


def policy_plan(
    a: Analysis,
    months_ahead: int,
    prev_target: float | None = None,
    already: float = 0.0,
    max_step: float = POLICY_MAX_STEP,
) -> dict:
    """Turn the optimiser's (or budget mode's) target share into this month's purchase.

    months_ahead : number of monthly decision dates left INCLUDING this one (6 at the start of a season)
    prev_target  : the (capped) target set on the previous decision date; None on the first date (no cap then)
    already      : share already fixed on earlier dates

    Rules: (1) target = optimiser's share, but at most prev_target + max_step;
           (2) the part still to buy is spread in equal monthly tranches over the remaining dates - never
               front-loaded; (3) nothing is ever sold back (if the target fell below what is fixed, buy 0)."""
    raw = float(a.recommendation["hedge_gas"])  # one share for gas and power (SAME_RATIO)
    target = raw if prev_target is None else min(raw, prev_target + max_step)
    n = max(int(months_ahead), 1)
    buy = max(0.0, target - already) / n
    buy = min(buy, 1.0 - already)
    return {
        "raw_target": raw,
        "target": target,
        "capped": target < raw - 1e-12,
        "buy_now": buy,
        "already": already,
        "months_ahead": n,
        "schedule": [buy] + [max(0.0, target - already - buy) / max(n - 1, 1)] * (n - 1),
    }


def _stress_prices(df: pd.DataFrame) -> dict:
    """Per commodity: realised 2022 monthly averages (calendar months 1-12) and the 2021 / 2022 annual averages."""
    out = {}
    for c in ("gas", "power"):
        y22 = df.loc[f"{STRESS_YEAR}-01-01" : f"{STRESS_YEAR}-12-31", c]
        y21 = df.loc[f"{STRESS_PREV_YEAR}-01-01" : f"{STRESS_PREV_YEAR}-12-31", c]
        monthly = y22.groupby(y22.index.month).mean().reindex(range(1, 13))
        if monthly.isna().any() or y21.empty:
            raise ValueError(
                f"price data for all of {STRESS_PREV_YEAR} and {STRESS_YEAR} is needed for the stress test"
            )
        out[c] = {"monthly_2022": monthly.values, "avg_2021": float(y21.mean()), "avg_2022": float(y22.mean())}
    return out


def stress_test(a: Analysis, df: pd.DataFrame) -> dict:
    """Two historical stress scenarios for next year's bill (NOT forecasts):

    (a) "repeat": the realised 2022 monthly gas and power prices exactly as they were.
    (b) "shock" : a shock as big as 2021->2022, starting from today's level:
                  price[m] = today's fixed price x (2022 annual avg / 2021 annual avg)
                             x (2022 month m / 2022 annual avg)
                  i.e. the 2022 monthly shape, scaled so the year averages fixed price x multiplier.
    For each: bills for all variable, the HedgeWise recommendation, fix 60% now and fix 100% now; the hedged part
    is priced at today's fixed price. Also the implied (volume-weighted) average gas and power price, so a reader
    can sanity-check the scenario."""
    ds = a.delivery_start
    cal = [(ds + pd.DateOffset(months=i)).month - 1 for i in range(12)]  # calendar-month index per delivery month
    vol = {"gas": a.company.gas_mwh[cal], "power": a.company.power_mwh[cal]}
    hp = _stress_prices(df)
    rec = a.recommendation
    scenarios = []
    for key in ("repeat", "shock"):
        var_cost, fix_cost, avg, mult = {}, {}, {}, {}
        for c in ("gas", "power"):
            p22 = hp[c]["monthly_2022"][cal]
            if key == "repeat":
                prices = p22
                mult[c] = None
            else:
                mult[c] = hp[c]["avg_2022"] / hp[c]["avg_2021"]
                prices = a.fwd_now[c] * mult[c] * p22 / hp[c]["avg_2022"]
            var_cost[c] = float(vol[c] @ prices)
            fix_cost[c] = float(vol[c].sum() * a.fwd_now[c])
            avg[c] = var_cost[c] / vol[c].sum() if vol[c].sum() else float(np.mean(prices))

        def bill(h: float, var_cost: dict = var_cost, fix_cost: dict = fix_cost) -> float:
            return sum((1 - h) * var_cost[c] + h * fix_cost[c] for c in ("gas", "power"))

        bills = {
            "All variable": bill(0.0),
            "HedgeWise": bill(rec["hedge_gas"]),
            "Fix 60% now": bill(PARTIAL_FIXED_SHARE),
            "Fix 100% now": bill(1.0),
        }
        scenarios.append(
            {"key": key, "label": STRESS_LABELS[key], "bills": bills, "avg_price": avg, "multiplier": mult}
        )
    return {
        "scenarios": scenarios,
        "hedge": rec["hedge_gas"],
        "fixed_price": dict(a.fwd_now),
        "note": "historical stress scenarios, not forecasts",
    }


def _eur_m(x: float, lang: str) -> str:
    """EUR millions, English or Dutch formatting."""
    return f"€{x / 1e6:.2f}m" if lang == "en" else f"€ {x / 1e6:.2f} mln".replace(".", ",")


def stress_sentence(s: dict, lang: str = "en") -> str:
    """One sentence per scenario, with the implied average prices."""
    out = []
    for sc in s["scenarios"]:
        b, p = sc["bills"], sc["avg_price"]
        if lang == "nl":
            pr = f"gas gem. € {p['gas']:.0f}, stroom € {p['power']:.0f} per MWh"
            out.append(
                f"{sc['label']['nl']} ({pr}): variabel {_eur_m(b['All variable'], lang)}, "
                f"HedgeWise-advies {_eur_m(b['HedgeWise'], lang)}, 100% vast {_eur_m(b['Fix 100% now'], lang)}."
            )
        else:
            pr = f"gas avg €{p['gas']:.0f}, power €{p['power']:.0f} per MWh"
            out.append(
                f"{sc['label']['en']} ({pr}): all variable {_eur_m(b['All variable'], lang)}, "
                f"HedgeWise plan {_eur_m(b['HedgeWise'], lang)}, fix 100% {_eur_m(b['Fix 100% now'], lang)}."
            )
    tail = (
        "Historical stress scenarios, not forecasts."
        if lang == "en"
        else "Historische stressscenario's, geen voorspellingen."
    )
    return " ".join(out) + " " + tail


def trailing_bill(history: pd.DataFrame, company: Company) -> float:
    """What the company's consumption profile would have cost over the last 12 months, all variable."""
    h = history.iloc[-BUDGET_LOOKBACK_DAYS:]
    total = 0.0
    for c, v in (("gas", company.gas_mwh), ("power", company.power_mwh)):
        mm = h[c].groupby(h.index.month).mean().reindex(range(1, 13)).ffill().bfill().values
        total += float(v @ mm)
    return total


def recommendation_text(a: Analysis) -> str:
    """The recommendation as plain-English lines (CLI)."""
    r, t = a.recommendation, a.table
    var_row, hw_row = t.loc["All variable"], t.iloc[-1]
    bad_var, src_var = bad_case(a, "All variable")
    bad_plan, src_plan = bad_case(a)
    n = len(a.tranches)
    if r["mode"] == "budget":
        head = (
            f"Budget EUR {r['budget'] / 1e6:.2f}m: the smallest hedge that keeps the bill within budget in 95% "
            "of scenarios is to "
        )
    elif r.get("rule") == "ladder":
        head = (
            f"Recommendation (HedgeWise ladder, pre-registered {BACKTEST['ladder_preregistered']}; model target "
            f"{r['model_target']:.0%}, kept between {LADDER_BASE:.0%} and {r['ladder_cap']:.0%}): "
        )
    else:
        head = f"Model-only recommendation (risk aversion {r['risk_aversion']}; not recommended, see RESULTS.md): "
    lines = [
        f"Delivery {a.delivery_start:%b %Y} - {(a.delivery_start + pd.DateOffset(months=11)):%b %Y}, {a.company.name}",
        f"If you stay fully variable: expected bill EUR {var_row.expected / 1e6:.2f}m; bad case EUR "
        f"{bad_var / 1e6:.2f}m ({'2021->22-size shock' if src_var == 'stress' else 'model, worst 5% average'}).",
        head
        + (
            f"fix {r['hedge_gas']:.0%} of next year's gas and power, "
            if np.isclose(r["hedge_gas"], r["hedge_power"])
            else f"fix {r['hedge_gas']:.0%} of gas and {r['hedge_power']:.0%} of power, "
        )
        + (
            "all of it now."
            if r["execution"] == "now"
            else f"spread over {n} monthly tranches ({', '.join(d[:7] for d in r['tranches'])}); "
            f"buy about {r['buy_now_gas']:.0%} gas / {r['buy_now_power']:.0%} power this month."
        ),
        f"That cuts the bad case by EUR {(bad_var - bad_plan) / 1e3:,.0f}k "
        + (
            f"for an expected extra cost of EUR {(hw_row.expected - var_row.expected) / 1e3:,.0f}k "
            "(the hedging premium)."
            if hw_row.expected >= var_row.expected
            else f"and is also EUR {(var_row.expected - hw_row.expected) / 1e3:,.0f}k cheaper in expectation."
        ),
    ]
    nx = r.get("next_year")
    if nx:
        lines.append(
            f"Also start {nx['year']}: buy {nx['buy_now']:.0%} of its volume this month, {nx['base']:.0%} by "
            "December (the ladder's base layer)."
        )
    if "bad_case" in t:
        lines.append(
            "The bad case is never lower than a repeat of the 2021->2022 price shock: the model's 12-month "
            "ranges were too narrow before that crisis (72% instead of 90% of outcomes)."
        )
    return "\n".join(lines)


def frontier_scale(a: Analysis) -> tuple[float, float]:
    """(extra expected cost, bad-case reduction) of fixing 100% instead of 0%, along the frontier that is drawn."""
    r = a.recommendation
    f = a.frontier
    f = f[(f.execution == r["execution"]) & np.isclose(f.hedge_gas, f.hedge_power)].sort_values("hedge_gas")
    lo, hi = f.iloc[0], f.iloc[-1]
    bc = "bad_case" if "bad_case" in f else "es95"
    return float(hi.expected - lo.expected), float(lo[bc] - hi[bc])


def frontier_scale_sentence(a: Analysis, lang: str = "en") -> str:
    """The sentence under the trade-off chart that makes its scale explicit."""
    x, y = frontier_scale(a)
    if lang == "nl":

        def k(v: float) -> str:
            if abs(v) < 1e6:
                return f"€\u00a0{round(v, -3):,.0f}".replace(",", ".")
            return f"€\u00a0{v / 1e6:.2f}\u00a0mln".replace(".", ",")

        return f"Alles vastzetten kost gemiddeld {k(x)} meer en verlaagt het slechte scenario met {k(y)}."

    def k(v: float) -> str:
        return f"€{v / 1e3:,.0f}k" if abs(v) < 1e6 else f"€{v / 1e6:.2f}m"

    return f"Fixing everything costs {k(x)} more on average and cuts the bad case by {k(y)}."


# --------------------------------------------------------------------------------------------
# The HedgeWise ladder: the pre-registered rule (PREREGISTRATION.md)
# --------------------------------------------------------------------------------------------
LADDER_BASE = BACKTEST["ladder_base"]  # 40% bought in monthly tranches in H2 of Y-2
LADDER_STEP = BACKTEST["ladder_max_step"]  # target <= previous season's target + 10 points
LADDER_GAMMA = BACKTEST["policy_gamma"]  # the model target comes from the optimiser at risk aversion 0.5
MODEL_ONLY = "Model-only"  # label of the plain optimiser column once the ladder is the plan


def _delivery_vol(a: Analysis) -> dict[str, np.ndarray]:
    order = [(a.delivery_start + pd.DateOffset(months=i)).month - 1 for i in range(12)]
    return {"gas": a.company.gas_mwh[order], "power": a.company.power_mwh[order]}


def hedge_costs(a: Analysis, hg: float, hp: float, execution: str = "tranches") -> np.ndarray:
    """Bill per scenario when hg of gas and hp of power are fixed ('now' at today's fixed price, 'tranches' at the
    average of the monthly tranche prices) and the rest floats: the same formula as analyse()."""
    vol = _delivery_vol(a)
    fixed = a.fwd_now if execution == "now" else a.fwd_tranche_avg
    total = 0.0
    for c, h in (("gas", hg), ("power", hp)):
        total = total + (1 - h) * (vol[c] @ a.spot_month[c]) + h * vol[c].sum() * np.asarray(fixed[c])
    return np.asarray(total * np.ones(a.spot_month["gas"].shape[1]))


def _table(costs: dict) -> pd.DataFrame:
    t = pd.DataFrame({k: risk_stats(v) for k, v in costs.items()}).T
    t["cost_at_risk"] = t["es95"] - t["expected"]
    return t


def apply_ladder(a: Analysis, prev_target: float | None = None) -> Analysis:
    """Make the HedgeWise ladder the recommendation (pre-registered rule, PREREGISTRATION.md):
    target = the optimiser's share at risk aversion 0.5, but at least the 40% base layer and at most the previous
    season's target + 10 points (no previous season: 40% + 10 = 50%), bought in equal monthly tranches. If today is
    in the second half of the year before delivery, also start next year's 40% base layer.
    The optimiser's own answer stays in the table as 'Model-only' (not recommended: procyclical, RESULTS.md).
    Budget mode is a different goal and is returned unchanged."""
    r = a.recommendation
    if r["mode"] == "budget":
        return a
    if not np.isclose(r["risk_aversion"], LADDER_GAMMA):
        raise ValueError(f"the ladder uses the model target at risk aversion {LADDER_GAMMA}; analyse with that")
    raw = float(r["hedge_gas"])
    cap = (LADDER_BASE if prev_target is None else float(prev_target)) + LADDER_STEP
    target = float(np.round(min(1.0, max(LADDER_BASE, min(raw, cap))) / HEDGE_GRID_STEP) * HEDGE_GRID_STEP)
    n = len(a.tranches)
    ex = "now" if n == 1 else "tranches"
    costs = {}
    for k, v in a.costs.items():  # rename the optimiser's column
        costs[k.replace("HedgeWise (", f"{MODEL_ONLY} (", 1) if k.startswith("HedgeWise (") else k] = v
    label = "now" if ex == "now" else f"{n} tranches"
    costs[f"HedgeWise ladder ({target:.0%}, {label})"] = hedge_costs(a, target, target, ex)
    as_of = a.model.as_of
    nxt = None
    if as_of.year == a.delivery_start.year - 1 and as_of.month >= 7:  # H2 of Y-2 for the year after
        months_left = 12 - as_of.month + 1
        nxt = {
            "year": a.delivery_start.year + 1,
            "base": LADDER_BASE,
            "buy_now": LADDER_BASE / months_left,
            "months_left": months_left,
        }
    rec = {
        **r,
        "hedge_gas": target,
        "hedge_power": target,
        "execution": ex,
        "tranches": [str(t.date()) for t in a.tranches] if ex == "tranches" else [str(a.tranches[0].date())],
        "buy_now_gas": target / (1 if ex == "now" else n),
        "buy_now_power": target / (1 if ex == "now" else n),
        "rule": "ladder",
        "model_target": raw,
        "ladder_cap": cap,
        "prev_target": prev_target,
        "next_year": nxt,
        "model_only": {"hedge_gas": r["hedge_gas"], "hedge_power": r["hedge_power"], "execution": r["execution"]},
    }
    return dataclasses.replace(a, costs=costs, table=_table(costs), recommendation=rec)


# --------------------------------------------------------------------------------------------
# An honest bad case: never lower than a repeat of the 2021->2022 shock
# --------------------------------------------------------------------------------------------
def shock_prices(a: Analysis, df: pd.DataFrame) -> dict[str, np.ndarray]:
    """Monthly prices (delivery order) in the 'shock as big as 2021->2022, from today' scenario."""
    hp_ = _stress_prices(df)
    cal = [(a.delivery_start + pd.DateOffset(months=i)).month - 1 for i in range(12)]
    return {
        c: a.fwd_now[c] * (hp_[c]["avg_2022"] / hp_[c]["avg_2021"]) * hp_[c]["monthly_2022"][cal] / hp_[c]["avg_2022"]
        for c in ("gas", "power")
    }


def shock_bill(a: Analysis, df: pd.DataFrame, hg: float, hp: float) -> float:
    """The bill in the 'shock as big as 2021->2022, from today' stress scenario with hg / hp fixed at today's
    fixed price (the same definition as stress_test scenario b)."""
    prices = shock_prices(a, df)
    vol = _delivery_vol(a)
    return sum(
        (1 - h) * float(vol[c] @ prices[c]) + h * float(vol[c].sum() * a.fwd_now[c])
        for c, h in (("gas", hg), ("power", hp))
    )


def _column_hedge(name: str, r: dict) -> tuple[float, float]:
    """The fixed shares behind a strategy column (stress scenario: fixed part at today's fixed price)."""
    m = re.search(r"gas (\d+)%, power (\d+)%", name)
    if m:
        return int(m[1]) / 100, int(m[2]) / 100
    m = re.search(r"ladder \((\d+)%", name)
    if m:
        return int(m[1]) / 100, int(m[1]) / 100
    m = re.search(r"(\d+)%", name)
    return (int(m[1]) / 100,) * 2 if m else (0.0, 0.0)


def honest_bad_case(a: Analysis, df: pd.DataFrame) -> Analysis:
    """Add the bad case shown to clients: the HIGHER of the model's ES95 and the 2021->2022-shock bill, per strategy
    (table) and per share fixed (frontier), with its source ('model' or 'stress'). Why: the model's 12-month ranges
    were too narrow before the 2021-22 crisis (72% coverage instead of 90%, RESULTS.md). The recommendation is not
    recomputed: the default objective is mean-variance and doesn't use the bad case."""
    if a.model.as_of < pd.Timestamp(STRESS_YEAR, 12, 31):
        # the shock is built from 2021 and 2022 prices: before the end of 2022 that would be look-ahead, so a
        # historical decision date gets the model's ES95 only
        return a
    t = a.table.copy()
    shock = [shock_bill(a, df, *_column_hedge(k, a.recommendation)) for k in t.index]
    t["shock"] = shock
    t["bad_case"] = np.maximum(t["es95"], t["shock"])
    src = {
        k: ("stress" if sh > es * (1 + 1e-9) else "model")
        for k, sh, es in zip(t.index, t["shock"], t["es95"], strict=True)
    }
    f = a.frontier.copy()
    f["shock"] = [shock_bill(a, df, hg, hp) for hg, hp in zip(f.hedge_gas, f.hedge_power, strict=True)]
    f["bad_case"] = np.maximum(f["es95"], f["shock"])
    f["bad_case_source"] = np.where(f["shock"] > f["es95"] * (1 + 1e-9), "stress", "model")
    return dataclasses.replace(a, table=t, frontier=f, bad_case_source=src)


def bad_case(a: Analysis, row: str | None = None) -> tuple[float, str]:
    """(value, source) of the bad case shown for a strategy row (default: the recommendation = last row);
    the plain ES95 ('model') if honest_bad_case() was not applied."""
    r = a.table.loc[row] if row else a.table.iloc[-1]
    if "bad_case" in a.table:
        return float(r["bad_case"]), a.bad_case_source[r.name]
    return float(r["es95"]), "model"


RULES = ("ladder", "model-only")


def recommend(
    m: FittedModel,
    company: Company,
    df: pd.DataFrame,
    delivery_start: DateLike | None = None,
    n_paths: int = DEFAULT_PATHS,
    seed: int = DEFAULT_SEED,
    rule: str = "ladder",
    risk_aversion: float = DEFAULT_RISK_AVERSION,
    budget: float | str | None = None,
    prev_target: float | None = None,
    sims: dict | None = None,
    allow_timing: bool = False,
) -> Analysis:
    """The analysis a client sees. rule='ladder' (default, pre-registered): the optimiser at risk aversion 0.5 gives
    the model target and apply_ladder() turns it into the plan; rule='model-only': the optimiser's own answer at
    `risk_aversion` (not recommended: procyclical); a budget overrides both. The bad case is the honest one
    (honest_bad_case) whenever the 2021-22 prices are in the data."""
    if rule not in RULES:
        raise ValueError(f"rule must be one of {RULES}")
    ladder = rule == "ladder" and budget is None
    gamma = LADDER_GAMMA if ladder else risk_aversion
    a = analyse(m, company, delivery_start, n_paths, seed, gamma, sims=sims, allow_timing=allow_timing, budget=budget)
    if ladder:
        a = apply_ladder(a, prev_target)
    elif budget is None:  # model-only: label the optimiser's column as such (not recommended, RESULTS.md)
        costs = {
            (k.replace("HedgeWise (", f"{MODEL_ONLY} (", 1) if k.startswith("HedgeWise (") else k): v
            for k, v in a.costs.items()
        }
        a = dataclasses.replace(
            a, costs=costs, table=_table(costs), recommendation={**a.recommendation, "rule": "model-only"}
        )
    try:
        return honest_bad_case(a, df)
    except ValueError:  # no 2021-22 prices (demo data): the model's ES95 only
        return a
