"""Green investments as a hedge: rooftop solar, a battery and a PPA, on top of the HedgeWise plan.

Hourly prices. The monthly price model stays as it is. Inside each simulated month, hourly prices are
    price(day d, hour h) = simulated monthly level x shape_m(d, h)
where shape_m is one real day of that calendar month (price per hour / that month's average price), drawn from the
last `lookback_months` of data/power_nl_hourly.csv (local time; the 15-minute data since Oct 2025 is averaged per
hour). `simulate_hourly()` draws such hourly series. The bill effects below use the exact expectation over the real
days of each month instead of a random draw (same answer, no sampling noise).

Why this works on the existing scenarios: for a given month, every cash flow of an investment is (volume per hour) x
(hourly price), and the hourly price is the monthly level times a shape. So each investment changes the bill by
    sum_m  level_m x delta_m  +  fixed yearly amount
where delta_m is the change in "MWh at the monthly average price" (negative = less to buy), and the fixed amount is
the yearly capital cost (annuity) + running cost - peak-shaving saving, or the PPA's fixed payments. That holds for any
hedge ratio, because the hedged volume is bought in advance either way and any surplus or shortfall settles at spot.

    solar   : self-consumed production replaces purchases at the hourly price; exports earn
              export_value_share x the hourly price (load and production both follow typical-day profiles)
    battery : one cycle a day: charge in the cheapest hours, discharge in the dearest (round-trip efficiency 88%),
              discharging into the company's own load first and exporting the rest; plus a fixed peak-shaving
              saving on the grid capacity charge (network cost, an assumption)
    PPA     : a share of the power volume at a fixed price, shaped like the load: a long-dated fixed-price hedge

All assumptions are in config.yaml (section `green`) with a source or "ASSUMPTION" comment. Simplifications: one
typical production day per month (no cloudy/sunny days), no degradation, solar and battery effects are added
(their interaction is ignored), the battery does not see the solar profile, and the PPA has no shape or
imbalance cost.
"""

from __future__ import annotations

import dataclasses
import math
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from .company import Company
from .settings import DATA, GREEN, KWH_PER_MWH, PERCENT, SOLAR_SHARE, SOLAR_YIELD_KWH_PER_KWP

HOURS = 24
_G = GREEN


# --------------------------------------------------------------------------------------------
# Hourly price shapes
# --------------------------------------------------------------------------------------------
def load_hourly(path: Path = DATA / "power_nl_hourly.csv") -> pd.Series:
    """Hourly day-ahead price in local time (Europe/Amsterdam, tz-naive); 15-minute data averaged per hour."""
    raw = pd.read_csv(path)
    ts = pd.to_datetime(raw["timestamp"], utc=True).dt.tz_convert("Europe/Amsterdam").dt.tz_localize(None)
    s = pd.Series(raw["price_eur_mwh"].values, index=ts).dropna()
    return s.groupby(s.index.floor("h")).mean()


def day_shapes(
    hourly: pd.Series, lookback_months: int = _G["hourly_shape"]["lookback_months"], as_of: object = None
) -> dict:
    """{calendar month 1-12: array (n_days, 24)} of hourly price / that month's average price, from the last
    `lookback_months` complete months up to `as_of` (default: all data). No data after `as_of` is used, so a
    historical decision date never sees later price shapes. DST days: the missing hour is interpolated, the
    doubled hour averaged."""
    if as_of is not None:
        hourly = hourly[hourly.index < pd.Timestamp(as_of).normalize() + pd.Timedelta(days=1)]
    end = (hourly.index.max() + pd.Timedelta(hours=1)).to_period("M") - 1  # last complete month
    start = end - lookback_months + 1
    h = hourly[(hourly.index.to_period("M") >= start) & (hourly.index.to_period("M") <= end)]
    month_mean = h.groupby(h.index.to_period("M")).transform("mean")
    r = (h / month_mean).to_frame("r")
    r["day"], r["hour"] = r.index.normalize(), r.index.hour
    grid = r.pivot_table(index="day", columns="hour", values="r", aggfunc="mean").reindex(columns=range(HOURS))
    grid = grid.interpolate(axis=1, limit_direction="both")
    out = {m: grid[grid.index.month == m].to_numpy() for m in range(1, 13)}
    missing = [m for m, v in out.items() if not len(v)]
    if missing:
        raise ValueError(f"not enough hourly data before {as_of} for calendar months {missing}")
    return out


