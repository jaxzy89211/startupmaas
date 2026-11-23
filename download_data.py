"""
Download / refresh the price data used by HedgeWise.

    python download_data.py              # power (Energy-Charts) + gas (Yahoo TTF=F), 2019 -> today
    python download_data.py --entsoe     # power from ENTSO-E instead (needs env var ENTSOE_TOKEN)

Writes
    data/power_nl_hourly.csv   timestamp (UTC), price_eur_mwh   (15-min resolution from Oct 2025)
    data/power_nl_daily.csv    Europe/Amsterdam calendar-day average
    data/gas_ttf.csv           Date, Open, High, Low, Close, Volume   (TTF front-month, EUR/MWh)

CO2 (EU ETS) has no free API that works automatically:
    investing.com -> "Carbon Emissions Futures" -> Historical Data -> Daily -> 01/01/2019 - today -> Download
    save as data/co2_eua.csv. The model picks it up automatically (adds log CO2 to the power equation and a
    CO2 GARCH process). Without it, CO2 is still priced in implicitly through power prices.

Licensing: Energy-Charts data is CC BY 4.0 (source: Bundesnetzagentur | SMARD.de) - fine for a pitch, cite it.
For a commercial product use ENTSO-E (register at transparency.entsoe.eu, then email transparency@entsoe.eu
asking for "Restful API access"; the token arrives in a few days).
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd
import requests

DATA = Path(__file__).resolve().parent / "data"


def _get(url: str, params: dict, tries: int = 8) -> requests.Response:
    """GET with back-off on HTTP 429 (rate limited)."""
    for attempt in range(tries):
        r = requests.get(url, params=params, timeout=120)
        if r.status_code == 429:
            wait = 30 * (attempt + 1)
            print(f"   rate-limited, waiting {wait}s")
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r
    raise RuntimeError(f"gave up after {tries} tries: {url}")


def power_energy_charts(start_year: int = 2019) -> pd.DataFrame:
    """Dutch day-ahead prices from Energy-Charts, one request per year."""
    frames = []
    for y in range(start_year, dt.date.today().year + 1):
        r = _get("https://api.energy-charts.info/price", {"bzn": "NL", "start": f"{y}-01-01", "end": f"{y}-12-31"})
        j = r.json()
        df = pd.DataFrame(
            {"timestamp": pd.to_datetime(j["unix_seconds"], unit="s", utc=True), "price_eur_mwh": j["price"]}
        )
        print(f"   power {y}: {len(df)} rows")
        frames.append(df)
        time.sleep(15)  # the API rate-limits bursts
    return pd.concat(frames).drop_duplicates("timestamp").sort_values("timestamp")


def power_entsoe(token: str, start_year: int = 2019) -> pd.DataFrame:
    """Day-ahead prices (documentType A44) for the NL bidding zone. Untested until you have a token."""
    zone = "10YNL----------L"
    ns = {"ns": "urn:iec62325.351:tc57wg16:451-3:publicationdocument:7:3"}
    rows = []
    for y in range(start_year, dt.date.today().year + 1):
        r = _get(
            "https://web-api.tp.entsoe.eu/api",
            {
                "securityToken": token,
                "documentType": "A44",
                "in_Domain": zone,
                "out_Domain": zone,
                "periodStart": f"{y - 1}12312300",
                "periodEnd": f"{y}12312300",
            },
        )
        root = ET.fromstring(r.content)
        tag_ns = root.tag.split("}")[0].strip("{")
        ns["ns"] = tag_ns
        for period in root.iterfind(".//ns:Period", ns):
            start = pd.Timestamp(period.find("ns:timeInterval/ns:start", ns).text)
            res = period.find("ns:resolution", ns).text
            step = pd.Timedelta(minutes=15) if res == "PT15M" else pd.Timedelta(hours=1)
            for p in period.iterfind("ns:Point", ns):
                pos = int(p.find("ns:position", ns).text)
                rows.append((start + (pos - 1) * step, float(p.find("ns:price.amount", ns).text)))
        print(f"   power {y}: ENTSO-E ok")
        time.sleep(2)
    return (
        pd.DataFrame(rows, columns=["timestamp", "price_eur_mwh"]).drop_duplicates("timestamp").sort_values("timestamp")
    )


def gas_yahoo() -> pd.DataFrame:
    """Daily TTF front-month prices from Yahoo Finance (TTF=F)."""
    import yfinance as yf

    g = yf.download("TTF=F", start="2019-01-01", progress=False, auto_adjust=False)
    g.columns = [c[0] if isinstance(c, tuple) else c for c in g.columns]
    g = g[["Open", "High", "Low", "Close", "Volume"]]
    g.index.name = "Date"
    return g


def main() -> None:
    """Download power and gas prices into data/."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--entsoe", action="store_true", help="use ENTSO-E for power (needs ENTSOE_TOKEN)")
    ap.add_argument("--skip-power", action="store_true")
    ap.add_argument("--skip-gas", action="store_true")
    args = ap.parse_args()
    DATA.mkdir(exist_ok=True)

    if not args.skip_power:
        print("Downloading Dutch day-ahead power prices ...")
        if args.entsoe:
            tok = os.environ.get("ENTSOE_TOKEN")
            if not tok:
                raise SystemExit("Set ENTSOE_TOKEN first:  export ENTSOE_TOKEN=your-token")
            p = power_entsoe(tok)
        else:
            p = power_energy_charts()
        p.to_csv(DATA / "power_nl_hourly.csv", index=False)
        d = (
            p.set_index("timestamp")
            .price_eur_mwh.tz_convert("Europe/Amsterdam")
            .resample("D")
            .mean()
            .dropna()
            .rename("price_eur_mwh")
        )
        d.to_csv(DATA / "power_nl_daily.csv")
        print(f"   saved {len(p)} intervals, {len(d)} days -> data/power_nl_daily.csv")

    if not args.skip_gas:
        print("Downloading TTF gas (Yahoo TTF=F) ...")
        try:
            g = gas_yahoo()
            if len(g) < 100:
                raise RuntimeError("too few rows")
            g.to_csv(DATA / "gas_ttf.csv")
            print(f"   saved {len(g)} days -> data/gas_ttf.csv")
        except Exception as e:  # noqa: BLE001
            print(
                f"   FAILED ({e}). Download manually from investing.com -> 'Dutch TTF Natural Gas Futures' "
                "-> Historical Data -> Daily -> save as data/gas_ttf.csv (the model reads that format too)."
            )

    if not (DATA / "co2_eua.csv").exists():
        print("\nCO2 not found: download it manually (see the top of this file). Optional.")


if __name__ == "__main__":
    main()
