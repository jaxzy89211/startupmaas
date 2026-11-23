"""
HedgeWise live demo  -  run with:   streamlit run app.py
Consumption in  ->  worst-case bill and recommendation out.

Every text is in English and Dutch (hedgewise/app_text.py; the switch is at the top of the sidebar).
"Interview mode" hides the model settings and walks through: company name -> energy use -> past contracts
(optional) -> results -> PDF.
"""

import io
import threading

import numpy as np
import pandas as pd
import streamlit as st

import hedgewise as hw
from hedgewise import audit as au
from hedgewise import company_input as ci
from hedgewise import green as gr
from hedgewise.app_text import UI, eur_m, number, recommendation_md, sector_label, strategy_table, warning_text
from hedgewise.naming import report_path
from hedgewise.settings import APP, DEFAULT_RISK_AVERSION, DEFAULT_SEED
from hedgewise.texts import TEXT as REPORT_TEXT

st.set_page_config(page_title="HedgeWise", page_icon="⚡", layout="wide")

# matplotlib's pyplot keeps global state and is not thread-safe; Streamlit runs every browser session in its own
# thread, so two people drawing a chart at the same moment could get each other's axes. One chart at a time.
PLOT_LOCK = threading.Lock()


# --------------------------------------------------------------------------------------------
# Cached computations
# --------------------------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def load() -> pd.DataFrame:
    """Price history from data/ (cached for all sessions)."""
    return hw.load_prices()


@st.cache_resource(show_spinner=False)
def fitted(as_of: str) -> hw.FittedModel:
    """Model fitted on data up to as_of (cached)."""
    return hw.fit(load(), as_of)


@st.cache_resource(show_spinner=False)
def sims(as_of: str, delivery_start: str, n: int, seed: int) -> dict:
    """Simulated price paths to the end of the delivery year (cached)."""
    m = fitted(as_of)
    end = pd.Timestamp(delivery_start) + pd.DateOffset(years=1) - pd.Timedelta(days=1)
    return hw.simulate(m, end, n, seed)


@st.cache_data(show_spinner=False, max_entries=64)
def analysis_png(key: tuple, _a: hw.Analysis) -> bytes:
    """The 4-panel analysis chart as PNG bytes, cached per input combination (`key`; `_a` is not hashed)."""
    buf = io.BytesIO()  # in memory: no shared file when several people use the demo at once
    with PLOT_LOCK:
        hw.plot_analysis(_a, buf)
    return buf.getvalue()


@st.cache_data(show_spinner=False, max_entries=64)
def frontier_png(key: tuple, lang: str, _a: hw.Analysis, extra: tuple = ()) -> bytes:
    """The trade-off chart in the chosen language (with green-investment points if any)."""
    buf = io.BytesIO()
    with PLOT_LOCK:
        hw.plot_frontier(_a, path=buf, text=REPORT_TEXT[lang]["chart"], extra_points=list(extra) or None)
    return buf.getvalue()


@st.cache_data(show_spinner=False, max_entries=64)
def stress_png(key: tuple, lang: str, _stress: dict) -> bytes:
    """The stress-scenario chart as PNG bytes, cached per input combination and language."""
    buf = io.BytesIO()
    with PLOT_LOCK:
        hw.plot_stress(_stress, buf, lang=lang)
    return buf.getvalue()


@st.cache_data(show_spinner=False, max_entries=16)
def audit_strategies(gas_m3: tuple, power_kwh: tuple, last_year: int) -> pd.DataFrame:
    """The comparison strategies for this consumption profile (slow: re-fits at every decision date; cached)."""
    comp = hw.Company(name="audit", gas_m3=gas_m3, power_kwh=power_kwh)
    return au.strategy_costs(comp, last_year, load())


def report_pdf(
    a: hw.Analysis, lang: str, stress: dict | None, audit: object | None = None, green: pd.DataFrame | None = None
) -> bytes:
    """The PDF. Called only when someone clicks "Download" (Streamlit runs it on a separate thread, outside the
    page script, so no Streamlit calls or caches in here); reportlab is imported only then."""
    from hedgewise import report

    buf = io.BytesIO()
    with PLOT_LOCK:  # the PDF draws its charts with matplotlib too
        report.make_report(a, buf, lang, stress=stress, audit=audit, green=green)
    return buf.getvalue()


