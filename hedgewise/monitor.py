"""Monthly update per client: the reason a subscription is worth paying for.

For every client in clients/*.yaml (one file per client: consumption, risk appetite, and the plan: delivery year,
target share, monthly tranche months and the tranches already bought):
    1. refresh prices (download_data.py; skipped when offline or with --no-download),
    2. refit the model and recompute next delivery year's expected and bad-case bill for the client's position
       (tranches bought so far at their prices, the rest variable) and for the full plan (the rest of the target
       bought at today's fixed price),
    3. compare with last month's run (output/history/<client>.csv) and work out what the plan says to do this month,
    4. flag big changes (bad case +-15%, fixed prices +-20%) and overdue tranches,
    5. write a one-page "monthly update" PDF (EN/NL) and an email DRAFT per client, plus an overview table.

THE UPDATE IS A DRAFT. Nothing is sent: a person checks the numbers and sends it. The plan rule is fixed in advance
(equal monthly tranches up to the agreed target); the model's current advice is shown for information only and
never changes the plan by itself.

    python monitor.py [--clients clients] [--no-download] [--as-of YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import io
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

import hedgewise as hw

from . import company_input as ci
from .config import load_config
from .provenance import one_line, write_csv
from .settings import DEFAULT_SEED, ROOT
from .texts import money, pct

_M = load_config()["monitor"]
HISTORY = hw.OUT / "history"
UPDATES = hw.OUT / "monitor"
HIST_COLS = [
    "run_date",
    "data_date",
    "delivery_year",
    "bought_share",
    "planned_share",
    "target_share",
    "model_target",
    "gas_fixed",
    "power_fixed",
    "expected_now",
    "bad_case_now",
    "expected_plan",
    "bad_case_plan",
    "bad_case_variable",
]


# --------------------------------------------------------------------------------------------
# Client files and the plan
# --------------------------------------------------------------------------------------------
def slug(name: str) -> str:
    """File-name-safe client name."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def load_client(path: Path) -> dict:
    """Read and check one client YAML; adds the Company under 'company'."""
    c = yaml.safe_load(Path(path).read_text())
    for key in ("name", "language", "consumption", "plan"):
        if key not in c:
            raise ValueError(f"{path}: missing '{key}'")
    p = c["plan"]
    for key in ("delivery_year", "target_share", "tranche_months"):
        if key not in p:
            raise ValueError(f"{path}: plan needs '{key}'")
    p.setdefault("tranches", [])
    if not 0 <= p["target_share"] <= 1 or sum(t["share"] for t in p["tranches"]) > 1 + 1e-9:
        raise ValueError(f"{path}: target and bought shares must be between 0 and 100%")
    cons = c["consumption"]
    sector = cons.get("sector", "food processing")
    if "file" in cons:
        res = ci.load_company(ROOT / cons["file"], name=c["name"], sector=sector)
    else:
        y = cons["yearly"]
        res = ci.from_yearly(
            y.get("gas", 0), y.get("gas_unit", "m3"), y.get("power", 0), y.get("power_unit", "MWh"), sector, c["name"]
        )
    c["company"], c["consumption_warnings"] = res.company, res.warnings
    return c


def plan_status(plan: dict, as_of: pd.Timestamp) -> dict:
    """Where the plan stands on `as_of`. Planned: equal tranches of target / n over tranche_months, so by the end
    of month m the plan has target x (months up to m) / n. Buy now = planned through this month - bought (>= 0).
    Overdue = more than the tolerance still missing from months that are already over."""
    months = [pd.Period(m, "M") for m in plan["tranche_months"]]
    now = as_of.to_period("M")
    n = len(months)
    target = float(plan["target_share"])
    bought = float(sum(t["share"] for t in plan["tranches"] if pd.Timestamp(t["date"]) <= as_of))
    planned_now = target * sum(m <= now for m in months) / n
    planned_before = target * sum(m < now for m in months) / n
    return {
        "target": target,
        "bought": bought,
        "planned": planned_now,
        "buy_now": max(0.0, planned_now - bought),
        "overdue": max(0.0, planned_before - bought),
        "is_overdue": planned_before - bought > _M["overdue_tolerance"],
        "months_left": sum(m >= now for m in months),
        "finished": now > months[-1] if months else True,
    }


