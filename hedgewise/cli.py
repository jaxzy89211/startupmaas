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

import pandas as pd

from .charts import plot_analysis, plot_fit, plot_stress
from .data import demo_prices, load_prices
from .models import describe, fit
from .provenance import one_line, write_csv, write_json
from .settings import DEFAULT_PATHS, DEFAULT_RISK_AVERSION, DEFAULT_SEED, OUT
from .strategies import Analysis, analyse, recommendation_text, stress_test


# --------------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> Analysis:
    """Run the model command line; returns the analysis."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--as-of", help="decision date YYYY-MM-DD (default: last date with data)")
    ap.add_argument("--delivery-start", help="first delivery month YYYY-MM-01 (default: next 1 January)")
    ap.add_argument(
        "--risk-aversion", type=float, default=DEFAULT_RISK_AVERSION, help="0 = only expected cost, 1 = only bad case"
    )
    ap.add_argument("--paths", type=int, default=DEFAULT_PATHS)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    from . import company_input

    company_input.add_cli_args(ap)
    ap.add_argument("--solar-kwp", type=float, default=0.0)
    ap.add_argument("--demo", action="store_true", help="run on synthetic data")
    ap.add_argument("--show", action="store_true", help="open the chart window")
    ap.add_argument("--allow-timing", action="store_true", help="let HedgeWise fix everything at once")
    ap.add_argument(
        "--budget",
        help="energy budget in EUR, or 'auto' (last 12 months + 20%%); "
        "switches to: smallest hedge that keeps VaR95 within budget",
    )
    args = ap.parse_args(argv)

    OUT.mkdir(exist_ok=True)
    df = demo_prices() if args.demo else load_prices()
    company = company_input.company_from_args(args)
    company.solar_kwp = args.solar_kwp
    as_of = args.as_of or (str(df.index.max().date()) if not args.demo else "2021-06-30")
    m = fit(df, as_of)
    print(describe(m), "\n")
    a = analyse(
        m,
        company,
        args.delivery_start,
        args.paths,
        args.seed,
        args.risk_aversion,
        allow_timing=args.allow_timing,
        budget=args.budget,
    )
    pd.set_option("display.width", 160)
    print((a.table / 1e3).round(0).astype(int).rename(columns=lambda c: c + " (kEUR)").to_string(), "\n")
    print(
        "Fixed price available today: "
        + ", ".join(f"{k} {v:.1f} EUR/MWh [{a.recommendation['price_source'][k]}]" for k, v in a.fwd_now.items())
    )
    print(recommendation_text(a))
    if not args.demo:
        st_ = stress_test(a, df)
        print("\nStress scenarios (historical, NOT forecasts), EUR k:")
        for sc in st_["scenarios"]:
            p = sc["avg_price"]
            mult = (
                f", multiplier gas x{sc['multiplier']['gas']:.2f}, power x{sc['multiplier']['power']:.2f}"
                if sc["multiplier"]["gas"]
                else ""
            )
            print(
                f"  {sc['label']['en']}: implied average price gas {p['gas']:.0f}, power {p['power']:.0f} EUR/MWh{mult}"
            )
            print("    " + " | ".join(f"{k} {v / 1e3:,.0f}" for k, v in sc["bills"].items()))
    tag = ("demo_" if args.demo else "") + f"{m.as_of:%Y-%m-%d}"
    plot_analysis(a, OUT / f"analysis_{tag}.png")
    plot_fit(m, OUT / f"fit_{tag}.png")
    if not args.demo:
        plot_stress(st_, OUT / f"stress_{tag}.png")
    write_csv(a.table, OUT / f"strategies_{tag}.csv")
    write_json(a.recommendation, OUT / f"recommendation_{tag}.json", indent=2)
    print("Provenance: " + one_line())
    print(f"\nCharts and tables written to {OUT}/  (analysis_{tag}.png, fit_{tag}.png)")
    if args.show:
        import matplotlib.pyplot as plt

        img = plt.imread(OUT / f"analysis_{tag}.png")
        plt.figure(figsize=(14, 9))
        plt.imshow(img)
        plt.axis("off")
        plt.show()
    return a
