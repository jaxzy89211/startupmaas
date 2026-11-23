"""Procurement strategies, risk metrics, the HedgeWise optimiser and policy, stress tests and advice text."""
from __future__ import annotations

import dataclasses

import numpy as np
import pandas as pd

from .company import Company
from .models import FittedModel, PowerModel
from .settings import (ANCHOR_TO_FORWARD, BUDGET_MARGIN, FORWARD_LOOKBACK_DAYS, FORWARD_PREMIUM, OBJECTIVE,
                       POLICY_MAX_STEP, SAME_RATIO, STRESS_LABELS, STRESS_PREV_YEAR, STRESS_YEAR)
from .simulate import simulate


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


def real_forward(commodity: str, as_of, delivery_start) -> dict | None:
    """Real calendar-year forward quote (forwards.py, data/forwards.csv) on or before as_of, if the file exists
    and delivery starts on 1 January; otherwise None and the proxy is used."""
    ds = pd.Timestamp(delivery_start)
    if (ds.month, ds.day) != (1, 1):
        return None
    from . import forwards
    if not forwards.available():
        return None
    return forwards.forward_quote(commodity, as_of, ds.year)


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
    price_src = {}
    sims = dict(sims)  # never modify the (cached) input paths in place
    for c in ("gas", "power"):
        hist = m.history[c]
        adj = (lambda t: season_adjustment(m.power, t, ds)) if c == "power" else (lambda t: 1.0)
        q = real_forward(c, m.as_of, ds)
        if q:   # real calendar forward: it IS the fixed price (and, below, the anchor)
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
           "objective": OBJECTIVE, "anchor_scale": anchor_k, "price_source": price_src}
    return Analysis(m, company, ds, tr, spot_m, fwd_now, fwd_tr, costs, table, frontier, rec, sims)
# Why 20 percentage points: the buying season has 6 monthly decision dates, i.e. 5 steps between them, and
# 5 x 20pp = 100%. So the cap never stops the plan from reaching a full hedge within one season; it only stops
# the target from jumping (e.g. 30% -> 90%) in one month right after a volatility spike, when GARCH volatility -
# and therefore the optimiser's target - is highest. The number follows from the calendar, not from 2021-2025
# backtest outcomes, and it is NOT to be tuned on the backtest.


def policy_plan(a: Analysis, months_ahead: int, prev_target: float | None = None, already: float = 0.0,
                max_step: float = POLICY_MAX_STEP) -> dict:
    """Turn the optimiser's (or budget mode's) target share into this month's purchase.

    months_ahead : number of monthly decision dates left INCLUDING this one (6 at the start of a season)
    prev_target  : the (capped) target set on the previous decision date; None on the first date (no cap then)
    already      : share already fixed on earlier dates

    Rules: (1) target = optimiser's share, but at most prev_target + max_step;
           (2) the part still to buy is spread in equal monthly tranches over the remaining dates - never
               front-loaded; (3) nothing is ever sold back (if the target fell below what is fixed, buy 0)."""
    raw = float(a.recommendation["hedge_gas"])          # one share for gas and power (SAME_RATIO)
    target = raw if prev_target is None else min(raw, prev_target + max_step)
    n = max(int(months_ahead), 1)
    buy = max(0.0, target - already) / n
    buy = min(buy, 1.0 - already)
    return {"raw_target": raw, "target": target, "capped": target < raw - 1e-12, "buy_now": buy,
            "already": already, "months_ahead": n,
            "schedule": [buy] + [max(0.0, target - already - buy) / max(n - 1, 1)] * (n - 1)}


def _stress_prices(df: pd.DataFrame) -> dict:
    """Per commodity: realised 2022 monthly averages (calendar months 1-12) and the 2021 / 2022 annual averages."""
    out = {}
    for c in ("gas", "power"):
        y22 = df.loc[f"{STRESS_YEAR}-01-01":f"{STRESS_YEAR}-12-31", c]
        y21 = df.loc[f"{STRESS_PREV_YEAR}-01-01":f"{STRESS_PREV_YEAR}-12-31", c]
        monthly = y22.groupby(y22.index.month).mean().reindex(range(1, 13))
        if monthly.isna().any() or y21.empty:
            raise ValueError(f"price data for all of {STRESS_PREV_YEAR} and {STRESS_YEAR} is needed for the stress test")
        out[c] = {"monthly_2022": monthly.values, "avg_2021": float(y21.mean()), "avg_2022": float(y22.mean())}
    return out


