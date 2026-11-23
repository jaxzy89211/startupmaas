"""
Download / refresh the price data used by HedgeWise.

    python download_data.py              # power (Energy-Charts) + gas (Yahoo TTF=F), 2019 -> today
    python download_data.py --entsoe     # power from ENTSO-E instead (needs env var ENTSOE_TOKEN)
    python download_data.py --extended   # NL, DE-LU and BE power + TTF gas, as far back as possible -> data/extended/

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


# Energy-Charts bidding-zone codes per market; Germany was DE-AT-LU until 30 Sep 2018 and DE-LU after.
ZONES = {
    "NL": [("NL", None, None)],
    "BE": [("BE", None, None)],
    "DE-LU": [("DE-AT-LU", None, "2018-09-30"), ("DE-LU", "2018-10-01", None)],
}
EARLIEST_YEAR = 2015


def _energy_charts_year(bzn: str, year: int, tries: int = 4) -> pd.DataFrame | None:
    """One calendar year of day-ahead prices for a bidding zone, or None if the API has no data for it."""
    for attempt in range(tries):
        try:
            r = _get(
                "https://api.energy-charts.info/price", {"bzn": bzn, "start": f"{year}-01-01", "end": f"{year}-12-31"}
            )
            j = r.json()
        except (ValueError, requests.HTTPError):  # empty or non-JSON reply: wait and retry, then give up on this year
            time.sleep(20 * (attempt + 1))
            continue
        if not j.get("unix_seconds"):
            return None
        return pd.DataFrame(
            {"timestamp": pd.to_datetime(j["unix_seconds"], unit="s", utc=True), "price_eur_mwh": j["price"]}
        )
    return None


def power_energy_charts(start_year: int = 2019, market: str = "NL") -> pd.DataFrame:
    """Day-ahead prices for a market (NL, BE, DE-LU) from Energy-Charts, one request per year and zone."""
    frames = []
    for bzn, frm, until in ZONES[market]:
        for y in range(start_year, dt.date.today().year + 1):
            if (frm and y < int(frm[:4])) or (until and y > int(until[:4])):
                continue
            df = _energy_charts_year(bzn, y)
            time.sleep(15)  # the API rate-limits bursts
            if df is None:
                print(f"   {market} ({bzn}) {y}: no data")
                continue
            if frm:
                df = df[df.timestamp >= pd.Timestamp(frm, tz="Europe/Berlin")]
            if until:
                df = df[df.timestamp < pd.Timestamp(until, tz="Europe/Berlin") + pd.Timedelta(days=1)]
            print(f"   {market} ({bzn}) {y}: {len(df)} rows")
            frames.append(df)
    return pd.concat(frames).dropna().drop_duplicates("timestamp").sort_values("timestamp")


def daily(p: pd.DataFrame) -> pd.Series:
    """Calendar-day average (Europe/Amsterdam; NL, BE and DE share the CET time zone)."""
    return (
        p.set_index("timestamp")
        .price_eur_mwh.tz_convert("Europe/Amsterdam")
        .resample("D")
        .mean()
        .dropna()
        .rename("price_eur_mwh")
    )


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


def gas_yahoo(start: str = "2019-01-01") -> pd.DataFrame:
    """Daily TTF front-month prices from Yahoo Finance (TTF=F; available from 23 Oct 2017)."""
    import yfinance as yf

    g = yf.download("TTF=F", start=start, progress=False, auto_adjust=False)
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
    ap.add_argument(
        "--extended",
        action="store_true",
        help="download NL, DE-LU and BE power and TTF gas as far back as the free sources allow into data/extended/ "
        "(the default files in data/ are left untouched, so the headline results don't move)",
    )
    args = ap.parse_args()
    DATA.mkdir(exist_ok=True)
    if args.extended:
        ext = DATA / "extended"
        ext.mkdir(exist_ok=True)
        for market in ZONES:
            print(f"Downloading {market} day-ahead power from {EARLIEST_YEAR} ...")
            d = daily(power_energy_charts(EARLIEST_YEAR, market))
            f = ext / f"power_{market.lower().replace('-', '_')}_daily.csv"
            d.to_csv(f)
            print(
                f"   saved {len(d)} days ({d.index.min().date()} .. {d.index.max().date()}) "
                f"-> {f.relative_to(DATA.parent)}"
            )
        g = gas_yahoo(start=f"{EARLIEST_YEAR}-01-01")
        g.to_csv(ext / "gas_ttf.csv")
        print(f"   gas: {len(g)} days ({g.index.min().date()} .. {g.index.max().date()}) -> data/extended/gas_ttf.csv")
        return

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
        d = daily(p)
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
