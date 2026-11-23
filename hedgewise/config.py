"""Load config.yaml once and check it, so a typo fails loudly instead of silently changing results."""

from __future__ import annotations

import hashlib
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = ROOT / "config.yaml"
ENV_VAR = "HEDGEWISE_CONFIG"

# (section, key) -> expected type(s); every key the code reads must be listed here
_SCHEMA: dict[tuple[str, str], type | tuple[type, ...]] = {
    ("data", "start_date"): str,
    ("data", "gas_lag_days"): int,
    ("units", "mwh_per_m3"): float,
    ("units", "mwh_per_gj"): float,
    ("fixed_price", "premium"): float,
    ("fixed_price", "lookback_days"): int,
    ("fixed_price", "anchor_to_forward"): bool,
    ("garch", "start_values"): list,
    ("garch", "max_persistence"): float,
    ("garch", "nu_min"): float,
    ("garch", "nu_max"): (int, float),
    ("garch", "maxiter"): int,
    ("garch", "xatol"): float,
    ("garch", "fatol"): float,
    ("garch", "trading_days_per_year"): int,
    ("power_model", "fit_days"): int,
    ("power_model", "price_floor"): float,
    ("power_model", "nu_bounds"): list,
    ("power_model", "phi_max"): float,
    ("fit", "min_history_days"): int,
    ("fit", "min_corr_obs"): int,
    ("fit", "min_eigenvalue"): float,
    ("simulation", "n_paths"): int,
    ("simulation", "seed"): int,
    ("simulation", "nu_min"): float,
    ("simulation", "max_daily_vol"): float,
    ("simulation", "spike_headroom"): float,
    ("optimiser", "objective"): str,
    ("optimiser", "same_ratio"): bool,
    ("optimiser", "risk_aversion"): float,
    ("optimiser", "hedge_grid_step"): float,
    ("optimiser", "risk_quantile"): float,
    ("optimiser", "max_tranches"): int,
    ("optimiser", "same_month_days"): int,
    ("optimiser", "budget_margin"): float,
    ("optimiser", "budget_lookback_days"): int,
    ("policy", "max_step"): float,
    ("strategies", "partial_fixed_share"): float,
    ("stress", "year"): int,
    ("stress", "prev_year"): int,
    ("solar", "yield_kwh_per_kwp"): (int, float),
    ("solar", "monthly_share"): list,
    ("company_default", "name"): str,
    ("company_default", "gas_m3_per_year"): (int, float),
    ("company_default", "gas_monthly_share_pct"): list,
    ("company_default", "power_kwh_per_year"): (int, float),
    ("company_default", "power_monthly_share_pct"): list,
    ("consumption_input", "max_missing_months"): int,
    ("backtest", "years"): list,
    ("backtest", "n_paths"): int,
    ("backtest", "risk_aversions"): list,
    ("backtest", "decision_months"): list,
    ("backtest", "typical_month"): int,
    ("backtest", "pitch_year"): int,
    ("backtest", "policy_gamma"): float,
    ("backtest", "ladder_base"): float,
    ("backtest", "ladder_max_step"): float,
    ("app", "scenario_choices"): list,
    ("app", "default_scenarios"): int,
}
MONTHS_PER_YEAR = 12


def config_path() -> Path:
    """The config file in use: $HEDGEWISE_CONFIG if set, else config.yaml in the repository."""
    return Path(os.environ.get(ENV_VAR, DEFAULT_PATH))


def _validate(cfg: dict[str, Any], path: Path) -> None:
    for (section, key), typ in _SCHEMA.items():
        if section not in cfg or key not in cfg[section]:
            raise ValueError(f"{path.name}: missing '{section}.{key}'")
        value = cfg[section][key]
        if isinstance(value, bool) and typ is not bool and bool not in (typ if isinstance(typ, tuple) else (typ,)):
            raise ValueError(f"{path.name}: '{section}.{key}' must be {typ}, got a bool")
        if not isinstance(value, typ):
            raise ValueError(f"{path.name}: '{section}.{key}' must be {typ}, got {type(value).__name__} ({value!r})")
    for key in ("monthly_share",):
        if len(cfg["solar"][key]) != MONTHS_PER_YEAR:
            raise ValueError(f"{path.name}: solar.{key} needs 12 values")
    for key in ("gas_monthly_share_pct", "power_monthly_share_pct"):
        if len(cfg["company_default"][key]) != MONTHS_PER_YEAR:
            raise ValueError(f"{path.name}: company_default.{key} needs 12 values")
    for sector, prof in cfg["sector_profiles"].items():
        for commodity in ("gas", "power"):
            if len(prof[commodity]) != MONTHS_PER_YEAR:
                raise ValueError(f"{path.name}: sector_profiles.{sector}.{commodity} needs 12 values")


@lru_cache(maxsize=4)
def _load(path_str: str) -> dict[str, Any]:
    path = Path(path_str)
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    _validate(cfg, path)
    return cfg


def load_config() -> dict[str, Any]:
    """The validated configuration (read from disk once per process)."""
    return _load(str(config_path()))


def config_hash() -> str:
    """SHA-256 (first 12 hex characters) of the config file's bytes: recorded in every output."""
    return hashlib.sha256(config_path().read_bytes()).hexdigest()[:12]
