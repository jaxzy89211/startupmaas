"""Procurement audit: "what would HedgeWise have saved you?"

The company enters what it actually bought, per delivery year and commodity: one row per contract, each a share of
that year's volume at a fixed price (EUR/MWh, ct/kWh, EUR/kWh, EUR/GJ, or EUR/m3 for gas), optionally with the date
it was fixed. A row with price "variable" says that the year/commodity was (partly) bought at the variable price.
Whatever is not fixed is paid at the realised monthly average day-ahead (power) / TTF front-month (gas) price.

Actual commodity cost of a year, per commodity c (v_m = monthly volume in MWh from the consumption profile,
V = sum of v_m, p_m = realised monthly average price in EUR/MWh, k = the fixed-price contracts of that year):

    cost_c = sum_k share_k * price_k * V  +  (1 - sum_k share_k) * sum_m v_m * p_m

A fixed share covers that share of every month (a fixed-volume-share contract), the rest floats. This is the same
convention as backtest.py, so the comparison is like for like with: all variable, fix 100% in October, 60% in
monthly tranches, the 2-year ladder (40%+40%) and the HedgeWise ladder. The strategies are run exactly as in
backtest.py (from 2021, so the HedgeWise ladder carries the same state), on real historical prices only.
Years can be partial (e.g. only 2022-2024); a year must be complete in the price data.

Commodity cost only: enter contract prices WITHOUT energy tax, network tariffs and VAT. The same monthly
consumption profile is used for every audited year.

    python audit.py data/audit_example.csv [--company bills.csv] [--lang nl]
"""

from __future__ import annotations

import argparse
import dataclasses
import re

import numpy as np
import pandas as pd

import hedgewise as hw
from hedgewise import backtest as bt
from hedgewise import company_input
from hedgewise._types import PathOrBuffer
from hedgewise.provenance import one_line, write_csv
from hedgewise.settings import BACKTEST, MWH_PER_GJ, MWH_PER_M3

OURS = "Your contracts"
COMPARE = {  # backtest.py column -> label in the audit
    "All variable": "All variable",
    f"Fix 100% in {hw.MONTHS[BACKTEST['typical_month'] - 1]}": "Fix 100% in October",
    "Tranches 6x (60%)": "60% in monthly tranches",
    "Layered 2 years (40+40%)": "2-year ladder (40%+40%)",
    bt.LADDER_NAME: "HedgeWise ladder",
}
LADDER_2Y = "2-year ladder (40%+40%)"
LABELS_NL = {
    OURS: "Uw contracten",
    "All variable": "Alles variabel",
    "Fix 100% in October": "100% vastzetten in oktober",
    "60% in monthly tranches": "60% in maandelijkse tranches",
    LADDER_2Y: "2-jaarsladder (40%+40%)",
    "HedgeWise ladder": "HedgeWise-ladder",
}
COMMODITIES = ("gas", "power")
PRICE_UNITS = ["EUR/MWh", "ct/kWh", "EUR/kWh", "EUR/GJ", "EUR/m3"]
VARIABLE_WORDS = ("", "nan", "none", "variable", "variabel", "var", "-")
COMMODITY_WORDS = {
    "gas": "gas",
    "aardgas": "gas",
    "power": "power",
    "electricity": "power",
    "elektriciteit": "power",
    "stroom": "power",
}
FIRST_YEAR = BACKTEST["years"][0]  # the price data (from 2019) supports the 2-year ladder from this delivery year