def position_costs(a: hw.Analysis, plan: dict, as_of: pd.Timestamp) -> dict[str, np.ndarray]:
    """Bill per scenario for the delivery year: 'now' = tranches bought so far at their prices + the rest variable;
    'plan' = the same + the rest of the target at today's fixed price; 'variable' = nothing fixed."""
    order = [(a.delivery_start + pd.DateOffset(months=i)).month - 1 for i in range(12)]
    vol = {"gas": a.company.gas_mwh[order], "power": a.company.power_mwh[order]}
    bought = [t for t in plan["tranches"] if pd.Timestamp(t["date"]) <= as_of]
    b_share = sum(t["share"] for t in bought)
    out = {"variable": 0.0, "now": 0.0, "plan": 0.0}
    rest = max(0.0, plan["target_share"] - b_share)
    for c in ("gas", "power"):
        var = vol[c] @ a.spot_month[c]  # (n,)
        fixed = sum(t["share"] * t[f"{c}_eur_mwh"] for t in bought) * vol[c].sum()
        out["variable"] = out["variable"] + var
        out["now"] = out["now"] + fixed + (1 - b_share) * var
        out["plan"] = out["plan"] + fixed + rest * a.fwd_now[c] * vol[c].sum() + (1 - b_share - rest) * var
    return out


def position_shock(a: hw.Analysis, df: pd.DataFrame, plan: dict, as_of: pd.Timestamp) -> dict[str, float]:
    """The same three positions in the 'shock as big as 2021->2022, from today' scenario (the rest of the target
    bought at today's fixed price, as in the stress test). Empty-price data -> zeros (bad case = the model's)."""
    from .strategies import _delivery_vol, shock_prices

    try:
        p = shock_prices(a, df)
    except ValueError:
        return {"variable": 0.0, "now": 0.0, "plan": 0.0}
    vol = _delivery_vol(a)
    bought = [t for t in plan["tranches"] if pd.Timestamp(t["date"]) <= as_of]
    b = sum(t["share"] for t in bought)
    rest = max(0.0, plan["target_share"] - b)
    out = {"variable": 0.0, "now": 0.0, "plan": 0.0}
    for c in ("gas", "power"):
        var = float(vol[c] @ p[c])
        fixed = sum(t["share"] * t[f"{c}_eur_mwh"] for t in bought) * vol[c].sum()
        out["variable"] += var
        out["now"] += fixed + (1 - b) * var
        out["plan"] += fixed + rest * a.fwd_now[c] * vol[c].sum() + (1 - b - rest) * var
    return out


def _es(x: np.ndarray) -> float:
    q = np.quantile(x, 0.95)
    return float(x[x >= q].mean())


# --------------------------------------------------------------------------------------------
# One client
# --------------------------------------------------------------------------------------------
def run_client(
    client: dict,
    df: pd.DataFrame,
    as_of: pd.Timestamp | None = None,
    n_paths: int = _M["n_paths"],
    history_dir: Path | None = None,
    out_dir: Path | None = None,
) -> dict:
    """Compute the update for one client, append it to its history, write the PDF and the email draft.
    Folders default to output/history/ and output/monitor/ (both git-ignored: client data is confidential)."""
    history_dir, out_dir = history_dir or HISTORY, out_dir or UPDATES
    as_of = pd.Timestamp(as_of) if as_of is not None else df.index.max()
    plan = client["plan"]
    ds = pd.Timestamp(int(plan["delivery_year"]), 1, 1)
    if as_of >= ds:
        raise ValueError(f"{client['name']}: delivery year {ds.year} has started; set up next year's plan")
    m = hw.fit(df.loc[:as_of], as_of)
    a = hw.recommend(
        m,
        client["company"],
        df.loc[:as_of],
        ds,
        n_paths,
        DEFAULT_SEED,
        client.get("rule", "ladder"),
        client.get("risk_aversion", 0.5),
        client.get("budget"),
        plan.get("previous_season_target"),
    )
    st = plan_status(plan, as_of)
    costs = position_costs(a, plan, as_of)
    shock = position_shock(a, df.loc[:as_of], plan, as_of)
    row = {
        "run_date": str(as_of.date()),
        "data_date": str(min(as_of, df.index.max()).date()),  # the last price the model saw
        "delivery_year": int(plan["delivery_year"]),
        "bought_share": st["bought"],
        "planned_share": st["planned"],
        "target_share": st["target"],
        "model_target": float(a.recommendation["hedge_gas"]),
        "gas_fixed": float(a.fwd_now["gas"]),
        "power_fixed": float(a.fwd_now["power"]),
        "expected_now": float(costs["now"].mean()),
        "bad_case_now": max(_es(costs["now"]), shock["now"]),  # honest bad case: never below the 2021->22 shock
        "expected_plan": float(costs["plan"].mean()),
        "bad_case_plan": max(_es(costs["plan"]), shock["plan"]),
        "bad_case_variable": max(_es(costs["variable"]), shock["variable"]),
    }
    history_dir.mkdir(parents=True, exist_ok=True)
    hpath = history_dir / f"{slug(client['name'])}.csv"
    hist = pd.read_csv(hpath, comment="#") if hpath.exists() else pd.DataFrame(columns=HIST_COLS)
    hist = hist[hist.run_date != row["run_date"]]  # a rerun on the same day replaces that day's row
    prev = hist[hist.run_date < row["run_date"]].tail(1)
    prev = None if prev.empty else prev.iloc[0].to_dict()
    hist = pd.concat([hist, pd.DataFrame([row])], ignore_index=True).sort_values("run_date")
    write_csv(hist[HIST_COLS], hpath, index=False)
    res = {"client": client, "row": row, "prev": prev, "status": st, "flags": flags(row, prev, st), "history": hist}
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{slug(client['name'])}_{row['run_date']}"
    make_update_pdf(res, out_dir / f"{stem}_update.pdf")
    (out_dir / f"{stem}_email.txt").write_text(email_draft(res))
    res["files"] = [out_dir / f"{stem}_update.pdf", out_dir / f"{stem}_email.txt"]
    return res