@lru_cache(maxsize=1)
def _hourly() -> pd.Series:
    return load_hourly()


@lru_cache(maxsize=16)
def default_shapes(as_of: str | None = None) -> dict:
    """Day shapes from data/power_nl_hourly.csv up to `as_of` (cached per date)."""
    return day_shapes(_hourly(), as_of=as_of)


def simulate_hourly(level: float, month: int, n_days: int, seed: int = 0, shapes: dict | None = None) -> np.ndarray:
    """(n_days, 24) hourly prices for one simulated month: random real days of that calendar month x level."""
    sh = (shapes or default_shapes())[month]
    rng = np.random.default_rng(seed)
    return level * sh[rng.integers(0, len(sh), n_days)]


# --------------------------------------------------------------------------------------------
# Typical-day volume profiles (MWh per hour)
# --------------------------------------------------------------------------------------------
def _days(month: int, year: int = 2027) -> int:  # year: the delivery year (leap years)
    return pd.Period(f"{year}-{month:02d}").days_in_month


def load_profile(company: Company, month: int, year: int = 2027) -> np.ndarray:
    """The company's power use on a typical day of `month`, MWh per hour (config: green.load_profile)."""
    day = float(np.asarray(company.power_kwh, float)[month - 1]) / KWH_PER_MWH / _days(month, year)
    if _G["load_profile"] == "daytime":
        w = np.where(
            (np.arange(HOURS) >= 7) & (np.arange(HOURS) < 19), _G["daytime_share"] / 12, (1 - _G["daytime_share"]) / 12
        )
    else:
        w = np.full(HOURS, 1 / HOURS)
    return day * w


def solar_profile(kwp: float, month: int, year: int = 2027) -> np.ndarray:
    """Rooftop-PV production on a typical day of `month`, MWh per hour: the month's yield (config solar.*) spread
    as a sine over the production window (config green.solar.sun_hours_cet)."""
    month_mwh = kwp * SOLAR_YIELD_KWH_PER_KWP / KWH_PER_MWH * SOLAR_SHARE[month - 1] / SOLAR_SHARE.sum()
    a, b = _G["solar"]["sun_hours_cet"][month - 1]
    mid = np.arange(HOURS) + 0.5
    w = np.where((mid > a) & (mid < b), np.sin(np.pi * (mid - a) / (b - a)), 0.0)
    return month_mwh / _days(month, year) * w / w.sum()


def annuity(capex: float, years: int, rate: float = _G["annuity_rate"]) -> float:
    """Yearly capital cost of `capex` over `years` at `rate`."""
    return capex / years if rate == 0 else capex * rate / (1 - (1 + rate) ** -years)


# --------------------------------------------------------------------------------------------
# Investments -> delta_m (MWh at the monthly average price) and a fixed yearly amount
# --------------------------------------------------------------------------------------------
@dataclasses.dataclass
class Investments:
    """What the company considers. Zero = not selected. battery_kw defaults to half the capacity (2-hour battery);
    ppa_price None = today's fixed power price."""

    solar_kwp: float = 0.0
    battery_kwh: float = 0.0
    battery_kw: float | None = None
    ppa_share: float = 0.0
    ppa_price: float | None = None
    ppa_years: int = 10

    def any(self) -> bool:
        """True if at least one investment is selected."""
        return bool(self.solar_kwp or self.battery_kwh or self.ppa_share)


@dataclasses.dataclass
class Effect:
    """One investment's effect on next year's power bill: bill change = delta_mwh_eq @ monthly price + fixed_eur."""

    key: str
    label: dict  # {"en": ..., "nl": ...}
    delta_mwh_eq: np.ndarray  # (12,) Jan..Dec
    fixed_eur: float  # yearly: annuity + opex - peak shaving, or PPA payments
    capex: float
    opex: float
    details: dict


