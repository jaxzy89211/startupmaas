"""Market size from CBS StatLine open data: how many mid-sized, energy-intensive companies are there, how much
energy do they use, and what could HedgeWise earn from them?

Sources (CBS StatLine, OData v3, no account; table IDs and periods in config.yaml `market.tables`):
    81589NED  Bedrijven; bedrijfstak                      companies by SBI sector and size class (working persons)
    81578NED  Vestigingen van bedrijven; bedrijfstak, regio   establishments by sector and region (Limburg share)
    83583NED  Banen van werknemers; bedrijfsgrootte en SBI    employee jobs by sector (energy per job)
    83989NED  Energiebalans; aanbod en verbruik, sector       gas and electricity use by sector (PJ)

Method (every step is a table value or a config assumption):
    1. Companies per target sector and size class 10-20, 20-50, 50-100, 100-250 (81589NED; 100-250 = "0 tot 250"
       minus the classes below 100). Limburg = NL count x Limburg's share of the sector's establishments (81578NED).
    2. Sector energy use E (GWh) = final gas use + gas used in CHP plants + final electricity use (83989NED; PJ x
       277.78). Feedstock (non-energy use) is left out.
    3. Energy per working person = E / employee jobs in the sector (83583NED), or, where CBS has no jobs breakdown
       (greenhouse horticulture), E / (sum over all size classes of companies x class midpoint persons).
    4. Average energy use of a company in a class = class midpoint persons x energy per person (ASSUMPTION: within a
       sector, energy use is proportional to company size).
    5. Target = size classes 10-250 whose average use is at least min_energy_gwh. Energy spend = GWh x the 2025
       average gas / power price (commodity only).
    6. Revenue = adoption x companies x (subscription + EUR per GWh x average GWh).

If CBS is unreachable, the same tables are read from data/cbs/<TABLE>.csv (written on every successful download, so
the repository carries the snapshot used for MARKET.md). Column names are the CBS OData TypedDataSet names.

    python market.py [--offline]
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from .config import load_config
from .provenance import one_line, save_figure, write_csv
from .settings import DATA, OUT

_M = load_config()["market"]
CBS = DATA / "cbs"
API = "https://opendata.cbs.nl/ODataApi/odata"
GWH_PER_PJ = 277.7778
FIRM_COLS = {
    "1": "k_1WerkzaamPersoon_2",
    "2": "k_2WerkzamePersonen_3",
    "3-5": "k_3Tot5WerkzamePersonen_4",
    "5-10": "k_5Tot10WerkzamePersonen_5",
    "10-20": "k_10Tot20WerkzamePersonen_6",
    "20-50": "k_20Tot50WerkzamePersonen_7",
    "50-100": "k_50Tot100WerkzamePersonen_8",
}
CARRIERS = {"gas": "E006560", "power": "E006607"}  # 83989NED Energiedragers: Aardgas, Elektriciteit


# --------------------------------------------------------------------------------------------
# Fetching (with an offline snapshot in data/cbs/)
# --------------------------------------------------------------------------------------------
def _odata(table: str, flt: str) -> pd.DataFrame:
    import requests

    url, rows = f"{API}/{table}/TypedDataSet", []
    params: dict | None = {"$filter": flt, "$format": "json"}
    while url:
        r = requests.get(url, params=params, timeout=60)
        r.raise_for_status()
        j = r.json()
        rows += j["value"]
        url, params = j.get("odata.nextLink"), None
    return pd.DataFrame(rows)


def fetch(table: str, flt: str, offline: bool = False) -> tuple[pd.DataFrame, str]:
    """A filtered CBS table and where it came from ('CBS API' or the snapshot file). Strips CBS's padded keys."""
    path = CBS / f"{table}.csv"
    src = f"data/cbs/{table}.csv"
    if not offline:
        try:
            df = _odata(table, flt)
            CBS.mkdir(parents=True, exist_ok=True)
            df.to_csv(path, index=False)
            src = f"CBS API ({API}/{table})"
        except Exception as e:  # noqa: BLE001 - any network problem -> the snapshot
            if not path.exists():
                raise RuntimeError(
                    f"CBS is unreachable ({e}) and {path} does not exist. Download table {table} from "
                    "https://opendata.cbs.nl/statline/ (CSV with the CBS column names) and save it there."
                ) from e
            df = pd.read_csv(path, dtype=str)
    else:
        df = pd.read_csv(path, dtype=str)
    for c in df.columns:
        if pd.api.types.is_string_dtype(df[c]):
            df[c] = df[c].str.strip()
    return df, src