# --------------------------------------------------------------------------------------------
# Building blocks (used by the full dashboard and by interview mode)
# --------------------------------------------------------------------------------------------
def consumption_input(T: dict, name: str) -> tuple[hw.Company | None, list[str]]:
    """Yearly totals + sector, or an uploaded bill file with the detected columns. (None, []) = waiting for a file."""
    source = st.radio(T["source"], [T["src_yearly"], T["src_upload"]], help=T["source_help"], key="source")
    sector = st.selectbox(
        T["sector"], ci.SECTORS, help=T["sector_help"], key="sector", format_func=lambda x: sector_label(x, LANG)
    )
    if source == T["src_upload"]:
        up = st.file_uploader(T["upload"], type=["csv", "txt", "xlsx", "xls"], key="bills")
        if up is None:
            st.info(T["upload_wait"])
            return None, []
        try:
            table = ci.read_table(up)
        except Exception as e:  # noqa: BLE001
            st.error(T["read_error"].format(e=e))
            return None, []
        guess = ci.detect_columns(table)
        cols, none = list(table.columns), T["none"]
        st.caption(T["check_cols"])
        dcol = st.selectbox(T["col_date"], cols, index=cols.index(guess["date"]) if guess["date"] else 0)
        gcol = st.selectbox(
            T["col_gas"], [none] + cols, index=([none] + cols).index(guess["gas"]) if guess["gas"] else 0
        )
        gunit = st.selectbox(T["unit_gas"], ci.GAS_UNITS, index=ci.GAS_UNITS.index(guess["gas_unit"]))
        pcol = st.selectbox(
            T["col_power"], [none] + cols, index=([none] + cols).index(guess["power"]) if guess["power"] else 0
        )
        punit = st.selectbox(T["unit_power"], ci.POWER_UNITS, index=ci.POWER_UNITS.index(guess["power_unit"]))
        mapping = {
            "date": dcol,
            "gas": None if gcol == none else gcol,
            "power": None if pcol == none else pcol,
            "gas_unit": gunit,
            "power_unit": punit,
        }
        try:
            res = ci.company_from_frame(table, mapping, name, sector)
        except ValueError as e:
            st.error(str(e))
            return None, []
        with st.expander(T["preview"]):
            st.dataframe(table.head(15), width="stretch")
    else:
        gas = st.number_input(T["gas_year"], 0.0, 1e9, 1_200_000.0, step=50_000.0, format="%.0f", key="gas")
        gunit = st.selectbox(T["unit_gas"], ci.GAS_UNITS, index=0, key="gunit")
        power = st.number_input(T["power_year"], 0.0, 1e9, 5_000.0, step=250.0, format="%.0f", key="power")
        punit = st.selectbox(T["unit_power"], ci.POWER_UNITS, index=1, key="punit")
        try:
            res = ci.from_yearly(gas, gunit, power, punit, sector, name)
        except ValueError as e:
            st.error(str(e))
            return None, []
    company = res.company
    st.success(
        T["volumes"].format(gas=number(sum(company.gas_m3), LANG), power=number(sum(company.power_kwh) / 1e3, LANG))
    )
    for n_ in res.warnings:
        st.warning(warning_text(n_, LANG))
    return company, res.warnings


def run_analysis(
    company: hw.Company, T: dict, as_of: str, n: int, lam: float, budget: float | str | None, allow_timing: bool
) -> tuple:
    """Fit, simulate, analyse (all cached where possible). Returns (model, analysis, stress, cache key)."""
    ds = str(pd.Timestamp(year=pd.Timestamp(as_of).year + 1, month=1, day=1).date())
    with st.spinner(T["spinner"]):
        m = fitted(as_of)
        s = sims(as_of, ds, n, DEFAULT_SEED)
        a = hw.analyse(m, company, ds, n, DEFAULT_SEED, lam, sims=s, allow_timing=allow_timing, budget=budget)
    try:
        stress = hw.stress_test(a, load())
    except ValueError:
        stress = None
    key = (as_of, ds, n, lam, budget, allow_timing, company.name, company.gas_m3, company.power_kwh)
    return m, a, stress, key