def solar_effect(company: Company, kwp: float, shapes: dict, year: int = 2027) -> Effect:
    """Self-consumption and export of `kwp` of rooftop PV."""
    c = _G["solar"]
    delta, self_mwh, export_mwh = np.zeros(12), 0.0, 0.0
    for m in range(1, 13):
        r = shapes[m].mean(axis=0)  # expected price shape of a day in this month
        pv, load = solar_profile(kwp, m, year), load_profile(company, m, year)
        own = np.minimum(pv, load)
        exp = pv - own
        d = _days(m, year)
        delta[m - 1] = -d * (own @ r + c["export_value_share"] * (exp @ r))
        self_mwh, export_mwh = self_mwh + d * own.sum(), export_mwh + d * exp.sum()
    capex = kwp * c["capex_eur_per_kwp"]
    opex = capex * c["opex_pct_of_capex"] / PERCENT
    mwp = kwp / 1000
    return Effect(
        "solar",
        {"en": f"+ solar {mwp:g} MWp", "nl": f"+ zon {mwp:g} MWp".replace(".", ",")},
        delta,
        annuity(capex, c["lifetime_years"]) + opex,
        capex,
        opex,
        {"production_mwh": self_mwh + export_mwh, "self_consumed_mwh": self_mwh, "exported_mwh": export_mwh},
    )


def battery_day_value(r: np.ndarray, load: np.ndarray, kwh: float, kw: float) -> np.ndarray:
    """Arbitrage value of one cycle per day, in 'MWh at the monthly average price', for each day shape in r
    (n_days, 24): charge kwh in the cheapest hours, discharge kwh x efficiency in the dearest of the other hours;
    discharge first replaces own load (full price), the rest is exported (export_value_share). Days where the
    cycle would lose money are skipped (value 0)."""
    b = _G["battery"]
    e_mwh, p_mw = kwh / KWH_PER_MWH, kw / KWH_PER_MWH
    n = max(1, math.ceil(e_mwh / p_mw - 1e-9))
    charge_per_h = e_mwh / n
    out_per_h = e_mwh * b["round_trip_efficiency"] / n
    to_load = np.minimum(out_per_h, load)  # per hour; load is (24,)
    to_grid = out_per_h - to_load
    order = np.argsort(r, axis=1)
    cheap, dear = order[:, :n], order[:, ::-1][:, :n]
    rows = np.arange(len(r))[:, None]
    cost = charge_per_h * r[rows, cheap].sum(axis=1)
    gain = (to_load[dear] + _G["solar"]["export_value_share"] * to_grid[dear]) * r[rows, dear]
    return np.maximum(gain.sum(axis=1) - cost, 0.0)


def battery_effect(company: Company, kwh: float, kw: float | None, shapes: dict, year: int = 2027) -> Effect:
    """Daily arbitrage (price-dependent) + peak shaving (fixed) of a battery."""
    b = _G["battery"]
    kw = kw or kwh / 2
    delta, cycles = np.zeros(12), 0
    for m in range(1, 13):
        v = battery_day_value(shapes[m], load_profile(company, m, year), kwh, kw)
        delta[m - 1] = -_days(m, year) * b["cycles_per_day"] * v.mean()
        cycles += _days(m, year) * (v > 0).mean()
    capex = kwh * b["capex_eur_per_kwh"]
    opex = capex * b["opex_pct_of_capex"] / PERCENT
    peak = kw * b["peak_tariff_eur_per_kw_year"] * b["peak_shaving_share"]
    mwh = kwh / 1000
    return Effect(
        "battery",
        {"en": f"+ battery {mwh:g} MWh", "nl": f"+ batterij {mwh:g} MWh".replace(".", ",")},
        delta,
        annuity(capex, b["lifetime_years"]) + opex - peak,
        capex,
        opex,
        {"power_kw": kw, "cycles_per_year": cycles, "peak_shaving_eur": peak},
    )


def ppa_effect(company: Company, share: float, price: float, years: int, shapes: dict, year: int = 2027) -> Effect:
    """A PPA for `share` of the yearly power volume at `price`, shaped like the load."""
    delta = np.zeros(12)
    for m in range(1, 13):
        delta[m - 1] = -share * _days(m, year) * (load_profile(company, m, year) @ shapes[m].mean(axis=0))
    vol = share * float(np.asarray(company.power_kwh, float).sum()) / KWH_PER_MWH
    return Effect(
        "ppa",
        {"en": f"+ PPA {share:.0%}", "nl": f"+ PPA {share:.0%}"},
        delta,
        vol * price,
        0.0,
        0.0,
        {"volume_mwh": vol, "price_eur_mwh": price, "years": years},
    )


