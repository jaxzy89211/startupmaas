"""The company whose energy bill is analysed: monthly consumption profile and optional rooftop solar."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import numpy as np
import pandas as pd

from .settings import COMPANY_DEFAULT, KWH_PER_MWH, MWH_PER_M3, PERCENT, SOLAR_SHARE, SOLAR_YIELD_KWH_PER_KWP

_D = COMPANY_DEFAULT  # config.yaml: company_default


# --------------------------------------------------------------------------------------------
# Company
# --------------------------------------------------------------------------------------------
@dataclasses.dataclass
class Company:
    """Monthly consumption profile. Replace with pilot data (12-24 months of bills) when you have it."""

    name: str = _D["name"]
    # m3 of natural gas per month (default: config.yaml company_default)
    gas_m3: tuple = tuple(_D["gas_m3_per_year"] * s / PERCENT for s in _D["gas_monthly_share_pct"])
    # kWh of electricity per month (default: config.yaml company_default)
    power_kwh: tuple = tuple(_D["power_kwh_per_year"] * s / PERCENT for s in _D["power_monthly_share_pct"])
    solar_kwp: float = 0.0  # rooftop PV, assumed fully self-consumed

    @property
    def gas_mwh(self) -> np.ndarray:
        """Monthly gas use in MWh."""
        return np.asarray(self.gas_m3, float) * MWH_PER_M3

    @property
    def power_mwh(self) -> np.ndarray:
        """Monthly power bought from the grid in MWh (consumption minus self-consumed solar)."""
        return np.maximum(np.asarray(self.power_kwh, float) / KWH_PER_MWH - solar_mwh(self.solar_kwp), 0.0)

    @classmethod
    def from_csv(cls, path: str | Path, name: str | None = None) -> Company:
        """CSV with columns month (1-12 or a date), gas_m3, power_kwh. If several years are given,
        the average per calendar month is used."""
        df = pd.read_csv(path)
        m = df["month"]
        month = m.astype(int) if pd.api.types.is_numeric_dtype(m) else pd.to_datetime(m).dt.month
        prof = df.assign(_m=month).groupby("_m")[["gas_m3", "power_kwh"]].mean().reindex(range(1, 13))
        if prof.isna().any().any():
            raise ValueError(f"{path}: need data for all 12 calendar months")
        return cls(name=name or Path(path).stem, gas_m3=tuple(prof.gas_m3), power_kwh=tuple(prof.power_kwh))


def solar_mwh(kwp: float) -> np.ndarray:
    """Monthly rooftop-solar production in MWh (Jan..Dec) for `kwp` kilowatt-peak."""
    return kwp * SOLAR_YIELD_KWH_PER_KWP / KWH_PER_MWH * SOLAR_SHARE / SOLAR_SHARE.sum()
