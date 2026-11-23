"""
HedgeWise live demo  -  run with:   streamlit run app.py
Consumption in  ->  worst-case bill and recommendation out.
"""

import io

import numpy as np
import pandas as pd
import streamlit as st

import hedgewise_model as hw

st.set_page_config(page_title="HedgeWise", page_icon="⚡", layout="wide")


@st.cache_data(show_spinner=False)
def load():
    return hw.load_prices()


@st.cache_resource(show_spinner=False)
def fitted(as_of: str):
    return hw.fit(load(), as_of)


@st.cache_resource(show_spinner=False)
def sims(as_of: str, delivery_start: str, n: int, seed: int):
    m = fitted(as_of)
    end = pd.Timestamp(delivery_start) + pd.DateOffset(years=1) - pd.Timedelta(days=1)
    return hw.simulate(m, end, n, seed)


df = load()
st.title("⚡ HedgeWise")
st.caption("How bad can next year's energy bill get, and how much should you fix? "
           f"Prices: Dutch day-ahead power + TTF gas up to {df.index.max():%d %b %Y}.")

with st.sidebar:
    st.header("Your company")
    name = st.text_input("Name", "Limburg food processor")
    up = st.file_uploader("Upload monthly bills (CSV: month, gas_m3, power_kwh)", type="csv")
    if up is not None:
        company = hw.Company.from_csv(up, name=name)
        st.success(f"{sum(company.gas_m3):,.0f} m3 gas, {sum(company.power_kwh)/1e3:,.0f} MWh power per year")
    else:
        gas = st.number_input("Gas per year (m3)", 0, 50_000_000, 1_200_000, step=50_000)
        power = st.number_input("Electricity per year (MWh)", 0, 500_000, 5_000, step=250)
        base = hw.Company()
        g_share = np.array(base.gas_m3) / sum(base.gas_m3)
        p_share = np.array(base.power_kwh) / sum(base.power_kwh)
        company = hw.Company(name=name, gas_m3=tuple(gas * g_share), power_kwh=tuple(power * 1000 * p_share))
    company.solar_kwp = st.slider("Rooftop solar (kWp)", 0, 5000, 0, step=100,
                                  help=f"~{hw.SOLAR_YIELD_KWH_PER_KWP} kWh per kWp per year, all self-consumed")

    st.header("Decision")
    scenario = st.selectbox("Decision date", ["Today", "30 Jun 2021 (before the crisis)",
                                              "30 Jun 2022 (mid-crisis)", "Custom"])
    as_of = {"Today": str(df.index.max().date()), "30 Jun 2021 (before the crisis)": "2021-06-30",
             "30 Jun 2022 (mid-crisis)": "2022-06-30"}.get(scenario)
    if as_of is None:
        as_of = str(st.date_input("As of", pd.Timestamp("2021-09-30"), min_value=pd.Timestamp("2020-01-31"),
                                  max_value=df.index.max()))
    mode = st.radio("Goal", ["Balance cost and risk", "Stay within a budget"])
    if mode == "Balance cost and risk":
        lam, budget = st.slider("Risk aversion", 0.0, 1.0, 0.5, 0.1,
                                help="0 = only care about the average bill, 1 = care a lot about bad years. "
                                     "The chart 'Pick your trade-off' shows every other choice."), None
    else:
        lam = 0.5
        b = st.number_input("Energy budget (EUR, 0 = last 12 months + 20%)", 0, 100_000_000, 0, step=50_000)
        budget = "auto" if b == 0 else b
    allow_timing = st.checkbox("Allow fixing everything at once (market timing)", False)
    n = st.select_slider("Scenarios", [2000, 5000, 10000], 5000)

ds = str(pd.Timestamp(year=pd.Timestamp(as_of).year + 1, month=1, day=1).date())
with st.spinner("Fitting GARCH-t model and simulating price scenarios ..."):
    m = fitted(as_of)
    s = sims(as_of, ds, n, 42)
    a = hw.analyse(m, company, ds, n, 42, lam, sims=s, allow_timing=allow_timing, budget=budget)

var_row, hw_row = a.table.loc["All variable"], a.table.iloc[-1]
c1, c2, c3, c4 = st.columns(4)
c1.metric("Expected bill, all variable", f"€{var_row.expected/1e6:.2f}m")
c2.metric("Bad case (avg worst 5%), all variable", f"€{var_row.es95/1e6:.2f}m")
c3.metric("Bad case with HedgeWise", f"€{hw_row.es95/1e6:.2f}m",
          delta=f"-€{(var_row.es95-hw_row.es95)/1e6:.2f}m", delta_color="inverse")
rg, rp = a.recommendation['hedge_gas'], a.recommendation['hedge_power']
c4.metric("Recommended share to fix", f"{rg:.0%}" if np.isclose(rg, rp) else f"gas {rg:.0%} · power {rp:.0%}")

st.info(hw.recommendation_text(a).replace("\n", "  \n"))

buf = io.BytesIO()          # in memory: no shared file when several people use the demo at once
hw.plot_analysis(a, buf)
st.image(buf.getvalue(), width="stretch")

st.subheader("Strategy comparison (EUR k)")
st.dataframe((a.table / 1e3).round(0).astype(int), width="stretch")

if pd.Timestamp(ds) + pd.DateOffset(years=1) <= df.index.max():
    real = {c: df.loc[ds:str(pd.Timestamp(ds).year) + "-12-31", c] for c in ("gas", "power")}
    rb = sum(float(v @ real[c].groupby(real[c].index.month).mean().values)
             for c, v in (("gas", company.gas_mwh), ("power", company.power_mwh)))
    st.warning(f"What actually happened: the all-variable bill for {pd.Timestamp(ds).year} was "
               f"**€{rb/1e6:.2f}m** (model expected €{var_row.expected/1e6:.2f}m, bad case €{var_row.es95/1e6:.2f}m).")

with st.expander("Model details"):
    st.code(hw.describe(m))
    st.caption("Commodity cost only (no network tariffs, energy tax or supplier margin). Fixed prices are a stand-in "
               "for real forward prices: 30-day average spot (power seasonally adjusted) + 5%. The simulated price level "
               "is anchored to that fixed price (minus the premium); the model supplies the risk around it.")