def effects(
    company: Company, inv: Investments, power_fixed_price: float, shapes: dict | None = None, year: int = 2027
) -> list[Effect]:
    """The selected investments' effects (one each)."""
    if company.solar_kwp:
        raise ValueError("Company.solar_kwp is the simple solar model; set it to 0 and use Investments.solar_kwp")
    sh = shapes or default_shapes()
    out = []
    if inv.solar_kwp:
        out.append(solar_effect(company, inv.solar_kwp, sh, year))
    if inv.battery_kwh:
        out.append(battery_effect(company, inv.battery_kwh, inv.battery_kw, sh, year))
    if inv.ppa_share:
        price = power_fixed_price if inv.ppa_price is None else inv.ppa_price
        out.append(ppa_effect(company, inv.ppa_share, price, inv.ppa_years, sh, year))
    return out


# --------------------------------------------------------------------------------------------
# Evaluation on the scenarios of an Analysis
# --------------------------------------------------------------------------------------------
def plan_key(a: object) -> str:
    """The recommended plan's column in a.costs: the last one (the ladder, model-only or budget plan)."""
    return list(a.costs)[-1]


def bill_change(a: object, e: Effect) -> np.ndarray:
    """Per scenario: change in next year's bill (EUR) from investment e, on the analysis' simulated prices."""
    order = [(a.delivery_start + pd.DateOffset(months=i)).month - 1 for i in range(12)]
    return e.delta_mwh_eq[order] @ a.spot_month["power"] + e.fixed_eur


def evaluate(
    a: object, inv: Investments, shapes: dict | None = None, df: pd.DataFrame | None = None
) -> tuple[pd.DataFrame, dict]:
    """Table per investment (and all selected together) on top of the HedgeWise plan, plus the cost arrays.

    expected / es95: the plan's bill with the investment; cut_*: how much lower than the plan alone.
    saving_normal: expected yearly saving before capital cost (energy + peak shaving - opex).
    saving_crisis: the same, averaged over the plan's worst 5% scenarios.
    payback_*: capex / saving (years); None when there is no capex or no saving."""
    base = a.costs[plan_key(a)]
    q = np.quantile(base, 0.95)
    bad = base >= q
    es0 = float(base[bad].mean())
    # price shapes only from data up to the decision date (no look-ahead); day counts of the delivery year
    sh = shapes or default_shapes(str(a.model.as_of.date()))
    effs = effects(a.company, inv, a.fwd_now["power"], sh, a.delivery_start.year)
    rows, arrays = [], {}
    combos = [(e.label, [e]) for e in effs]
    if len(effs) > 1:
        combos.append(({k: " ".join(e.label[k] for e in effs) for k in ("en", "nl")}, effs))
    for label, group in combos:
        d = sum(bill_change(a, e) for e in group)
        cost = base + d
        var = np.quantile(cost, 0.95)
        capex = sum(e.capex for e in group)
        annual_capital = sum(annuity(e.capex, _life(e)) for e in group if e.capex)
        s_norm, s_crisis = _savings(a, group, bad)
        # payback only from the investments that cost capital (a PPA's crisis saving doesn't pay for solar panels)
        p_norm, p_crisis = _savings(a, [e for e in group if e.capex], bad)
        rows.append(
            {
                "investment": label["en"],
                "label_nl": label["nl"],
                "expected": float(cost.mean()),
                "es95": float(cost[cost >= var].mean()),
                **_honest(a, df, group, float(cost[cost >= var].mean())),
                "cut_expected": float(base.mean() - cost.mean()),
                "cut_es95": float(es0 - cost[cost >= var].mean()),
                "capex": capex,
                "annual_capital_cost": annual_capital,
                "saving_normal": s_norm,
                "saving_crisis": s_crisis,
                "payback_normal": capex / p_norm if capex and p_norm > 0 else None,
                "payback_crisis": capex / p_crisis if capex and p_crisis > 0 else None,
            }
        )
        arrays[label["en"]] = cost
    out = pd.DataFrame(rows)
    if "plan_bad_case" in out:  # honest bad case: the cut is measured on it too
        out["cut_es95"] = out["plan_bad_case"] - out["bad_case"]
    return out, {"effects": effs, "costs": arrays, "plan": base}


