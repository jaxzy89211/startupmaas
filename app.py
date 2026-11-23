"""
HedgeWise live demo  -  run with:   streamlit run app.py
Consumption in  ->  worst-case bill and recommendation out.
"""

import io

import numpy as np
import pandas as pd
import streamlit as st

import hedgewise as hw
from hedgewise import company_input as ci
from hedgewise import report
from hedgewise.settings import APP, DEFAULT_RISK_AVERSION, DEFAULT_SEED

st.set_page_config(page_title="HedgeWise", page_icon="⚡", layout="wide")


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


df = load()
st.title("⚡ HedgeWise")
st.caption(
    "How bad can next year's energy bill get, and how much should you fix? "
    f"Prices: Dutch day-ahead power + TTF gas up to {df.index.max():%d %b %Y}. "
    "Power data: Energy-Charts (CC BY 4.0, Bundesnetzagentur | SMARD.de)."
)

with st.sidebar:
    st.header("Your company")
    name = st.text_input("Name", "Limburg food processor")
    source = st.radio(
        "Consumption data",
        ["Yearly totals + sector", "Upload bills (CSV / Excel)"],
        help="Upload 12-24 months of bills or a supplier-portal export, or just enter yearly totals.",
    )
    sector = st.selectbox(
        "Sector (seasonal profile)",
        ci.SECTORS,
        help="Used to spread yearly totals over the months and to fill 1-3 missing months. "
        "The profiles are assumptions.",
    )
    company, notes = None, []
    if source.startswith("Upload"):
        up = st.file_uploader("Monthly bills or portal export", type=["csv", "txt", "xlsx", "xls"])
        if up is not None:
            try:
                table = ci.read_table(up)
            except Exception as e:  # noqa: BLE001
                st.error(f"Could not read this file: {e}")
                st.stop()
            guess = ci.detect_columns(table)
            cols = list(table.columns)
            none = "(none)"
            st.caption("Check the columns HedgeWise found:")
            dcol = st.selectbox("Month / date column", cols, index=cols.index(guess["date"]) if guess["date"] else 0)
            gcol = st.selectbox(
                "Gas column", [none] + cols, index=([none] + cols).index(guess["gas"]) if guess["gas"] else 0
            )
            gunit = st.selectbox("Gas unit", ci.GAS_UNITS, index=ci.GAS_UNITS.index(guess["gas_unit"]))
            pcol = st.selectbox(
                "Power column", [none] + cols, index=([none] + cols).index(guess["power"]) if guess["power"] else 0
            )
            punit = st.selectbox("Power unit", ci.POWER_UNITS, index=ci.POWER_UNITS.index(guess["power_unit"]))
            mapping = {
                "date": dcol,
                "gas": None if gcol == none else gcol,
                "power": None if pcol == none else pcol,
                "gas_unit": gunit,
                "power_unit": punit,
            }
            try:
                res = ci.company_from_frame(table, mapping, name, sector)
                company, notes = res.company, res.warnings
            except ValueError as e:
                st.error(str(e))
                st.stop()
            with st.expander("Preview of the file"):
                st.dataframe(table.head(15), width="stretch")
        else:
            st.info("Upload a file, or switch to yearly totals.")
            st.stop()
    else:
        gas = st.number_input("Gas per year", 0.0, 1e9, 1_200_000.0, step=50_000.0, format="%.0f")
        gunit = st.selectbox("Gas unit", ci.GAS_UNITS, index=0)
        power = st.number_input("Electricity per year", 0.0, 1e9, 5_000.0, step=250.0, format="%.0f")
        punit = st.selectbox("Electricity unit", ci.POWER_UNITS, index=1)
        try:
            res = ci.from_yearly(gas, gunit, power, punit, sector, name)
        except ValueError as e:
            st.error(str(e))
            st.stop()
        company, notes = res.company, res.warnings
    st.success(f"{sum(company.gas_m3):,.0f} m³ gas · {sum(company.power_kwh) / 1e3:,.0f} MWh power per year")
    for n_ in notes:
        st.warning(n_)
    company.solar_kwp = st.slider(
        "Rooftop solar (kWp)",
        0,
        5000,
        0,
        step=100,
        help=f"~{hw.SOLAR_YIELD_KWH_PER_KWP} kWh per kWp per year, all self-consumed",
    )

    st.header("Decision")
    scenario = st.selectbox(
        "Decision date", ["Today", "30 Jun 2021 (before the crisis)", "30 Jun 2022 (mid-crisis)", "Custom"]
    )
    as_of = {
        "Today": str(df.index.max().date()),
        "30 Jun 2021 (before the crisis)": "2021-06-30",
        "30 Jun 2022 (mid-crisis)": "2022-06-30",
    }.get(scenario)
    if as_of is None:
        as_of = str(
            st.date_input(
                "As of", pd.Timestamp("2021-09-30"), min_value=pd.Timestamp("2020-01-31"), max_value=df.index.max()
            )
        )
    mode = st.radio("Goal", ["Balance cost and risk", "Stay within a budget"])
    if mode == "Balance cost and risk":
        lam, budget = (
            st.slider(
                "Risk aversion",
                0.0,
                1.0,
                DEFAULT_RISK_AVERSION,
                0.1,
                help="0 = only care about the average bill, 1 = care a lot about bad years. "
                "The chart 'Pick your trade-off' shows every other choice.",
            ),
            None,
        )
    else:
        lam = DEFAULT_RISK_AVERSION
        b = st.number_input("Energy budget (EUR, 0 = last 12 months + 20%)", 0, 100_000_000, 0, step=50_000)
        budget = "auto" if b == 0 else b
    allow_timing = st.checkbox("Allow fixing everything at once (market timing)", False)
    n = st.select_slider("Scenarios", APP["scenario_choices"], APP["default_scenarios"])

