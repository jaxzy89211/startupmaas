"""The company whose energy bill is analysed: monthly consumption profile and optional rooftop solar."""
from __future__ import annotations

import dataclasses
from pathlib import Path

import numpy as np
import pandas as pd

from .settings import MWH_PER_M3, SOLAR_SHARE, SOLAR_YIELD_KWH_PER_KWP


# --------------------------------------------------------------------------------------------
# Company
# --------------------------------------------------------------------------------------------
@dataclasses.dataclass
class Company:
    """Monthly consumption profile. Replace with pilot data (12-24 months of bills) when you have it."""
    name: str = "Limburg food processor (hypothetical)"
    # m3 of natural gas per month. 1.2 million m3/yr: process steam (flat) + space heating (winter)
    gas_m3: tuple = tuple(1_200_000 * s / 100 for s in
                          (11.5, 10.5, 9.5, 8.0, 7.0, 6.0, 5.5, 5.5, 6.5, 8.0, 10.0, 12.0))
    # kWh of electricity per month. 5 GWh/yr: flat production with a small summer cooling bump
    power_kwh: tuple = tuple(5_000_000 * s / 100 for s in
                             (8.2, 7.6, 8.3, 8.1, 8.4, 8.5, 8.9, 8.8, 8.4, 8.4, 8.2, 8.2))
    solar_kwp: float = 0.0  # rooftop PV, assumed fully self-consumed

    @property
    def gas_mwh(self) -> np.ndarray:
        return np.asarray(self.gas_m3, float) * MWH_PER_M3

    @property
    def power_mwh(self) -> np.ndarray:
        return np.maximum(np.asarray(self.power_kwh, float) / 1000 - solar_mwh(self.solar_kwp), 0.0)

    @classmethod
    def from_csv(cls, path: str | Path, name: str | None = None) -> "Company":
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
    return kwp * SOLAR_YIELD_KWH_PER_KWP / 1000 * SOLAR_SHARE / SOLAR_SHARE.sum()
