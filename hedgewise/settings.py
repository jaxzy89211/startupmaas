"""Model settings, read once from config.yaml (see that file for what each value means and why).

Only fixed conventions live here as literals (days, months, unit factors); every modelling assumption comes
from the config file.
"""

from __future__ import annotations

import numpy as np

from .config import ROOT, load_config

_C = load_config()

DATA = ROOT / "data"
OUT = ROOT / "output"

# fixed conventions (not assumptions)
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
KWH_PER_MWH = 1000
PERCENT = 100

# data and units
DATA_START = _C["data"]["start_date"]
GAS_LAG_DAYS = _C["data"]["gas_lag_days"]
MWH_PER_M3 = _C["units"]["mwh_per_m3"]
MWH_PER_GJ = _C["units"]["mwh_per_gj"]

# fixed price (forward proxy) and anchoring
FORWARD_PREMIUM = _C["fixed_price"]["premium"]
FORWARD_LOOKBACK_DAYS = _C["fixed_price"]["lookback_days"]
ANCHOR_TO_FORWARD = _C["fixed_price"]["anchor_to_forward"]

# GARCH(1,1)-t
GARCH_START_VALUES = [tuple(v) for v in _C["garch"]["start_values"]]
GARCH_MAX_PERSISTENCE = _C["garch"]["max_persistence"]
GARCH_NU_MIN = _C["garch"]["nu_min"]
GARCH_NU_MAX = _C["garch"]["nu_max"]
GARCH_MAXITER = _C["garch"]["maxiter"]
GARCH_XATOL = _C["garch"]["xatol"]
GARCH_FATOL = _C["garch"]["fatol"]
TRADING_DAYS_PER_YEAR = _C["garch"]["trading_days_per_year"]

# power model
POWER_FIT_DAYS = _C["power_model"]["fit_days"]
POWER_FLOOR = _C["power_model"]["price_floor"]
POWER_NU_BOUNDS = tuple(_C["power_model"]["nu_bounds"])
POWER_PHI_MAX = _C["power_model"]["phi_max"]

# fitting
MIN_HISTORY_DAYS = _C["fit"]["min_history_days"]
MIN_CORR_OBS = _C["fit"]["min_corr_obs"]
MIN_EIGENVALUE = _C["fit"]["min_eigenvalue"]

# simulation
DEFAULT_PATHS = _C["simulation"]["n_paths"]
DEFAULT_SEED = _C["simulation"]["seed"]
NU_MIN_SIM = _C["simulation"]["nu_min"]
MAX_DAILY_VOL = _C["simulation"]["max_daily_vol"]
SPIKE_HEADROOM = _C["simulation"]["spike_headroom"]

# optimiser and strategies
OBJECTIVE = _C["optimiser"]["objective"]
SAME_RATIO = _C["optimiser"]["same_ratio"]
DEFAULT_RISK_AVERSION = _C["optimiser"]["risk_aversion"]
HEDGE_GRID_STEP = _C["optimiser"]["hedge_grid_step"]
RISK_QUANTILE = _C["optimiser"]["risk_quantile"]
MAX_TRANCHES = _C["optimiser"]["max_tranches"]
SAME_MONTH_DAYS = _C["optimiser"]["same_month_days"]
BUDGET_MARGIN = _C["optimiser"]["budget_margin"]
BUDGET_LOOKBACK_DAYS = _C["optimiser"]["budget_lookback_days"]
POLICY_MAX_STEP = _C["policy"]["max_step"]
PARTIAL_FIXED_SHARE = _C["strategies"]["partial_fixed_share"]

# stress test
STRESS_YEAR = _C["stress"]["year"]
STRESS_PREV_YEAR = _C["stress"]["prev_year"]
STRESS_KEYS = ["All variable", "HedgeWise", "Fix 60% now", "Fix 100% now"]
STRESS_LABELS = {
    "repeat": {"en": "2022 prices repeat", "nl": "Prijzen van 2022 herhalen zich"},
    "shock": {"en": "A shock as big as 2021→2022, from today", "nl": "Een schok zo groot als 2021→2022, vanaf nu"},
}

# solar and the default company
SOLAR_SHARE = np.array(_C["solar"]["monthly_share"])
SOLAR_YIELD_KWH_PER_KWP = _C["solar"]["yield_kwh_per_kwp"]
COMPANY_DEFAULT = _C["company_default"]

# consumption input
SECTOR_PROFILES = _C["sector_profiles"]
MAX_MISSING_MONTHS = _C["consumption_input"]["max_missing_months"]

# backtest and app
BACKTEST = _C["backtest"]
APP = _C["app"]