def flags(row: dict, prev: dict | None, st: dict) -> list[dict]:
    """Big changes since the last run and overdue tranches. Each flag: {key, values} (worded per language)."""
    out = []
    if prev is not None:
        ch = row["bad_case_now"] / prev["bad_case_now"] - 1
        if abs(ch) > _M["bad_case_change"]:
            out.append({"key": "bad_case", "change": ch, "old": prev["bad_case_now"], "new": row["bad_case_now"]})
        for c in ("gas", "power"):
            ch = row[f"{c}_fixed"] / prev[f"{c}_fixed"] - 1
            if abs(ch) > _M["price_change"]:
                out.append(
                    {"key": "price", "commodity": c, "change": ch, "old": prev[f"{c}_fixed"], "new": row[f"{c}_fixed"]}
                )
    if st["is_overdue"]:
        out.append({"key": "overdue", "missing": st["overdue"]})
    return out


# --------------------------------------------------------------------------------------------
# Words (EN/NL)
# --------------------------------------------------------------------------------------------
TXT = {
    "en": {
        "title": "Monthly update",
        "subtitle": "{date} · market prices up to {data} · delivery year {year}",
        "draft": "DRAFT for HedgeWise to check before sending",
        "todo_h": "What to do this month",
        "buy": "Buy **{s}** of {year} gas and electricity this month at today's fixed price (gas {g}, "
        "electricity {p} per MWh). That brings you to {after} of the {target} target.",
        "nothing": "Nothing to buy this month: you have bought {b}, on plan ({p} planned so far).",
        "finished": "The buying season for {year} is over: {b} of your volume is fixed ({target} target).",
        "model": "For information: with today's prices the HedgeWise ladder would set the target at {m} (your agreed "
        "target: {target}). The plan doesn't change by itself; let's discuss it if the gap stays. The bad case is "
        "never lower than a repeat of the 2021->2022 price shock.",
        "changed_h": "What changed since {prev}",
        "first": "This is the first update: from next month you'll see what changed.",
        "row_bad": "Bad case for {year} with what you've bought so far",
        "row_exp": "Expected bill for {year} with what you've bought so far",
        "row_gas": "Fixed gas price (EUR/MWh)",
        "row_power": "Fixed electricity price (EUR/MWh)",
        "flags_h": "Attention",
        "flag_bad_case": "The bad case moved {chg} since last month ({old} -> {new}).",
        "flag_price": "The fixed {c} price moved {chg} since last month ({old} -> {new} per MWh).",
        "flag_overdue": "A planned purchase is overdue: {m} of your volume should already have been fixed.",
        "progress_h": "Plan progress",
        "bought": "bought",
        "planned": "planned by now",
        "target": "target",
        "bad_hist": "Bad case over time",
        "now_vs": "Now: expected {e}, bad case {b}. With the full plan: expected {pe}, bad case {pb}. "
        "All variable: bad case {v}.",
        "footer": "HedgeWise monthly update. Commodity cost only; the fixed price is a proxy for forward prices "
        "(recent market price + 5%). Scenarios come from a statistical model and can miss events that never happened "
        "before. Decision support, not financial advice.",
        "gas": "gas",
        "power": "electricity",
        "email_subject": "HedgeWise monthly update {date}: {todo}",
        "email_buy": "buy {s} this month",
        "email_nothing": "nothing to buy this month",
        "email_hello": "Hello,",
        "email_bye": "Kind regards,\nHedgeWise",
        "email_attached": "The one-page update is attached.",
    },
    "nl": {
        "title": "Maandelijkse update",
        "subtitle": "{date} · marktprijzen t/m {data} · leveringsjaar {year}",
        "draft": "CONCEPT, door HedgeWise te controleren voor verzending",
        "todo_h": "Wat te doen deze maand",
        "buy": "Koop deze maand **{s}** van het gas en de elektriciteit voor {year} tegen de vaste prijs van vandaag "
        "(gas {g}, elektriciteit {p} per MWh). Dan zit u op {after} van het doel van {target}.",
        "nothing": "Deze maand niets te kopen: u heeft {b} gekocht, volgens plan ({p} gepland tot nu).",
        "finished": "Het inkoopseizoen voor {year} is voorbij: {b} van uw volume ligt vast (doel {target}).",
        "model": "Ter informatie: met de prijzen van vandaag zou de HedgeWise-ladder het doel op {m} zetten (uw "
        "afgesproken doel: {target}). Het plan verandert niet vanzelf; laten we het bespreken als het verschil blijft. "
        "Het slechte scenario is nooit lager dan een herhaling van de prijsschok van 2021->2022.",
        "changed_h": "Wat er veranderde sinds {prev}",
        "first": "Dit is de eerste update: vanaf volgende maand ziet u wat er veranderde.",
        "row_bad": "Slecht scenario {year} met wat u al heeft gekocht",
        "row_exp": "Verwachte rekening {year} met wat u al heeft gekocht",
        "row_gas": "Vaste gasprijs (EUR/MWh)",
        "row_power": "Vaste stroomprijs (EUR/MWh)",
        "flags_h": "Let op",
        "flag_bad_case": "Het slechte scenario veranderde {chg} sinds vorige maand ({old} -> {new}).",
        "flag_price": "De vaste {c}prijs veranderde {chg} sinds vorige maand ({old} -> {new} per MWh).",
        "flag_overdue": "Een geplande aankoop is te laat: {m} van uw volume had al vast moeten staan.",
        "progress_h": "Voortgang van het plan",
        "bought": "gekocht",
        "planned": "gepland tot nu",
        "target": "doel",
        "bad_hist": "Slecht scenario door de tijd",
        "now_vs": "Nu: verwacht {e}, slecht scenario {b}. Met het hele plan: verwacht {pe}, slecht scenario {pb}. "
        "Alles variabel: slecht scenario {v}.",
        "footer": "HedgeWise maandelijkse update. Alleen de kosten van de energie zelf; de vaste prijs is een "
        "benadering van termijnprijzen (recente marktprijs + 5%). De scenario's komen uit een statistisch model en "
        "kunnen gebeurtenissen missen die nog nooit voorkwamen. Beslisondersteuning, geen financieel advies.",
        "gas": "gas",
        "power": "stroom",
        "email_subject": "HedgeWise maandupdate {date}: {todo}",
        "email_buy": "deze maand {s} kopen",
        "email_nothing": "deze maand niets te kopen",
        "email_hello": "Goedendag,",
        "email_bye": "Met vriendelijke groet,\nHedgeWise",
        "email_attached": "De update van één pagina zit in de bijlage.",
    },
}


