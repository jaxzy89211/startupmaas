"""Charts: scenario fan charts, trade-off (efficient frontier), model fit and stress scenarios."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .models import POWER_FIT_DAYS, FittedModel, _power_design
from .settings import STRESS_KEYS
from .strategies import Analysis


def plot_stress(s: dict, path=None, ax=None, lang: str = "en", compact: bool = False):
    """Grouped bars: one group per scenario, one bar per strategy. compact=True for the PDF (smaller text)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    own = ax is None
    if own:
        fig, ax = plt.subplots(figsize=(9, 4.2))
    names = {"en": ["All variable", f"HedgeWise (fix {s['hedge']:.0%})", "Fix 60% now", "Fix 100% now"],
             "nl": ["Alles variabel", f"HedgeWise ({s['hedge']:.0%} vast)", "60% vast nu", "100% vast nu"]}[lang]
    colors = ["#b8433a", "#1b9e5a", "#2a6fdb", "#6b7280"]
    scs = s["scenarios"]
    w = 0.2
    fs = 7 if compact else 9
    top = max(v for sc in scs for v in sc["bills"].values()) / 1e6
    for j, key in enumerate(STRESS_KEYS):
        xs = np.arange(len(scs)) + (j - 1.5) * w
        vals = [sc["bills"][key] / 1e6 for sc in scs]
        ax.bar(xs, vals, w * 0.92, color=colors[j], label=names[j])
        for x, v in zip(xs, vals):
            t = f"{v:.2f}" if lang == "en" else f"{v:.2f}".replace(".", ",")
            ax.text(x, v + top * 0.015, t, ha="center", va="bottom", fontsize=fs - 0.5)
    ticks = []
    for sc in scs:
        p = sc["avg_price"]
        if lang == "en":
            ticks.append(f"{sc['label']['en']}\n(gas avg €{p['gas']:.0f}, power €{p['power']:.0f}/MWh)")
        else:
            ticks.append(f"{sc['label']['nl']}\n(gas gem. € {p['gas']:.0f}, stroom € {p['power']:.0f}/MWh)")
    ax.set_xticks(np.arange(len(scs)), ticks, fontsize=fs)
    ax.set_ylim(0, top * 1.22)
    ax.set_ylabel("€ m" if lang == "en" else "€ mln", fontsize=fs)
    ax.tick_params(axis="y", labelsize=fs)
    # legend in the empty top-left corner (the "repeat" bars are lower than the "shock" bars)
    ax.legend(fontsize=fs - 0.5, frameon=False, ncol=4 if not compact else 2,
              loc="upper center" if not compact else "upper left")
    if not compact:
        ax.set_title("What if 2022 happens again? (historical stress scenarios, not forecasts)" if lang == "en"
                     else "Wat als 2022 zich herhaalt? (historische stressscenario's, geen voorspellingen)", fontsize=11)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", alpha=0.25)
    if own:
        fig.tight_layout()
        fig.savefig(path, dpi=140, format="png")
        plt.close(fig)


# --------------------------------------------------------------------------------------------
# Charts
# --------------------------------------------------------------------------------------------
COLORS = {"gas": "#2a6fdb", "power": "#e07b00", "co2": "#4f8a3a"}
STRAT_COLORS = ["#b8433a", "#8a8f98", "#4b5563", "#2a6fdb", "#1b9e5a"]


def plot_analysis(a: Analysis, path: Path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(2, 2, figsize=(14, 9))
    dates = a.sims["dates"]
    for ax, c in zip(axes[0], ("gas", "power")):
        hist = a.model.history[c].iloc[-730:]
        ax.plot(hist.index, hist.rolling(7).mean(), color=COLORS[c], lw=1.2, label="history (7-day avg)")
        # prepend the last 6 observed days so the 7-day average is a full window from day 1
        # (v0.2 used min_periods=1, which made the first simulated days look like a spike)
        raw = a.sims[c][:, :4000]
        pre = np.repeat(a.model.history[c].iloc[-6:].values[:, None], raw.shape[1], axis=1)
        p = pd.DataFrame(np.vstack([pre, raw])).rolling(7).mean().values[6:]
        qs = np.quantile(p, [0.05, 0.25, 0.5, 0.75, 0.95], axis=1)
        ax.fill_between(dates, qs[0], qs[4], color=COLORS[c], alpha=0.15, label="5-95% of scenarios")
        ax.fill_between(dates, qs[1], qs[3], color=COLORS[c], alpha=0.3, label="25-75%")
        ax.plot(dates, qs[2], color=COLORS[c], ls="--", lw=1, label="median")
        ax.axvspan(a.delivery_start, dates[-1], color="k", alpha=0.04)
        ax.set_title(f"{c.title()} price, EUR/MWh, 7-day avg (as of {a.model.as_of:%d %b %Y}); grey = delivery")
        ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%b %y"))
        ax.set_ylim(0, np.nanmax([hist.max(), qs[4].max()]) * 1.05)
        ax.legend(loc="upper left", fontsize=8, frameon=False)
        ax.grid(alpha=0.25)
    plot_frontier(a, axes[1, 0])
    ax = axes[1, 1]
    t = a.table
    y = np.arange(len(t))
    ax.barh(y, t.expected / 1e6, color="#cbd5e1", label="Expected")
    ax.barh(y, (t.es95 - t.expected) / 1e6, left=t.expected / 1e6, color="#ef8a62", label="extra in worst 5% (ES95)")
    for i, (e, es) in enumerate(zip(t.expected, t.es95)):
        ax.text(es / 1e6, i, f"  {es/1e6:.2f}", va="center", fontsize=8)
    ax.set_yticks(y, [n.replace(" (", "\n(") for n in t.index], fontsize=8)
    ax.invert_yaxis()
    ax.set_title("Expected bill vs bad-case bill (EUR m)")
    ax.legend(fontsize=8, frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.06), ncol=2)
    ax.set_xlim(0, t.es95.max() / 1e6 * 1.12)
    ax.grid(alpha=0.25, axis="x")
    fig.suptitle(f"HedgeWise - {a.company.name}", fontsize=13, weight="bold")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


