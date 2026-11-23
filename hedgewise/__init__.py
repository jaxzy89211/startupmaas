"""HedgeWise: energy price-risk model for mid-sized Dutch companies.

Public, audit-ready interface. Every name below is part of the documented API; anything not listed
(names starting with "_") is an implementation detail.

Pipeline (each step only uses data available on the as-of date):

    df = load_prices()                          # data.py      daily gas / power (/ CO2) prices
    m = fit(df, as_of)                          # models.py    GARCH(1,1)-t + power regression + correlation
    sims = simulate(m, end, n_paths, seed)      # simulate.py  Monte Carlo price paths
    a = analyse(m, Company(), sims=sims)        # strategies.py  bills per strategy, risk, recommendation
    stress_test(a, df)                          # strategies.py  historical stress scenarios
    plot_analysis(a, path)                      # charts.py
    report.make_report(a, path, "nl")           # report.py    one-page PDF

Submodules not imported here (heavier or optional): report, backtest, company_input, forwards, cli.
"""
from .charts import COLORS, FRONTIER_TEXT_EN, STRAT_COLORS, plot_analysis, plot_fit, plot_frontier, plot_stress
from .company import Company, solar_mwh
from .data import demo_prices, load_prices
from .models import FittedModel, GarchT, PowerModel, describe, fit, fit_garch_t, fit_power
from .settings import (ANCHOR_TO_FORWARD, BUDGET_MARGIN, DATA, FORWARD_LOOKBACK_DAYS, FORWARD_PREMIUM,
                       MAX_DAILY_VOL, MONTHS, MWH_PER_M3, NU_MIN_SIM, OBJECTIVE, OUT, POLICY_MAX_STEP,
                       POWER_FIT_DAYS, POWER_FLOOR, ROOT, SAME_RATIO, SOLAR_SHARE, SOLAR_YIELD_KWH_PER_KWP,
                       STRESS_KEYS, STRESS_LABELS, STRESS_PREV_YEAR, STRESS_YEAR)
from .simulate import simulate
from .strategies import (Analysis, analyse, frontier_scale, frontier_scale_sentence, policy_plan, real_forward,
                         recommendation_text, risk_stats, season_adjustment, stress_sentence, stress_test,
                         tranche_dates, trailing_bill)

__version__ = "0.5.0"


def main(argv=None):
    """Model command line (same as `python hedgewise_model.py`)."""
    from .cli import main as _main
    return _main(argv)


__all__ = [
    # data / company
    "load_prices", "demo_prices", "Company", "solar_mwh",
    # models and simulation
    "fit", "fit_garch_t", "fit_power", "FittedModel", "GarchT", "PowerModel", "describe", "simulate",
    # strategies, risk, advice
    "analyse", "Analysis", "risk_stats", "tranche_dates", "season_adjustment", "real_forward", "trailing_bill",
    "policy_plan", "stress_test", "stress_sentence", "recommendation_text", "frontier_scale",
    "frontier_scale_sentence",
    # charts
    "plot_analysis", "plot_frontier", "plot_fit", "plot_stress", "COLORS", "STRAT_COLORS", "FRONTIER_TEXT_EN",
    # settings
    "ROOT", "DATA", "OUT", "MWH_PER_M3", "FORWARD_PREMIUM", "FORWARD_LOOKBACK_DAYS", "POWER_FLOOR", "NU_MIN_SIM",
    "MAX_DAILY_VOL", "POWER_FIT_DAYS", "ANCHOR_TO_FORWARD", "SAME_RATIO", "OBJECTIVE", "MONTHS", "SOLAR_SHARE",
    "SOLAR_YIELD_KWH_PER_KWP", "POLICY_MAX_STEP", "STRESS_YEAR", "STRESS_PREV_YEAR", "STRESS_KEYS",
    "STRESS_LABELS", "BUDGET_MARGIN",
    "main", "__version__",
]
