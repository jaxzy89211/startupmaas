"""All model assumptions and constants in one place (step 2 moves them to config.yaml)."""
from __future__ import annotations

from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parent.parent   # the repository folder
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


SOLAR_SHARE = np.array([3.0, 4.5, 8.0, 11.0, 13.0, 14.0, 14.0, 11.5, 9.0, 6.5, 3.5, 2.0])
SOLAR_YIELD_KWH_PER_KWP = 900  # typical for south-east Netherlands


# --------------------------------------------------------------------------------------------
# HedgeWise policy: a disciplined layered plan on top of the optimiser
# --------------------------------------------------------------------------------------------
POLICY_MAX_STEP = 0.20


# --------------------------------------------------------------------------------------------
# Historical stress scenarios: "what if 2022 happens again?"  (NOT forecasts)
# --------------------------------------------------------------------------------------------
STRESS_YEAR = 2022
STRESS_PREV_YEAR = 2021
STRESS_KEYS = ["All variable", "HedgeWise", "Fix 60% now", "Fix 100% now"]
STRESS_LABELS = {
    "repeat": {"en": "2022 prices repeat", "nl": "Prijzen van 2022 herhalen zich"},
    "shock": {"en": "A shock as big as 2021→2022, from today", "nl": "Een schok zo groot als 2021→2022, vanaf nu"},
}


BUDGET_MARGIN = 0.20