def stress_test(a: Analysis, df: pd.DataFrame) -> dict:
    """Two historical stress scenarios for next year's bill (NOT forecasts):

    (a) "repeat": the realised 2022 monthly gas and power prices exactly as they were.
    (b) "shock" : a shock as big as 2021->2022, starting from today's level:
                  price[m] = today's fixed price x (2022 annual avg / 2021 annual avg) x (2022 month m / 2022 annual avg)
                  i.e. the 2022 monthly shape, scaled so the year averages fixed price x multiplier.
    For each: bills for all variable, the HedgeWise recommendation, fix 60% now and fix 100% now; the hedged part
    is priced at today's fixed price. Also the implied (volume-weighted) average gas and power price, so a reader
    can sanity-check the scenario."""
    ds = a.delivery_start
    cal = [(ds + pd.DateOffset(months=i)).month - 1 for i in range(12)]   # calendar-month index per delivery month
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

        def bill(h):
            return sum((1 - h) * var_cost[c] + h * fix_cost[c] for c in ("gas", "power"))

        bills = {"All variable": bill(0.0), "HedgeWise": bill(rec["hedge_gas"]),
                 "Fix 60% now": bill(0.6), "Fix 100% now": bill(1.0)}
        scenarios.append({"key": key, "label": STRESS_LABELS[key], "bills": bills, "avg_price": avg,
                          "multiplier": mult})
    return {"scenarios": scenarios, "hedge": rec["hedge_gas"], "fixed_price": dict(a.fwd_now),
            "note": "historical stress scenarios, not forecasts"}


def _eur_m(x: float, lang: str) -> str:
    return f"€{x/1e6:.2f}m" if lang == "en" else f"€ {x/1e6:.2f} mln".replace(".", ",")


def stress_sentence(s: dict, lang: str = "en") -> str:
    """One sentence per scenario, with the implied average prices."""
    out = []
    for sc in s["scenarios"]:
        b, p = sc["bills"], sc["avg_price"]
        if lang == "nl":
            pr = f"gas gem. € {p['gas']:.0f}, stroom € {p['power']:.0f} per MWh"
            out.append(f"{sc['label']['nl']} ({pr}): variabel {_eur_m(b['All variable'], lang)}, "
                       f"HedgeWise-advies {_eur_m(b['HedgeWise'], lang)}, 100% vast {_eur_m(b['Fix 100% now'], lang)}.")
        else:
            pr = f"gas avg €{p['gas']:.0f}, power €{p['power']:.0f} per MWh"
            out.append(f"{sc['label']['en']} ({pr}): all variable {_eur_m(b['All variable'], lang)}, "
                       f"HedgeWise plan {_eur_m(b['HedgeWise'], lang)}, fix 100% {_eur_m(b['Fix 100% now'], lang)}.")
    tail = ("Historical stress scenarios, not forecasts." if lang == "en"
            else "Historische stressscenario's, geen voorspellingen.")
    return " ".join(out) + " " + tail


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


def frontier_scale(a: Analysis) -> tuple[float, float]:
    """(extra expected cost, bad-case reduction) of fixing 100% instead of 0%, along the frontier that is drawn."""
    r = a.recommendation
    f = a.frontier
    f = f[(f.execution == r["execution"]) & np.isclose(f.hedge_gas, f.hedge_power)].sort_values("hedge_gas")
    lo, hi = f.iloc[0], f.iloc[-1]
    return float(hi.expected - lo.expected), float(lo.es95 - hi.es95)


def frontier_scale_sentence(a: Analysis, lang: str = "en") -> str:
    """The sentence under the trade-off chart that makes its scale explicit."""
    x, y = frontier_scale(a)
    if lang == "nl":
        k = lambda v: (f"€\u00a0{round(v, -3):,.0f}".replace(",", ".") if abs(v) < 1e6
                       else f"€\u00a0{v/1e6:.2f}\u00a0mln".replace(".", ","))
        return f"Alles vastzetten kost gemiddeld {k(x)} meer en verlaagt het slechte scenario met {k(y)}."
    k = lambda v: f"€{v/1e3:,.0f}k" if abs(v) < 1e6 else f"€{v/1e6:.2f}m"
    return f"Fixing everything costs {k(x)} more on average and cuts the bad case by {k(y)}."