ds = str(pd.Timestamp(year=pd.Timestamp(as_of).year + 1, month=1, day=1).date())
with st.spinner("Fitting GARCH-t model and simulating price scenarios ..."):
    m = fitted(as_of)
    s = sims(as_of, ds, n, DEFAULT_SEED)
    a = hw.analyse(m, company, ds, n, DEFAULT_SEED, lam, sims=s, allow_timing=allow_timing, budget=budget)

var_row, hw_row = a.table.loc["All variable"], a.table.iloc[-1]
try:
    stress = hw.stress_test(a, df)
except ValueError:
    stress = None
c1, c2, c3, c4 = st.columns(4)
c1.metric("Expected bill, all variable", f"€{var_row.expected / 1e6:.2f}m")
c2.metric("Bad case (avg worst 5%), all variable", f"€{var_row.es95 / 1e6:.2f}m")
c3.metric(
    "Bad case with HedgeWise",
    f"€{hw_row.es95 / 1e6:.2f}m",
    delta=f"-€{(var_row.es95 - hw_row.es95) / 1e6:.2f}m",
    delta_color="inverse",
)
rg, rp = a.recommendation["hedge_gas"], a.recommendation["hedge_power"]
c4.metric("Recommended share to fix", f"{rg:.0%}" if np.isclose(rg, rp) else f"gas {rg:.0%} · power {rp:.0%}")

st.info(hw.recommendation_text(a).replace("\n", "  \n"))

rc1, rc2, _ = st.columns([1, 2, 3])
rep_lang = rc1.radio("Report language", ["EN", "NL"], horizontal=True)
pdf = io.BytesIO()
try:
    report.make_report(a, pdf, rep_lang.lower(), stress=stress)
    rc2.download_button(
        "Download report (PDF)",
        pdf.getvalue(),
        type="primary",
        file_name=report.report_path(company.name, m.as_of).name,
        mime="application/pdf",
    )
except Exception as e:  # noqa: BLE001  (never let the report break the demo)
    rc2.error(f"Report could not be made: {e}")

buf = io.BytesIO()  # in memory: no shared file when several people use the demo at once
hw.plot_analysis(a, buf)
st.image(buf.getvalue(), width="stretch")
st.caption("Bottom left, the trade-off chart: " + hw.frontier_scale_sentence(a, "en"))

if stress:
    st.subheader("What if 2022 happens again?")
    sbuf = io.BytesIO()
    hw.plot_stress(stress, sbuf)
    sc1, sc2 = st.columns([3, 2])
    sc1.image(sbuf.getvalue(), width="stretch")
    for sc in stress["scenarios"]:
        b, p = sc["bills"], sc["avg_price"]
        sc2.markdown(
            f"**{sc['label']['en']}** (gas avg €{p['gas']:.0f}, power €{p['power']:.0f}/MWh): "
            f"all variable €{b['All variable'] / 1e6:.2f}m · HedgeWise plan €{b['HedgeWise'] / 1e6:.2f}m · "
            f"fix 100% €{b['Fix 100% now'] / 1e6:.2f}m"
        )
    sc2.caption(
        "Historical stress scenarios, not forecasts. (a) The realised 2022 monthly prices exactly as they were. "
        "(b) Today's fixed price × (2022 average / 2021 average) per commodity, with the 2022 monthly shape. "
        "The hedged share is priced at today's fixed price."
    )

st.subheader("Strategy comparison (EUR k)")
st.dataframe((a.table / 1e3).round(0).astype(int), width="stretch")

if pd.Timestamp(ds) + pd.DateOffset(years=1) <= df.index.max():
    real = {c: df.loc[ds : str(pd.Timestamp(ds).year) + "-12-31", c] for c in ("gas", "power")}
    rb = sum(
        float(v @ real[c].groupby(real[c].index.month).mean().values)
        for c, v in (("gas", company.gas_mwh), ("power", company.power_mwh))
    )
    st.warning(
        f"What actually happened: the all-variable bill for {pd.Timestamp(ds).year} was "
        f"**€{rb / 1e6:.2f}m** (model expected €{var_row.expected / 1e6:.2f}m, bad case €{var_row.es95 / 1e6:.2f}m)."
    )

with st.expander("Model details"):
    st.code(hw.describe(m))
    src = a.recommendation["price_source"]
    st.markdown("**Fixed prices used:** " + "; ".join(f"{k} €{a.fwd_now[k]:.1f}/MWh ({v})" for k, v in src.items()))
    st.caption(
        "Commodity cost only (no network tariffs, energy tax or supplier margin). Without data/forwards.csv the "
        "fixed price is a stand-in for real forward prices: 30-day average spot (power seasonally adjusted) + 5%. "
        "The simulated price level is anchored to the fixed price (minus the premium); the model supplies the "
        "risk around it."
    )