def show_headline(a: hw.Analysis, T: dict, lang: str) -> None:
    """Four metrics and the advice in words."""
    var_row, hw_row = a.table.loc["All variable"], a.table.iloc[-1]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(T["m_expected"], eur_m(var_row.expected, lang))
    c2.metric(T["m_bad"], eur_m(var_row.es95, lang), help=T["m_bad_help"])
    c3.metric(
        T["m_bad_hw"],
        eur_m(hw_row.es95, lang),
        delta="-" + eur_m(var_row.es95 - hw_row.es95, lang),
        delta_color="inverse",
    )
    rg, rp = a.recommendation["hedge_gas"], a.recommendation["hedge_power"]
    c4.metric(
        T["m_share"], f"{rg:.0%}" if np.isclose(rg, rp) else T["share_split"].format(g=f"{rg:.0%}", p=f"{rp:.0%}")
    )
    st.info(recommendation_md(a, lang))


def show_stress(stress: dict | None, key: tuple, T: dict, lang: str) -> None:
    """The 2022 stress scenarios: chart and one line each."""
    if not stress:
        return
    st.subheader(T["stress_h"])
    sc1, sc2 = st.columns([3, 2])
    sc1.image(stress_png(key, lang, stress), width="stretch")
    for sc in stress["scenarios"]:
        b, p = sc["bills"], sc["avg_price"]
        sc2.markdown(
            T["stress_line"].format(
                label=sc["label"][lang],
                g=p["gas"],
                p=p["power"],
                v=eur_m(b["All variable"], lang),
                h=eur_m(b["HedgeWise"], lang),
                f=eur_m(b["Fix 100% now"], lang),
            )
        )
    sc2.caption(T["stress_note"])


def audit_ui(company: hw.Company, T: dict, lang: str) -> None:
    """Past contracts: editable table (or upload), comparison with the strategies, sentence, chart."""
    st.markdown(T["audit_intro"])
    cols = T["audit_cols"]
    example = au.read_contracts(hw.DATA / "audit_example.csv")
    default_rows = pd.DataFrame(
        [
            {
                cols["year"]: k.year,
                cols["commodity"]: k.commodity,
                cols["share_%"]: round(k.share * 100) if k.price_eur_mwh is not None else None,
                cols["price"]: k.price_entered.split(" ")[0],
                cols["unit"]: k.price_entered.split(" ", 1)[1] if " " in k.price_entered else "",
                cols["fixed_on"]: "" if k.fixed_on is None else str(k.fixed_on.date()),
            }
            for k in example
        ]
    )
    up_c = st.file_uploader(T["audit_upload"], type=["csv", "txt", "xlsx", "xls"], key="audit_upload")
    if up_c is not None:
        try:
            default_rows = ci.read_table(up_c)
        except Exception as e:  # noqa: BLE001
            st.error(str(e))
    edited = st.data_editor(
        default_rows,
        num_rows="dynamic",
        width="stretch",
        key=f"audit_table_{lang}",
        column_config={
            cols["commodity"]: st.column_config.SelectboxColumn(options=["gas", "power"]),
            cols["unit"]: st.column_config.SelectboxColumn(options=au.PRICE_UNITS + [""]),
        },
    )
    if st.button(T["audit_run"], type="primary"):
        st.session_state["audit_go"] = True
    if not st.session_state.get("audit_go"):
        return
    try:
        tbl = edited.rename(columns={v: k for k, v in cols.items()}).copy()
        if "share_%" in tbl:  # the editor's column is in percent; an uploaded table keeps its own format
            tbl["share"] = [None if pd.isna(v) or v == "" else f"{v}%" for v in tbl.pop("share_%")]
        contracts = au.read_contracts(tbl)
        years = au.audited_years(contracts, load())
    except ValueError as e:
        st.error(str(e))
        return
    with st.spinner(T["audit_wait"]):
        strat = audit_strategies(company.gas_m3, company.power_kwh, max(years))
    r = au.run_audit(contracts, company, load(), strategies=strat)
    st.success(au.sentence(r, lang))
    show = (r.table / 1e3).round(0).astype(int)
    show.loc[T["audit_total"]] = (r.table.sum() / 1e3).round(0).astype(int)
    show.loc[T["audit_worst"]] = (r.table.max() / 1e3).round(0).astype(int)
    show.columns = [au.label(c, lang) for c in show.columns]
    st.subheader(T["audit_table"])
    st.dataframe(show, width="stretch")
    buf = io.BytesIO()
    with PLOT_LOCK:
        au.plot_audit(r, buf, lang)
    st.image(buf.getvalue(), width="stretch")
    st.caption(T["audit_note"])
    with st.expander(T["audit_detail"]):
        st.dataframe(r.detail.round(2), width="stretch", hide_index=True)
        if r.contracts.fixed_on.notna().any():
            st.caption(T["audit_proxy"])
            st.dataframe(r.contracts.dropna(subset=["fixed_on"]).round(1), width="stretch", hide_index=True)
    st.session_state["audit_result"] = ((company.gas_m3, company.power_kwh), r)


