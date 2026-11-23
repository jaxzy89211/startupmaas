"""Price data: Dutch day-ahead power, TTF gas and (optional) EU ETS CO2, plus a synthetic demo history."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from scipy import signal

from .settings import DATA, DATA_START, GAS_LAG_DAYS


# --------------------------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------------------------
def _read_investing_csv(path: Path) -> pd.Series:
    """investing.com export: Date, Price, Open, High, Low, Vol., Change %  (US date format)."""
    df = pd.read_csv(path)
    date_col = next(c for c in df.columns if c.strip().lower() in ("date", "datum"))
    price_col = next(c for c in df.columns if c.strip().lower() in ("price", "close", "koers", "slot"))
    s = df[price_col]
    if s.dtype == object:
        s = pd.to_numeric(s.str.replace(",", ""), errors="coerce")
    idx = pd.to_datetime(df[date_col], format="mixed", dayfirst=False)
    return pd.Series(s.values, index=idx).sort_index().dropna()


def load_prices(data_dir: Path = DATA) -> pd.DataFrame:
    """Daily calendar-day frame with columns gas, power and (if data/co2_eua.csv exists) co2.
    Gas/CO2 are forward-filled over weekends; column 'gas_trade' flags real gas trading days."""
    g = pd.read_csv(data_dir / "gas_ttf.csv")
    gcol = "Close" if "Close" in g.columns else None
    gas = (
        pd.Series(g[gcol].values, index=pd.to_datetime(g["Date"]))
        if gcol
        else _read_investing_csv(data_dir / "gas_ttf.csv")
    )
    p = pd.read_csv(data_dir / "power_nl_daily.csv", index_col=0)
    pidx = pd.to_datetime(p.index, utc=True).tz_convert("Europe/Amsterdam").tz_localize(None).normalize()
    power = pd.Series(p.iloc[:, 0].values, index=pidx)

    idx = pd.date_range(DATA_START, min(power.index.max(), gas.index.max() + pd.Timedelta(days=GAS_LAG_DAYS)), freq="D")
    df = pd.DataFrame(index=idx)
    df["power"] = power.reindex(idx)
    df["gas_trade"] = gas.reindex(idx).notna()
    df["gas"] = gas.reindex(idx).ffill()
    co2_path = data_dir / "co2_eua.csv"
    if co2_path.exists():
        co2 = _read_investing_csv(co2_path)
        df["co2"] = co2.reindex(idx).ffill()
        df["co2_trade"] = co2.reindex(idx).notna()
    df = df.dropna(subset=["power", "gas"])
    return df


def demo_prices(seed: int = 7) -> pd.DataFrame:
    """Synthetic 2019-2024 history with a 2021-22 style crisis, so the model runs without any files."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2019-01-01", "2024-12-31", freq="D")
    t = np.arange(len(idx)) / 365
    crisis = 2.2 * np.exp(-(((t - 3.6) / 0.6) ** 2))
    log_gas = np.log(15) + crisis + np.cumsum(rng.standard_t(4, len(idx)) * 0.025) * 0.6
    gas = np.exp(log_gas)
    season = 0.08 * np.cos(2 * np.pi * (idx.day_of_year - 15) / 365)
    weekend = np.where(idx.day_of_week >= 5, -0.15, 0.0)
    u = signal.lfilter([1], [1, -0.7], rng.standard_t(4, len(idx)) * 0.12)
    power = np.exp(1.2 + 0.75 * np.log(gas) + season + weekend + u)
    df = pd.DataFrame({"power": power, "gas": gas, "gas_trade": idx.day_of_week < 5}, index=idx)
    return df