FRONTIER_TEXT_EN = {
    "title": "Pick your trade-off: share of next year's energy fixed",
    "xlabel": "Bad-case bill: average of worst 5% of scenarios (EUR m)",
    "ylabel": "Expected bill (EUR m)",
    "pick": "HedgeWise pick: fix {h} (risk aversion {g})",
    "pick_budget": "HedgeWise pick: fix {h} (budget)",
    "tradeoff": "Each extra 10% fixed: about EUR {e}k more on average,\nEUR {es}k less in the bad case",
    "decimal": ".",
}


def plot_frontier(a: Analysis, ax=None, path=None, text: dict | None = None, show_tradeoff: bool = True):
    """Efficient frontier: expected bill vs bad-case bill (ES95) for one hedge ratio 0-100% (gas and power),
    executed the recommended way. The CFO picks a point; HedgeWise marks the one for their risk appetite.
    `text` overrides the labels (e.g. Dutch, see report.py); `path` may be a file path or a binary buffer."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter
    tx = {**FRONTIER_TEXT_EN, **(text or {})}
    own = ax is None
    if own:
        fig, ax = plt.subplots(figsize=(8, 5.5))
    r = a.recommendation
    f = a.frontier
    f = f[(f.execution == r["execution"]) & (np.isclose(f.hedge_gas, f.hedge_power))].sort_values("hedge_gas")
    ax.plot(f.es95 / 1e6, f.expected / 1e6, "-", color="#2a6fdb", lw=1.8, zorder=2)
    for _, row in f.iterrows():
        h = row.hedge_gas
        is_lbl = np.isclose(h % 0.25, 0) or np.isclose(h % 0.25, 0.25)
        ax.plot(row.es95 / 1e6, row.expected / 1e6, "o", ms=6 if is_lbl else 3, color="#2a6fdb", zorder=3)
        if is_lbl:
            ax.annotate(f"{h:.0%}", (row.es95 / 1e6, row.expected / 1e6), textcoords="offset points",
                        xytext=(4, 6), fontsize=9, color="#1e3a8a")
    rec = f[np.isclose(f.hedge_gas, r["hedge_gas"])]
    if len(rec):
        h_txt = f"{r['hedge_gas']:.0%}"
        lbl = (tx["pick_budget"] if r["mode"] == "budget" else tx["pick"]).format(
            h=h_txt, g=str(r["risk_aversion"]).replace(".", tx["decimal"]))
        ax.plot(rec.es95.iloc[0] / 1e6, rec.expected.iloc[0] / 1e6, "*", ms=18, color="#1b9e5a", zorder=4, label=lbl)
    if show_tradeoff and len(f) > 2:
        lo, hi = f.iloc[0], f.iloc[-1]
        d_e = (hi.expected - lo.expected) / 10 / 1e3
        d_es = (lo.es95 - hi.es95) / 10 / 1e3
        ax.text(0.03, 0.05, tx["tradeoff"].format(e=f"{d_e:,.0f}", es=f"{d_es:,.0f}"),
                transform=ax.transAxes, fontsize=9, color="#333")
    if tx["decimal"] == ",":
        fmt = FuncFormatter(lambda v, _: f"{v:.2f}".replace(".", ","))
        ax.xaxis.set_major_formatter(fmt)
        ax.yaxis.set_major_formatter(fmt)
    ax.margins(x=0.07, y=0.12)   # room for the 0% / 100% labels at the ends
    ax.set_xlabel(tx["xlabel"])
    ax.set_ylabel(tx["ylabel"])
    ax.set_title(tx["title"])
    ax.legend(fontsize=8, frameon=False, loc="upper right")
    ax.grid(alpha=0.25)
    if own:
        fig.tight_layout()
        fig.savefig(path, dpi=140, format="png")
        plt.close(fig)


def plot_fit(m: FittedModel, path: Path):
    """In-sample check: actual vs fitted power, and GARCH volatility over time."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    h = m.history
    d = h.iloc[-POWER_FIT_DAYS:]
    X = _power_design(d.index, {k: np.log(d[k].values) for k in m.drivers})
    fitted = np.exp(X @ m.power.coef)
    fig, ax = plt.subplots(1, 2, figsize=(14, 4.5))
    ax[0].plot(d.index, d.power, color=COLORS["power"], lw=0.7, label="actual day-ahead")
    ax[0].plot(d.index, fitted, color="k", lw=1, label=f"model (R2 = {m.power.r2:.2f})")
    ax[0].set_title("Power: actual vs explained by gas + seasonality")
    ax[0].legend(frameon=False)
    for k in m.drivers:
        g = m.garch[k]
        ax[1].plot(g.dates, np.sqrt(g.cond_var * 252) * 100, color=COLORS[k], lw=1, label=f"{k} (GARCH-t, nu={g.nu:.1f})")
    ax[1].set_title("Annualised volatility, %")
    ax[1].legend(frameon=False)
    for a_ in ax:
        a_.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