def pdf_button(
    container: object,
    a: hw.Analysis,
    m: hw.FittedModel,
    company: hw.Company,
    stress: dict | None,
    green_t: pd.DataFrame | None,
    T: dict,
    rep_lang: str,
) -> None:
    """Download button for the PDF, with page 2 (past contracts) when an audit was run for this profile."""
    prev = st.session_state.get("audit_result")
    audit_page = None
    if prev is not None and prev[0] == (company.gas_m3, company.power_kwh) and container.checkbox(T["add_audit"], True):
        audit_page = prev[1]
    container.download_button(
        T["download"],
        lambda: report_pdf(a, rep_lang, stress, audit_page, green_t),  # built on click, not on every rerun
        type="primary",
        file_name=report_path(company.name, m.as_of).name,
        mime="application/pdf",
    )


# --------------------------------------------------------------------------------------------
# Page
# --------------------------------------------------------------------------------------------
df = load()
# Links can preset the language and the mode, e.g. https://<app>/?lang=nl&mode=interview (TEAM_GUIDE.md);
# &contracts=example also ticks step 3 and runs the example contracts (for demos and screenshots).
qp = st.query_params
with st.sidebar:
    lang = st.radio(
        UI["en"]["lang"], ["EN", "NL"], index=int(qp.get("lang", "en").lower() == "nl"), horizontal=True, key="lang"
    ).lower()
    LANG = lang  # used by the building blocks above
    T = UI[lang]
    interview = st.toggle(
        T["interview"], value=qp.get("mode") == "interview", key="interview", help=T["interview_help"]
    )
if qp.get("contracts") == "example":
    st.session_state.setdefault("audit_go", True)
st.title("⚡ HedgeWise")
st.caption(T["caption"].format(date=f"{df.index.max():%d-%m-%Y}"))

if interview:
    # ---------------- interview mode: 5 steps, no model settings ----------------
    st.sidebar.info(T["interview_on"])
    st.subheader(T["step1"])
    name = st.text_input(T["name"], "", key="iv_name") or "Company"
    st.subheader(T["step2"])
    st.caption(T["step2_help"])
    company, _ = consumption_input(T, name)
    if company is None:
        st.info(T["step_wait"])
        st.stop()
    st.subheader(T["step3"])
    if st.checkbox(T["step3_check"], value=qp.get("contracts") == "example", key="iv_audit"):
        audit_ui(company, T, lang)
    st.subheader(T["step4"])
    as_of = str(df.index.max().date())
    m, a, stress, key = run_analysis(company, T, as_of, APP["default_scenarios"], DEFAULT_RISK_AVERSION, None, False)
    show_headline(a, T, lang)
    st.image(frontier_png(key, lang, a), width="stretch")
    show_stress(stress, key, T, lang)
    st.subheader(T["step5"])
    st.caption(T["pdf_ready"])
    pdf_button(st, a, m, company, stress, None, T, lang)
    st.stop()