def _eur(x: float, lang: str) -> str:
    """A price per MWh, rounded: €96 (en) / € 96 (nl)."""
    return f"€{x:.0f}" if lang == "en" else f"€ {x:.0f}"


def _signed(x: float, lang: str) -> str:
    s = f"{x:+.0%}"
    return s.replace(".", ",") if lang == "nl" else s


def todo_text(res: dict, lang: str) -> str:
    """The 'what to do this month' sentence (Markdown bold)."""
    T, st, row = TXT[lang], res["status"], res["row"]
    if st["finished"]:
        return T["finished"].format(year=row["delivery_year"], b=pct(st["bought"]), target=pct(st["target"]))
    if st["buy_now"] > 1e-9:
        return T["buy"].format(
            s=pct(st["buy_now"]),
            year=row["delivery_year"],
            g=_eur(row["gas_fixed"], lang),
            p=_eur(row["power_fixed"], lang),
            after=pct(st["bought"] + st["buy_now"]),
            target=pct(st["target"]),
        )
    return T["nothing"].format(b=pct(st["bought"]), p=pct(st["planned"]))


def flag_texts(res: dict, lang: str) -> list[str]:
    """The flags as sentences."""
    T, out = TXT[lang], []
    for f in res["flags"]:
        if f["key"] == "bad_case":
            out.append(
                T["flag_bad_case"].format(
                    chg=_signed(f["change"], lang), old=money(f["old"], lang), new=money(f["new"], lang)
                )
            )
        elif f["key"] == "price":
            out.append(
                T["flag_price"].format(
                    c=T[f["commodity"]],
                    chg=_signed(f["change"], lang),
                    old=_eur(f["old"], lang),
                    new=_eur(f["new"], lang),
                )
            )
        else:
            out.append(T["flag_overdue"].format(m=pct(f["missing"])))
    return out