def _honest(a: object, df: pd.DataFrame | None, group: list[Effect], es95: float) -> dict:
    """The honest bad case of plan + investments (strategies.honest_bad_case): the higher of the model's ES95 and the
    bill in the 2021->22-size shock (the investments' price-dependent part valued at the shock's power prices)."""
    from .strategies import bad_case, shock_prices

    if df is None or "bad_case" not in a.table:
        return {"bad_case": es95, "bad_case_source": "model"}
    order = [(a.delivery_start + pd.DateOffset(months=i)).month - 1 for i in range(12)]
    p = shock_prices(a, df)["power"]
    plan_shock = float(a.table.iloc[-1]["shock"])
    shock = plan_shock + sum(float(e.delta_mwh_eq[order] @ p) + e.fixed_eur for e in group)
    return {
        "bad_case": max(es95, shock),
        "bad_case_source": "stress" if shock > es95 else "model",
        "plan_bad_case": bad_case(a)[0],
    }


def _savings(a: object, group: list[Effect], bad: np.ndarray) -> tuple[float, float]:
    """Yearly saving before capital cost (price-dependent saving - opex + peak shaving - PPA payments), on average
    and averaged over the plan's worst 5% scenarios (`bad`)."""
    if not group:
        return 0.0, 0.0
    d = sum(bill_change(a, e) for e in group)
    capital = sum(annuity(e.capex, _life(e)) for e in group if e.capex)
    saving = -(d - capital)  # bill change without the capital cost, as a saving
    return float(saving.mean()), float(saving[bad].mean())


def _life(e: Effect) -> int:
    return _G["solar"]["lifetime_years"] if e.key == "solar" else _G["battery"]["lifetime_years"]


def assumptions_text(lang: str = "en") -> list[str]:
    """The assumptions, as short lines for the app."""
    s, b = _G["solar"], _G["battery"]
    if lang == "nl":
        return [
            f"Zon: {SOLAR_YIELD_KWH_PER_KWP} kWh/kWp/jaar, capex €{s['capex_eur_per_kwp']}/kWp, onderhoud "
            f"{s['opex_pct_of_capex']}%/jaar, {s['lifetime_years']} jaar; teruglevering tegen "
            f"{s['export_value_share']:.0%} van de day-aheadprijs.",
            f"Batterij: capex €{b['capex_eur_per_kwh']}/kWh, rendement {b['round_trip_efficiency']:.0%}, "
            f"1 cyclus/dag, {b['lifetime_years']} jaar; piekafvlakking €{b['peak_tariff_eur_per_kw_year']}/kW/jaar x "
            f"{b['peak_shaving_share']:.0%} van het vermogen (netkosten).",
            "PPA: vaste prijs voor een deel van het stroomvolume, zelfde profiel als het verbruik; standaardprijs = "
            "de vaste stroomprijs van vandaag.",
            f"Uurprijzen: echte dagprofielen van de laatste {_G['hourly_shape']['lookback_months']} maanden x het "
            f"gesimuleerde maandniveau. Verbruik: {'vlak over de dag' if _G['load_profile'] == 'flat' else 'overdag'}"
            f". Kapitaalkosten als annuïteit tegen {_G['annuity_rate']:.0%}.",
        ]
    return [
        f"Solar: {SOLAR_YIELD_KWH_PER_KWP} kWh/kWp/yr, capex €{s['capex_eur_per_kwp']}/kWp, upkeep "
        f"{s['opex_pct_of_capex']}%/yr, {s['lifetime_years']} years; exports earn {s['export_value_share']:.0%} of "
        "the day-ahead price.",
        f"Battery: capex €{b['capex_eur_per_kwh']}/kWh, round trip {b['round_trip_efficiency']:.0%}, one cycle a day, "
        f"{b['lifetime_years']} years; peak shaving €{b['peak_tariff_eur_per_kw_year']}/kW/yr x "
        f"{b['peak_shaving_share']:.0%} of its power (network cost).",
        "PPA: a fixed price for a share of the power volume, shaped like your use; default price = today's fixed "
        "power price.",
        f"Hourly prices: real day profiles from the last {_G['hourly_shape']['lookback_months']} months x the "
        f"simulated monthly level. Load: {'flat over the day' if _G['load_profile'] == 'flat' else 'daytime'}. "
        f"Capital cost as an annuity at {_G['annuity_rate']:.0%}.",
    ]