_PADDED: dict = {}


def _padded(table: str, dim: str) -> dict[str, str]:
    """CBS pads dimension keys with spaces ('307610  '); OData 'eq' needs the exact key. {stripped: raw}."""
    if (table, dim) not in _PADDED:
        import requests

        try:
            v = requests.get(f"{API}/{table}/{dim}", timeout=60).json()["value"]
            _PADDED[(table, dim)] = {x["Key"].strip(): x["Key"] for x in v}
        except Exception:  # noqa: BLE001 - offline: the filter is not used anyway
            _PADDED[(table, dim)] = {}
    return _PADDED[(table, dim)]


def _or(field: str, keys: list[str], table: str = "") -> str:
    pad = _padded(table, field) if table else {}
    return "(" + " or ".join(f"{field} eq '{pad.get(k, k)}'" for k in keys) + ")"


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce").fillna(0.0)


# --------------------------------------------------------------------------------------------
# The estimate
# --------------------------------------------------------------------------------------------
def load_inputs(offline: bool = False) -> dict:
    """The four CBS tables, filtered to the target sectors and periods."""
    t, secs = _M["tables"], _M["sectors"]
    firm_keys = sorted({k for s in secs for k in s["firms"]})
    region_keys = sorted({k for s in secs for k in s.get("region", s["firms"])})
    sbi = "BedrijfstakkenBranchesSBI2008"
    firms, s1 = fetch(t["firms"], f"Perioden eq '{t['firms_period']}' and {_or(sbi, firm_keys, t['firms'])}", offline)
    est, s2 = fetch(
        t["establishments"],
        f"Perioden eq '{t['establishments_period']}' and "
        f"{_or('RegioS', [_M['region_nl'], _M['region_limburg']], t['establishments'])}"
        f" and {_or(sbi, region_keys, t['establishments'])}",
        offline,
    )
    job_keys = sorted({s["jobs"] for s in secs if s["jobs"]})
    jobs, s3 = fetch(t["jobs"], f"Perioden eq '{t['jobs_period']}' and {_or(sbi, job_keys, t['jobs'])}", offline)
    en_keys = sorted({s["energy"] for s in secs})
    energy, s4 = fetch(
        t["energy"],
        f"Perioden eq '{t['energy_period']}' and {_or('Sectoren', en_keys, t['energy'])} and "
        f"{_or('Energiedragers', list(CARRIERS.values()), t['energy'])}",
        offline,
    )
    return {"firms": firms, "est": est, "jobs": jobs, "energy": energy, "sources": [s1, s2, s3, s4]}