def to_eur_per_mwh(price: float, unit: str, commodity: str) -> float:
    """Convert a contract price to EUR/MWh. 1 ct/kWh = 10 EUR/MWh; 1 EUR/kWh = 1,000 EUR/MWh;
    EUR/GJ / 0.2778; EUR/m3 (gas only) / 0.009769 (Dutch gas, 35.17 MJ/m3; config.yaml units)."""
    u = str(unit).strip().lower().replace(" ", "").replace("€", "eur").replace("³", "3").replace("euro", "eur")
    if u in ("eur/mwh", "eurmwh"):
        return float(price)
    if u in ("ct/kwh", "cent/kwh", "eurct/kwh", "ct"):
        return float(price) * 10.0
    if u in ("eur/kwh",):
        return float(price) * 1000.0
    if u in ("eur/gj",):
        return float(price) / MWH_PER_GJ
    if u in ("ct/m3", "cent/m3", "eurct/m3"):
        if commodity != "gas":
            raise ValueError("ct/m3 only applies to gas")
        return float(price) / 100 / MWH_PER_M3
    if u in ("eur/m3", "eur/nm3"):
        if commodity != "gas":
            raise ValueError("EUR/m3 only applies to gas")
        return float(price) / MWH_PER_M3
    raise ValueError(f"unknown price unit {unit!r}; use one of {PRICE_UNITS}")


@dataclasses.dataclass
class Contract:
    """One purchase: `share` (0-1) of a delivery year's volume of `commodity` at a fixed price in EUR/MWh.
    price_eur_mwh None = a 'variable' row (it only marks the year/commodity as audited)."""

    year: int
    commodity: str
    share: float
    price_eur_mwh: float | None
    fixed_on: pd.Timestamp | None = None
    price_entered: str = ""  # the price as entered, with its unit (for the results table)


def _blank(v: object) -> bool:
    return v is None or (isinstance(v, float) and np.isnan(v)) or str(v).strip().lower() in VARIABLE_WORDS


def parse_date(v: object) -> pd.Timestamp:
    """ISO dates (2022-03-10) as such; anything else day-first, the Dutch way (10-03-2022, 10/3/2022)."""
    txt = str(v).strip()
    if re.match(r"^\d{4}-\d{1,2}-\d{1,2}", txt):
        return pd.Timestamp(txt[:10])
    return pd.Timestamp(pd.to_datetime(txt, dayfirst=True))


def read_contracts(src: PathOrBuffer | pd.DataFrame) -> list[Contract]:
    """Contracts table: year, commodity (gas/power), share (0-1 or %), price, unit, fixed_on (optional).

    Dutch column names (jaar, product, aandeel, prijs, eenheid, datum) and Dutch numbers (0,45) are accepted."""
    df = src.copy() if isinstance(src, pd.DataFrame) else company_input.read_table(src)
    df = df.dropna(how="all")
    cols = {str(c).lower().strip(): c for c in df.columns}

    def col(*names: str) -> str | None:
        return next((cols[n] for n in names if n in cols), None)

    cy, cc, cs = col("year", "jaar"), col("commodity", "product"), col("share", "aandeel")
    cp, cu, cd = col("price", "prijs"), col("unit", "eenheid"), col("fixed_on", "date", "datum")
    if not (cy and cc and cp):
        raise ValueError(f"need columns year, commodity, share, price (and unit); got {list(df.columns)}")
    out = []
    for i, row in enumerate(df.to_dict("records"), start=1):
        if _blank(row[cy]):
            continue
        year = int(float(str(row[cy]).strip()))
        commodity = COMMODITY_WORDS.get(str(row[cc]).strip().lower())
        if commodity is None:
            raise ValueError(f"row {i}: commodity must be gas or power, got {row[cc]!r}")
        if _blank(row[cp]):  # a variable row
            out.append(Contract(year, commodity, 0.0, None, price_entered="variable"))
            continue
        share_raw = row[cs] if cs else None
        if _blank(share_raw):
            raise ValueError(f"row {i}: a fixed-price row needs a share (e.g. 60% or 0.6)")
        share = float(company_input.parse_numbers([str(share_raw).replace("%", "")])[0])
        share = share / 100 if (share > 1 or "%" in str(share_raw)) else share
        if not 0 < share <= 1:
            raise ValueError(f"row {i}: share must be between 0 and 100%, got {share_raw!r}")
        price = float(company_input.parse_numbers([row[cp]])[0])
        if not np.isfinite(price) or price <= 0:
            raise ValueError(f"row {i}: price must be a positive number or 'variable', got {row[cp]!r}")
        unit = str(row[cu]).strip() if cu and not _blank(row[cu]) else "EUR/MWh"
        fixed_on = None
        if cd and not _blank(row[cd]):
            fixed_on = parse_date(row[cd])
            if fixed_on > pd.Timestamp(year, 12, 31):
                raise ValueError(f"row {i}: fixed on {fixed_on.date()}, after the end of delivery year {year}")
        p = to_eur_per_mwh(price, unit, commodity)
        out.append(Contract(year, commodity, share, p, fixed_on, f"{price:g} {unit}"))
    if not out:
        raise ValueError("no contracts entered")
    for y in sorted({k.year for k in out}):
        for c in COMMODITIES:
            tot = sum(k.share for k in out if k.year == y and k.commodity == c)
            if tot > 1 + 1e-9:
                raise ValueError(f"{y} {c}: fixed shares add up to {tot:.0%} (more than 100%)")
    return out