TABLE_COLUMNS = {
    "en": {
        "cut_expected": "Expected bill lower by (EUR/yr)",
        "cut_es95": "Bad case lower by (EUR/yr)",
        "capex": "Investment (EUR)",
        "saving_normal": "Saving, normal (EUR/yr)",
        "saving_crisis": "Saving, crisis (EUR/yr)",
        "payback_normal": "Payback, normal (yrs)",
        "payback_crisis": "Payback, crisis (yrs)",
    },
    "nl": {
        "cut_expected": "Verwachte rekening lager met (EUR/jr)",
        "cut_es95": "Slecht scenario lager met (EUR/jr)",
        "capex": "Investering (EUR)",
        "saving_normal": "Besparing, normaal (EUR/jr)",
        "saving_crisis": "Besparing, crisis (EUR/jr)",
        "payback_normal": "Terugverdientijd, normaal (jr)",
        "payback_crisis": "Terugverdientijd, crisis (jr)",
    },
}


def _k(x: float, lang: str) -> str:
    """EUR thousands, signed in words by the caller: '€46k' / '€ 46.000'."""
    return f"€{abs(x) / 1e3:,.0f}k" if lang == "en" else f"€ {round(abs(x), -3):,.0f}".replace(",", ".")


def _yrs(x: float | None, lang: str) -> str:
    if x is None or pd.isna(x):
        return "no payback" if lang == "en" else "geen terugverdientijd"
    return f"{x:.1f} years" if lang == "en" else f"{x:.1f} jaar".replace(".", ",")


def row_sentence(r: pd.Series, lang: str = "en") -> str:
    """One line per investment: effect on the expected and bad-case bill (capital cost included) and payback."""
    de, db = float(r["cut_expected"]), float(r["cut_es95"])
    if lang == "nl":
        word = {True: "hoger", False: "lager"}
        s = (
            f"{r['label_nl']}: verwachte rekening {_k(de, 'nl')} {word[de < 0]}, slecht scenario "
            f"{_k(db, 'nl')} {word[db < 0]} (kapitaalkosten meegerekend)"
        )
        if r["capex"]:
            pn, pc = _yrs(r["payback_normal"], "nl"), _yrs(r["payback_crisis"], "nl")
            s += f"; terugverdientijd {pn} bij normale prijzen, {pc} in een crisis"
        return s + "."
    word = {True: "higher", False: "lower"}
    s = (
        f"{r['investment']}: expected bill {_k(de, 'en')} {word[de < 0]}, bad case {_k(db, 'en')} {word[db < 0]} "
        "(capital cost included)"
    )
    if r["capex"]:
        pn, pc = _yrs(r["payback_normal"], "en"), _yrs(r["payback_crisis"], "en")
        s += f"; payback {pn} at normal prices, {pc} in a crisis"
    return s + "."


def summary_text(t: pd.DataFrame, lang: str = "en") -> str:
    """All rows as a bulleted list (Markdown)."""
    return "\n".join("- " + row_sentence(r, lang) for _, r in t.iterrows())


def report_line(t: pd.DataFrame, lang: str = "en") -> str:
    """The one line for the PDF: the combined row if several investments were selected, else the single one."""
    return row_sentence(t.iloc[-1], lang)


__all__ = [
    "Investments",
    "Effect",
    "load_hourly",
    "day_shapes",
    "simulate_hourly",
    "load_profile",
    "solar_profile",
    "battery_day_value",
    "effects",
    "evaluate",
    "annuity",
    "assumptions_text",
    "row_sentence",
    "summary_text",
    "report_line",
    "TABLE_COLUMNS",
]