# ---------------- full dashboard ----------------
with st.sidebar:
    st.header(T["company_h"])
    name = st.text_input(T["name"], "Limburg food processor", key="name")
    company, _ = consumption_input(T, name)
    if company is None:
        st.stop()
    st.header(T["green_h"])
    g_solar = st.number_input(T["solar"], 0, 20_000, 0, step=100, key="g_solar")
    g_batt = st.number_input(T["battery"], 0, 50_000, 0, step=250, key="g_batt")
    g_bkw = st.number_input(T["battery_kw"], 0, 50_000, 0, step=250, key="g_bkw")
    g_ppa = st.slider(T["ppa"], 0, 100, 0, 5, key="g_ppa")
    g_ppa_price = st.number_input(T["ppa_price"], 0.0, 1000.0, 0.0, step=5.0, key="g_ppa_price")
    g_ppa_years = st.number_input(T["ppa_years"], 1, 25, 10, key="g_ppa_years")
    inv = gr.Investments(
        solar_kwp=float(g_solar),
        battery_kwh=float(g_batt),
        battery_kw=float(g_bkw) or None,
        ppa_share=g_ppa / 100,
        ppa_price=g_ppa_price or None,
        ppa_years=int(g_ppa_years),
    )

    st.header(T["decision_h"])
    dates = {
        T["date_today"]: str(df.index.max().date()),
        T["date_2021"]: "2021-06-30",
        T["date_2022"]: "2022-06-30",
        T["date_custom"]: None,
    }
    choice = st.selectbox(T["date"], list(dates), key="date")
    as_of = dates[choice] or str(
        st.date_input(
            T["as_of"], pd.Timestamp("2021-09-30"), min_value=pd.Timestamp("2020-01-31"), max_value=df.index.max()
        )
    )
    mode = st.radio(T["goal"], [T["goal_balance"], T["goal_budget"]], key="goal")
    if mode == T["goal_balance"]:
        lam = st.slider(T["risk"], 0.0, 1.0, DEFAULT_RISK_AVERSION, 0.1, help=T["risk_help"], key="risk")
        budget = None
    else:
        lam = DEFAULT_RISK_AVERSION
        b = st.number_input(T["budget"], 0, 100_000_000, 0, step=50_000, key="budget")
        budget = "auto" if b == 0 else b
    allow_timing = st.checkbox(T["timing"], False, key="timing")
    n = st.select_slider(T["scenarios"], APP["scenario_choices"], APP["default_scenarios"], key="n")

tab_today, tab_audit = st.tabs([T["tab_today"], T["tab_audit"]])

with tab_today:
    m, a, stress, key = run_analysis(company, T, as_of, n, lam, budget, allow_timing)
    var_row = a.table.loc["All variable"]
    green_t = gr.evaluate(a, inv)[0] if inv.any() else None
    show_headline(a, T, lang)
    rc1, rc2, _ = st.columns([1, 2, 3])
    rep_lang = rc1.radio(T["report_lang"], ["EN", "NL"], index=["en", "nl"].index(lang), horizontal=True).lower()
    pdf_button(rc2, a, m, company, stress, green_t, T, rep_lang)

    extra = ()
    if green_t is not None:
        extra = tuple(
            (T["plan_plus"].format(inv=r.investment if lang == "en" else r.label_nl), r.es95, r.expected)
            for r in green_t.itertuples()
        )
    st.image(frontier_png(key, lang, a, extra), width="stretch")
    st.caption(T["frontier_caption"] + hw.frontier_scale_sentence(a, lang))
    with st.expander(T["more_charts"]):
        st.image(analysis_png(key, a), width="stretch")

    show_stress(stress, key, T, lang)

    st.subheader(T["strategies_h"])
    st.dataframe(strategy_table(a, lang), width="stretch")

    ds = a.delivery_start
    if ds + pd.DateOffset(years=1) <= df.index.max():
        real = {c: df.loc[ds : f"{ds.year}-12-31", c] for c in ("gas", "power")}
        rb = sum(
            float(v @ real[c].groupby(real[c].index.month).mean().values)
            for c, v in (("gas", company.gas_mwh), ("power", company.power_mwh))
        )
        st.warning(
            T["what_happened"].format(
                y=ds.year, r=eur_m(rb, lang), e=eur_m(var_row.expected, lang), b=eur_m(var_row.es95, lang)
            )
        )

    st.subheader(T["green_section"])
    if green_t is None:
        st.info(T["green_none"])
    else:
        st.markdown(gr.summary_text(green_t, lang))
        cols = gr.TABLE_COLUMNS[lang]
        show = (
            green_t.assign(name=green_t.investment if lang == "en" else green_t.label_nl)
            .set_index("name")[list(cols)]
            .rename(columns=cols)
        )
        st.dataframe(
            show.style.format(
                {c: "{:,.0f}" for c in list(cols.values())[:5]}
                | {c: (lambda v: "–" if v is None or pd.isna(v) else f"{v:.1f}") for c in list(cols.values())[5:]}
            ),
            width="stretch",
        )
        with st.expander(T["assumptions"]):
            for line in gr.assumptions_text(lang):
                st.markdown("- " + line)

    with st.expander(T["model_details"]):
        st.code(hw.describe(m))
        src = a.recommendation["price_source"]
        st.markdown(T["fixed_used"] + "; ".join(f"{k} €{a.fwd_now[k]:.1f}/MWh ({v})" for k, v in src.items()))
        st.caption(T["model_note"])

with tab_audit:
    audit_ui(company, T, lang)