def audited_years(contracts: list[Contract], df: pd.DataFrame) -> list[int]:
    """The years in the contracts, checked against the price data (complete years from FIRST_YEAR)."""
    years = sorted({k.year for k in contracts})
    last_complete = df.index.max().year - (0 if df.index.max() >= pd.Timestamp(df.index.max().year, 12, 31) else 1)
    bad = [y for y in years if y < FIRST_YEAR or y > last_complete]
    if bad:
        raise ValueError(
            f"years {bad} can't be audited: the price data supports complete delivery years "
            f"{FIRST_YEAR}-{last_complete}"
        )
    return years


def actual_cost(df: pd.DataFrame, company: hw.Company, contracts: list[Contract], year: int) -> dict[str, dict]:
    """The company's actual cost in `year` per commodity (formula in the module docstring), with the inputs."""
    real = bt.realised_monthly(df, year)
    vol = {"gas": company.gas_mwh, "power": company.power_mwh}
    out = {}
    for c in COMMODITIES:
        ks = [k for k in contracts if k.year == year and k.commodity == c and k.price_eur_mwh is not None]
        share = sum(k.share for k in ks)
        fixed = sum(k.share * k.price_eur_mwh for k in ks) * vol[c].sum()
        variable = (1 - share) * float(vol[c] @ real[c])
        out[c] = {
            "volume_mwh": float(vol[c].sum()),
            "fixed_share": share,
            "avg_fixed_price": sum(k.share * k.price_eur_mwh for k in ks) / share if share else None,
            "realised_avg_price": float(vol[c] @ real[c] / vol[c].sum()) if vol[c].sum() else None,
            "fixed_cost": fixed,
            "variable_cost": variable,
            "cost": fixed + variable,
        }
    return out


def strategy_costs(
    company: hw.Company, last_year: int, df: pd.DataFrame | None = None, n_paths: int | None = None, seed: int = 42
) -> pd.DataFrame:
    """The comparison strategies' realised bill per delivery year FIRST_YEAR..last_year (EUR), run exactly as in
    backtest.py (same seed and scenario count by default, so for the default company the numbers equal
    output/backtest_results.csv). Independent of the contracts, so the app caches it per company."""
    df = hw.load_prices() if df is None else df
    rows, prev = {}, None
    for y in range(FIRST_YEAR, last_year + 1):
        res, info = bt.run_year(
            df,
            company,
            y,
            [BACKTEST["policy_gamma"]],
            n_paths or BACKTEST["n_paths"],
            seed,
            BACKTEST["typical_month"],
            verbose=False,
            prev_ladder_target=prev,
        )
        prev = info["ladder_final_target"]
        rows[y] = {label: res[key] for key, label in COMPARE.items()}
    return pd.DataFrame(rows).T.rename_axis("year")


@dataclasses.dataclass
class AuditResult:
    """table: EUR per audited year, column OURS + the strategies. detail: the actual cost broken down."""

    table: pd.DataFrame
    detail: pd.DataFrame
    contracts: pd.DataFrame

    @property
    def years(self) -> list[int]:
        """Audited years."""
        return [int(y) for y in self.table.index]


def market_price_on(df: pd.DataFrame, c: str, when: pd.Timestamp, year: int) -> float | None:
    """The backtest's fixed-price proxy on `when` for delivery year `year` (None if outside the data)."""
    if when < df.index.min() + pd.Timedelta(days=30) or when > df.index.max():
        return None
    return bt._fp_safe(df, c, when, pd.Timestamp(year, 1, 1))