def email_draft(res: dict) -> str:
    """A short email DRAFT (plain text) in the client's language."""
    lang = res["client"]["language"]
    T, st, row = TXT[lang], res["status"], res["row"]
    todo = T["email_buy"].format(s=pct(st["buy_now"])) if st["buy_now"] > 1e-9 else T["email_nothing"]
    lines = [
        f"[{T['draft']}]",
        f"To: {res['client'].get('email', '')}",
        "Subject: " + T["email_subject"].format(date=row["run_date"], todo=todo),
        "",
        T["email_hello"],
        "",
        todo_text(res, lang).replace("**", ""),
        "",
        T["now_vs"].format(
            e=money(row["expected_now"], lang),
            b=money(row["bad_case_now"], lang),
            pe=money(row["expected_plan"], lang),
            pb=money(row["bad_case_plan"], lang),
            v=money(row["bad_case_variable"], lang),
        ),
    ]
    ft = flag_texts(res, lang)
    if ft:
        lines += ["", T["flags_h"] + ":"] + [f"- {x}" for x in ft]
    lines += ["", T["email_attached"], "", T["email_bye"]]
    return "\n".join(lines).replace(" ", " ") + "\n"


# --------------------------------------------------------------------------------------------
# The one-page PDF
# --------------------------------------------------------------------------------------------
def progress_chart(res: dict, lang: str, path: io.BytesIO) -> None:
    """Left: plan progress bar (bought vs planned vs target). Right: bad case over time."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    T, st, hist = TXT[lang], res["status"], res["history"]
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(8.2, 2.3), gridspec_kw={"width_ratios": [1.3, 1]})
    ax.barh([0], [1.0], color="#eef2f7", height=0.5)
    ax.barh([0], [st["planned"]], color="none", edgecolor="#2a6fdb", hatch="///", height=0.5, label=T["planned"])
    ax.barh([0], [st["bought"]], color="#1b9e5a", height=0.5, label=f"{T['bought']} {pct(st['bought'])}")
    ax.axvline(st["target"], color="#111827", lw=2, label=f"{T['target']} {pct(st['target'])}")
    ax.set_xlim(0, 1)
    ax.set_yticks([])
    ax.xaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    ax.legend(fontsize=7, frameon=False, ncol=3, loc="upper center", bbox_to_anchor=(0.5, -0.25))
    ax.set_title(T["progress_h"], fontsize=9)
    ax.spines[["top", "right", "left"]].set_visible(False)
    h = hist.sort_values("run_date")
    x = np.arange(len(h))  # one tick per monthly run, labelled with its date
    ax2.plot(x, h.bad_case_now, "o-", color="#b8433a", ms=4)
    from .moneyfmt import money_axis

    money_axis(ax2, h.bad_case_now, lang, "y", fontsize=7)
    ax2.set_xticks(x, [d[2:] for d in h.run_date], fontsize=7)  # 'yy-mm-dd'
    ax2.set_xlim(-0.5, max(len(h) - 0.5, 1.5))
    ax2.set_title(T["bad_hist"], fontsize=9)
    ax2.tick_params(labelsize=7)
    ax2.grid(alpha=0.25)
    ax2.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(path, format="png", dpi=200)
    plt.close(fig)


def make_update_pdf(res: dict, path: Path) -> None:
    """One A4 page in the client's language, in the style of the risk report."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    from . import report as rp

    lang = res["client"]["language"]
    T, row, prev = TXT[lang], res["row"], res["prev"]
    ss = {
        "title": ParagraphStyle("t", fontName=rp.FONT_BOLD, fontSize=16, leading=20, textColor=rp.INK),
        "sub": ParagraphStyle("s", fontName=rp.FONT, fontSize=8.5, leading=11, textColor=rp.MUTED),
        "h": ParagraphStyle(
            "h", fontName=rp.FONT_BOLD, fontSize=11, leading=14, textColor=rp.BRAND, spaceBefore=6, spaceAfter=2
        ),
        "body": ParagraphStyle("b", fontName=rp.FONT, fontSize=9.5, leading=13, textColor=rp.INK),
        "rec": ParagraphStyle("r", fontName=rp.FONT, fontSize=11, leading=15, textColor=rp.INK),
        "small": ParagraphStyle("sm", fontName=rp.FONT, fontSize=7.5, leading=9.5, textColor=rp.MUTED),
        "draft": ParagraphStyle(
            "d", fontName=rp.FONT_BOLD, fontSize=8.5, leading=11, textColor=colors.HexColor("#b8433a")
        ),
    }
    W = A4[0] - 3.6 * cm
    md = lambda s: re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", rp._esc(s))  # noqa: E731
    story = [
        Paragraph(T["draft"], ss["draft"]),
        Paragraph(rp._esc(f"{T['title']} · {res['client']['name']}"), ss["title"]),
        Paragraph(
            T["subtitle"].format(date=row["run_date"], data=row["data_date"], year=row["delivery_year"]), ss["sub"]
        ),
        Paragraph(T["todo_h"], ss["h"]),
    ]
    todo = [
        Paragraph(md(todo_text(res, lang)), ss["rec"]),
        Spacer(1, 3),
        Paragraph(
            md(
                T["now_vs"].format(
                    e=money(row["expected_now"], lang),
                    b=money(row["bad_case_now"], lang),
                    pe=money(row["expected_plan"], lang),
                    pb=money(row["bad_case_plan"], lang),
                    v=money(row["bad_case_variable"], lang),
                )
            ),
            ss["body"],
        ),
        Paragraph(md(T["model"].format(m=pct(row["model_target"]), target=pct(row["target_share"]))), ss["small"]),
    ]
    box = Table([[todo]], colWidths=[W])
    box.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), rp.SOFT),
                ("LINEBEFORE", (0, 0), (0, -1), 4, rp.BRAND),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
            ]
        )
    )
    story.append(box)
    ft = flag_texts(res, lang)
    if ft:
        story.append(Paragraph(T["flags_h"], ss["h"]))
        story += [Paragraph("• " + rp._esc(x), ss["body"]) for x in ft]
    if prev is None:
        story += [Paragraph(T["changed_h"].format(prev="-"), ss["h"]), Paragraph(T["first"], ss["body"])]
    else:
        story.append(Paragraph(T["changed_h"].format(prev=prev["run_date"]), ss["h"]))
        rows = [["", prev["run_date"], row["run_date"], ""]]
        for key, label, fmt in (
            ("bad_case_now", T["row_bad"], lambda v: money(v, lang)),
            ("expected_now", T["row_exp"], lambda v: money(v, lang)),
            ("gas_fixed", T["row_gas"], lambda v: f"{v:.1f}"),
            ("power_fixed", T["row_power"], lambda v: f"{v:.1f}"),
        ):
            rows.append(
                [
                    label.format(year=row["delivery_year"]),
                    fmt(prev[key]),
                    fmt(row[key]),
                    _signed(row[key] / prev[key] - 1, lang),
                ]
            )
        tb = Table(rows, colWidths=[W * 0.52, W * 0.16, W * 0.16, W * 0.16])
        tb.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, -1), rp.FONT),
                    ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                    ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                    ("TEXTCOLOR", (0, 0), (-1, 0), rp.MUTED),
                    ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#d1d5db")),
                ]
            )
        )
        story.append(tb)
    buf = io.BytesIO()
    progress_chart(res, lang, buf)
    buf.seek(0)
    story += [Spacer(1, 8), Image(buf, width=W, height=W * 2.3 / 8.2)]
    story += [Spacer(1, 10), Paragraph(T["footer"] + "<br/>" + rp._esc(one_line()), ss["small"])]
    doc = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        leftMargin=1.8 * cm,
        rightMargin=1.8 * cm,
        topMargin=1.6 * cm,
        bottomMargin=1.4 * cm,
        title=f"HedgeWise update - {res['client']['name']}",
        author="HedgeWise",
        initialFontName=rp.FONT,
        invariant=True,
    )

    def bar(canv, _doc) -> None:  # noqa: ANN001
        canv.saveState()
        canv.setFillColor(rp.BRAND)
        canv.rect(0, A4[1] - 1.1 * cm, A4[0], 1.1 * cm, fill=1, stroke=0)
        canv.setFillColor(colors.white)
        canv.setFont(rp.FONT_BOLD, 13)
        canv.drawString(1.8 * cm, A4[1] - 0.75 * cm, "HedgeWise")
        canv.setFont(rp.FONT, 8.5)
        canv.drawRightString(A4[0] - 1.8 * cm, A4[1] - 0.72 * cm, T["title"])
        canv.restoreState()

    doc.build(story, onFirstPage=bar, onLaterPages=bar)
    if doc.page != 1:
        raise RuntimeError(f"monthly update ran over one page ({doc.page})")