def estimate(inp: dict, intensity: float | None = None) -> pd.DataFrame:
    """One row per sector x size class: companies (NL, Limburg), average GWh, target flag, energy spend.
    intensity: energy per person of a 10-250 company relative to the sector average (default: config)."""
    intensity = _M["sme_energy_intensity"] if intensity is None else intensity
    firms, est, jobs, energy = inp["firms"], inp["est"], inp["jobs"], inp["energy"]
    sbi = "BedrijfstakkenBranchesSBI2008"
    mid = {k: float(v) for k, v in _M["class_midpoint_persons"].items()}
    price = _M["energy_prices_eur_mwh"]
    rows = []
    for s in _M["sectors"]:
        f = firms[firms[sbi].isin(s["firms"])]
        n = {c: _num(f[col]).sum() for c, col in FIRM_COLS.items()}
        n["100-250"] = _num(f["k_0Tot250WerkzamePersonen_11"]).sum() - sum(n.values())
        n["250+"] = _num(f["TotaalBedrijven_1"]).sum() - _num(f["k_0Tot250WerkzamePersonen_11"]).sum()
        e = energy[energy["Sectoren"] == s["energy"]].set_index("Energiedragers")
        gas_pj = float(_num(e.loc[[CARRIERS["gas"]], "FinaalEnergieverbruik_19"]).sum()) + float(
            _num(e.loc[[CARRIERS["gas"]], "InzetElektriciteitWKKOmzetting_12"]).sum()
        )
        pow_pj = float(_num(e.loc[[CARRIERS["power"]], "FinaalEnergieverbruik_19"]).sum())
        gas_gwh, pow_gwh = gas_pj * GWH_PER_PJ, pow_pj * GWH_PER_PJ
        if s["jobs"]:
            j = jobs[(jobs[sbi] == s["jobs"]) & (jobs["Bedrijfsgrootte"] == "T001098")]
            persons, persons_src = float(_num(j["BanenVanWerknemersInDecember_1"]).sum()) * 1000, "jobs (83583NED)"
        else:
            persons, persons_src = sum(n[c] * mid[c] for c in n), "firms x class midpoints"
        e_per_person = (gas_gwh + pow_gwh) / persons if persons else np.nan
        reg_rows = est[est[sbi].isin(s.get("region", s["firms"]))]
        reg = reg_rows.groupby("RegioS")["Vestigingen_1"].apply(lambda x: _num(x).sum())
        lim_share = float(reg.get(_M["region_limburg"], 0) / reg.get(_M["region_nl"], np.nan))
        for c in _M["size_classes"]:
            gwh = mid[c] * e_per_person * intensity
            rows.append(
                {
                    "sector": s["name"],
                    "size_class": c,
                    "companies_nl": n[c],
                    "limburg_share": lim_share,
                    "companies_limburg": n[c] * lim_share,
                    "sector_gas_gwh": gas_gwh,
                    "sector_power_gwh": pow_gwh,
                    "persons": persons,
                    "persons_source": persons_src,
                    "gwh_per_person": e_per_person,
                    "avg_gwh_per_company": gwh,
                    "target": gwh >= _M["min_energy_gwh"],
                    "gas_share": gas_gwh / (gas_gwh + pow_gwh) if gas_gwh + pow_gwh else np.nan,
                }
            )
    out = pd.DataFrame(rows)
    out["avg_spend_eur"] = (
        out.avg_gwh_per_company * 1000 * (out.gas_share * price["gas"] + (1 - out.gas_share) * price["power"])
    )
    return out


def summary(est: pd.DataFrame) -> pd.DataFrame:
    """The headline table (plain labels): target companies, their energy use and spend, revenue at each adoption."""
    r = _M["revenue"]
    t = est[est.target]
    rows = []
    for region, col in (("Netherlands", "companies_nl"), ("Limburg", "companies_limburg")):
        n = float(t[col].sum())
        gwh = float((t[col] * t.avg_gwh_per_company).sum())
        spend = float((t[col] * t.avg_spend_eur).sum())
        row = {
            "region": region,
            "all_10_250_in_these_sectors": float(est[col].sum()),
            "target_companies": n,
            "energy_use_gwh": gwh,
            "energy_spend_eur": spend,
            "revenue_if_all_subscribe_eur": n * r["subscription_eur"] + gwh * r["eur_per_gwh"],
        }
        for a in r["adoption"]:
            row[f"revenue_{a:.0%}_adoption_eur"] = a * row["revenue_if_all_subscribe_eur"]
        rows.append(row)
    return pd.DataFrame(rows).set_index("region")