def run_audit(
    contracts: list[Contract],
    company: hw.Company,
    df: pd.DataFrame | None = None,
    strategies: pd.DataFrame | None = None,
    n_paths: int | None = None,
) -> AuditResult:
    """Actual cost per audited year next to the comparison strategies (backtest rules, real prices only)."""
    df = hw.load_prices() if df is None else df
    years = audited_years(contracts, df)
    if strategies is None or not set(years) <= set(strategies.index):
        strategies = strategy_costs(company, max(years), df, n_paths)
    rows, det = {}, []
    for y in years:
        act = actual_cost(df, company, contracts, y)
        rows[y] = {OURS: act["gas"]["cost"] + act["power"]["cost"], **strategies.loc[y].to_dict()}
        det += [{"year": y, "commodity": c, **act[c]} for c in COMMODITIES]
    ct = pd.DataFrame(
        [
            {
                "year": k.year,
                "commodity": k.commodity,
                "share": k.share,
                "price_entered": k.price_entered,
                "price_eur_mwh": k.price_eur_mwh,
                "fixed_on": None if k.fixed_on is None else k.fixed_on.date(),
                "market_proxy_that_day": None
                if k.fixed_on is None
                else market_price_on(df, k.commodity, k.fixed_on, k.year),
            }
            for k in contracts
        ]
    )
    return AuditResult(pd.DataFrame(rows).T.rename_axis("year"), pd.DataFrame(det), ct)


# --------------------------------------------------------------------------------------------
# Text and chart
# --------------------------------------------------------------------------------------------
def money(x: float, lang: str = "en") -> str:
    """EUR: millions with 2 decimals from EUR 1m ('€1.23m' / '€ 1,23 mln'), else thousands ('€74k' / '€ 74.000')."""
    from hedgewise.moneyfmt import money_text

    return money_text(x, lang)


def label(name: str, lang: str) -> str:
    """Column label in the chosen language."""
    return LABELS_NL.get(name, name) if lang == "nl" else name


def sentence(r: AuditResult, lang: str = "en") -> str:
    """The summary sentence, plus which strategies the company beat. Says so plainly when the company did better:
    results are never hidden."""
    t = r.table
    you, lad = t[OURS], t[LADDER_2Y]
    wy = int(you.idxmax())
    diff = lad[wy] - you[wy]  # > 0: the ladder would have been higher in your worst year
    span = f"{r.years[0]}-{r.years[-1]}" if len(r.years) > 1 else str(r.years[0])
    beat = [c for c in t.columns if c != OURS and you.sum() < t[c].sum()]
    if lang == "nl":
        s = (
            f"U betaalde {money(you.sum(), 'nl')} over {span}; de 2-jaarsladder had {money(lad.sum(), 'nl')} "
            f"gekost. Uw duurste jaar ({wy}, {money(you[wy], 'nl')}) was met de 2-jaarsladder "
            f"{money(abs(diff), 'nl')} {'hoger' if diff > 0 else 'lager'} geweest."
        )
        if beat:
            s += " In totaal deed u het beter dan: " + ", ".join(label(c, "nl") for c in beat) + "."
        return s
    s = (
        f"You paid {money(you.sum())} over {span}; the 2-year ladder would have cost {money(lad.sum())}. "
        f"Your worst year ({wy}, {money(you[wy])}) would have been {money(abs(diff))} "
        f"{'higher' if diff > 0 else 'lower'} with the 2-year ladder."
    )
    if beat:
        s += " In total you did better than: " + ", ".join(beat) + "."
    return s


COLORS = ["#111827", "#b8433a", "#6b7280", "#93c5fd", "#1b9e5a", "#0e7490"]