# --------------------------------------------------------------------------------------------
# All clients
# --------------------------------------------------------------------------------------------
def refresh_prices(timeout: int = 900) -> str:
    """Run download_data.py; on any failure (offline, rate limit) keep the existing data and say so."""
    try:
        r = subprocess.run(
            [sys.executable, str(ROOT / "download_data.py")], cwd=ROOT, capture_output=True, text=True, timeout=timeout
        )
        return "prices refreshed" if r.returncode == 0 else f"download failed, using existing data ({r.stderr[-200:]})"
    except (OSError, subprocess.SubprocessError) as e:
        return f"download skipped, using existing data ({e})"


def main(argv: list[str] | None = None) -> pd.DataFrame:
    """Run the update for every clients/*.yaml (and clients/private/*.yaml) and write the overview table."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--clients", default=str(ROOT / "clients"), help="folder with one YAML per client")
    ap.add_argument("--no-download", action="store_true", help="don't refresh prices first")
    ap.add_argument("--as-of", help="pretend today is this date (tests, re-runs)")
    args = ap.parse_args(argv)
    if not args.no_download:
        print(refresh_prices())
    df = hw.load_prices()
    folder = Path(args.clients)
    files = sorted(folder.glob("*.yaml")) + sorted((folder / "private").glob("*.yaml"))
    rows = []
    for f in files:
        client = load_client(f)
        r = run_client(client, df, args.as_of)
        st, row = r["status"], r["row"]
        rows.append(
            {
                "client": client["name"],
                "language": client["language"],
                "delivery_year": row["delivery_year"],
                "bought": st["bought"],
                "planned": st["planned"],
                "target": st["target"],
                "buy_now": st["buy_now"],
                "bad_case_now": row["bad_case_now"],
                "bad_case_prev": None if r["prev"] is None else r["prev"]["bad_case_now"],
                "attention": " | ".join(flag_texts(r, "en")),
                "pdf": str(r["files"][0].relative_to(ROOT))
                if r["files"][0].is_relative_to(ROOT)
                else str(r["files"][0]),
            }
        )
        print(f"{client['name']}: {todo_text(r, 'en').replace('**', '')}")
        for x in flag_texts(r, "en"):
            print("  FLAG:", x)
    overview = pd.DataFrame(rows)
    if len(overview):
        date = rows and pd.Timestamp(args.as_of or df.index.max()).date()
        UPDATES.mkdir(parents=True, exist_ok=True)
        write_csv(overview, UPDATES / f"overview_{date}.csv", index=False)
        print(f"Overview: {UPDATES / f'overview_{date}.csv'} (DRAFTS: check before sending anything)")
    return overview


__all__ = [
    "load_client",
    "plan_status",
    "position_costs",
    "run_client",
    "flags",
    "todo_text",
    "flag_texts",
    "email_draft",
    "make_update_pdf",
    "refresh_prices",
    "main",
]