def plot_market(est: pd.DataFrame, summ: pd.DataFrame, path: Path) -> None:
    """Target companies per sector (NL and Limburg) and yearly revenue at each adoption rate."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    t = est[est.target].groupby("sector")[["companies_nl", "companies_limburg"]].sum().sort_values("companies_nl")
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(13, 5.2), gridspec_kw={"width_ratios": [1.6, 1]})
    y = np.arange(len(t))
    ax.barh(y + 0.2, t.companies_nl, 0.4, color="#2a6fdb", label="Netherlands")
    ax.barh(y - 0.2, t.companies_limburg, 0.4, color="#1b9e5a", label="Limburg")
    for i, (a, b) in enumerate(zip(t.companies_nl, t.companies_limburg, strict=True)):
        ax.text(a, i + 0.2, f" {a:,.0f}", va="center", fontsize=8)
        ax.text(b, i - 0.2, f" {b:,.0f}", va="center", fontsize=8)
    ax.set_yticks(y, [s.split(" (")[0] for s in t.index], fontsize=9)
    ax.set_xlabel(f"Target companies (10-250 persons, estimated use >= {_M['min_energy_gwh']:g} GWh/yr)")
    ax.set_xlim(0, t.companies_nl.max() * 1.18)
    ax.legend(frameon=False, loc="lower right")
    ax.set_title("Who: target companies by sector", fontsize=11)
    ax.spines[["top", "right"]].set_visible(False)
    adopt = _M["revenue"]["adoption"]
    x = np.arange(len(adopt))
    for k, (region, col) in enumerate((("Netherlands", "#2a6fdb"), ("Limburg", "#1b9e5a"))):
        v = [summ.loc[region, f"revenue_{a:.0%}_adoption_eur"] / 1e3 for a in adopt]
        ax2.bar(x + (k - 0.5) * 0.38, v, 0.36, color=col, label=region)
        for xi, vi in zip(x + (k - 0.5) * 0.38, v, strict=True):
            ax2.text(xi, vi, f"{vi:,.0f}", ha="center", va="bottom", fontsize=8)
    ax2.set_xticks(x, [f"{a:.0%} subscribe" for a in adopt])
    ax2.set_ylabel("Revenue, EUR k per year")
    r = _M["revenue"]
    ax2.set_title(f"How much: EUR {r['subscription_eur']:,}/yr + EUR {r['eur_per_gwh']}/GWh", fontsize=11)
    ax2.legend(frameon=False)
    ax2.spines[["top", "right"]].set_visible(False)
    fig.suptitle("HedgeWise market size (CBS StatLine; estimates, see MARKET.md)", fontsize=12, weight="bold")
    fig.tight_layout()
    save_figure(fig, path, dpi=140)
    plt.close(fig)


def main(argv: list[str] | None = None) -> pd.DataFrame:
    """Fetch (or read the snapshot), estimate, write output/market_*.csv and market.png, print the tables."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--offline", action="store_true", help="use the snapshot in data/cbs/ only")
    args = ap.parse_args(argv)
    inp = load_inputs(args.offline)
    est = estimate(inp)
    summ = summary(est)
    OUT.mkdir(exist_ok=True)
    write_csv(est, OUT / "market_segments.csv", index=False)
    write_csv(summ, OUT / "market_summary.csv")
    plot_market(est, summ, OUT / "market.png")
    pd.set_option("display.width", 220)
    show = est.copy()
    show["companies_nl"] = show.companies_nl.round(0).astype(int)
    show["companies_limburg"] = show.companies_limburg.round(1)
    print("Sources: " + "; ".join(inp["sources"]))
    print(
        show[["sector", "size_class", "companies_nl", "companies_limburg", "avg_gwh_per_company", "target"]]
        .round(2)
        .to_string(index=False)
    )
    print("\n" + (summ.T / 1).round(0).to_string())
    print("Provenance: " + one_line())
    return summ


__all__ = ["fetch", "load_inputs", "estimate", "summary", "plot_market", "main"]