def plot_audit(r: AuditResult, path: PathOrBuffer, lang: str = "en") -> None:
    """Bars per year (your contracts vs the strategies) and the totals."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from hedgewise.moneyfmt import money_axis, money_unit
    from hedgewise.provenance import save_figure

    t = r.table  # raw euros; money_axis sets the unit
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(11.5, 4.6), gridspec_kw={"width_ratios": [2.3, 1]})
    x = np.arange(len(t))
    w = 0.8 / len(t.columns)
    for i, c in enumerate(t.columns):
        ax.bar(x + (i - (len(t.columns) - 1) / 2) * w, t[c], w * 0.92, color=COLORS[i % 6], label=label(c, lang))
    ax.set_xticks(x, [str(y) for y in t.index])
    money_axis(ax, t.values.ravel(), lang, "y")
    ax.set_title("Per year" if lang == "en" else "Per jaar", fontsize=10)
    ax.grid(axis="y", alpha=0.25)
    ax.spines[["top", "right"]].set_visible(False)
    tot = t.sum()
    ax2.barh(np.arange(len(tot)), tot.values, color=COLORS[: len(tot)])
    ax2.set_yticks(np.arange(len(tot)), [label(c, lang) for c in tot.index], fontsize=8)
    ax2.invert_yaxis()
    tunit = money_unit(tot.values, lang)
    for i, v in enumerate(tot.values):
        ax2.text(v, i, " " + tunit.fmt(v), va="center", fontsize=8)
    ax2.set_xlim(0, tot.max() * 1.25)
    money_axis(ax2, tot.values, lang, "x")
    span = f"{t.index[0]}-{t.index[-1]}" if len(t) > 1 else str(t.index[0])
    ax2.set_title(("Total " if lang == "en" else "Totaal ") + span, fontsize=10)
    ax2.spines[["top", "right"]].set_visible(False)
    fig.suptitle(
        "Your contracts vs alternative strategies (realised prices, commodity cost only)"
        if lang == "en"
        else "Uw contracten vs andere strategieën (gerealiseerde prijzen, alleen energiekosten)",
        fontsize=11,
    )
    fig.legend(*ax.get_legend_handles_labels(), fontsize=8, frameon=False, ncol=6, loc="lower center")
    fig.tight_layout(rect=(0, 0.07, 1, 1))
    save_figure(fig, path, dpi=140, format="png")
    plt.close(fig)


def main(argv: list[str] | None = None) -> AuditResult:
    """Command line: python audit.py contracts.csv [company options] [--lang nl]."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("contracts", help="CSV/Excel: year, commodity, share, price, unit, fixed_on")
    company_input.add_cli_args(ap)
    ap.add_argument("--lang", default="en", choices=["en", "nl"])
    args = ap.parse_args(argv)
    company = company_input.company_from_args(args)
    r = run_audit(read_contracts(args.contracts), company)
    show = (r.table / 1e3).round(0).astype(int)
    show.loc["TOTAL"] = (r.table.sum() / 1e3).round(0).astype(int)
    show.loc["WORST YEAR"] = (r.table.max() / 1e3).round(0).astype(int)
    pd.set_option("display.width", 200)
    print("EUR k, commodity cost only\n" + show.to_string())
    d = r.detail.copy()
    print("\nYour cost, broken down (fixed share x avg fixed price x volume + rest x realised prices):")
    print(d.round(2).to_string(index=False))
    if r.contracts.fixed_on.notna().any():
        print("\nYour fixed prices vs the market proxy (30-day spot + 5%) on the day you fixed (EUR/MWh):")
        print(r.contracts.dropna(subset=["fixed_on"]).round(1).to_string(index=False))
    print("\n" + sentence(r, args.lang))
    hw.OUT.mkdir(exist_ok=True)
    write_csv(r.table, hw.OUT / "audit_table.csv")
    write_csv(r.detail, hw.OUT / "audit_detail.csv", index=False)
    plot_audit(r, hw.OUT / f"audit_{args.lang}.png", args.lang)
    print(f"Wrote {hw.OUT}/audit_table.csv, audit_detail.csv, audit_{args.lang}.png")
    print("Provenance: " + one_line())
    return r


__all__ = [
    "Contract",
    "AuditResult",
    "read_contracts",
    "to_eur_per_mwh",
    "actual_cost",
    "strategy_costs",
    "run_audit",
    "sentence",
    "plot_audit",
    "main",
]
