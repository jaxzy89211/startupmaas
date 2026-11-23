# HedgeWise methodology (2 pages)

Every assumption is in `config.yaml`; every output records the config hash, a hash of the data and the data's date
range. Detailed numbers: `RESULTS.md`; market size: `MARKET.md`.

## 1. Data
* **Power:** Dutch day-ahead prices (Energy-Charts, CC BY 4.0), daily averages from hourly data and, since Oct 2025,
  15-minute data. German (DE-LU, DE-AT-LU before Oct 2018) and Belgian prices for the robustness test.
* **Gas:** TTF front-month (Yahoo `TTF=F`, from Oct 2017; roll jumps included). Weekend gas = Friday's price.
* **Company:** monthly gas (m³) and power (kWh) from bills or yearly totals × a sector profile (an assumption).
  Gas m³ → MWh at 0.009769 MWh/m³ (35.17 MJ/m³). Commodity cost only: no network costs, taxes or supplier margin.

## 2. Price model
* **Gas:** GARCH(1,1) with Student-t shocks on daily log returns, fitted by maximum likelihood on all data up to the
  decision date. Prices are simulated as a martingale: no view on direction.
* **Power:** `log P = a + b·log gas + annual seasonality + weekend effect + AR(1) residual`, Student-t, fitted on the
  last 2 years; power inherits gas crises and keeps its own spikes. Simulated power is floored at €5/MWh.
* **Dependence:** correlated shocks from the standardised residuals (fat tails are joint).
* **Hourly prices (green investments):** simulated monthly level × real day profiles of the same calendar month
  (last 36 months), so midday solar dips and evening peaks keep their real shape.

## 3. Anchoring: the market sets the level, the model sets the risk
Simulated paths are rescaled so the expected delivery price equals the fixed price ÷ 1.05 (a 5% risk premium). The
fixed price is the real forward (`data/forwards.csv`) when available, otherwise a proxy: the 30-day average spot price
(power seasonally adjusted) × 1.05. So fixing costs exactly the premium in expectation; the model only says how wide
the outcomes are.

## 4. Objective and advice
For each share fixed h (0–100%, same for gas and power, bought in monthly tranches until delivery):
`minimise E[bill] + (γ/2)·Var[bill] / E[all-variable bill]`, γ = risk aversion (default 0.5). Variance is quadratic in
h, so the answer is interior (≈ 1 − premium / (γ·CV²)). A budget mode picks the smallest h that keeps the 95% VaR within
budget. **The recommendation is the HedgeWise ladder** (pre-registered 23 Nov 2026, `PREREGISTRATION.md`): the
optimiser's share at γ = 0.5 is the *model target*, kept between the 40% base layer (bought in H2 of Y-2) and last
season's target + 10 points, and bought in monthly tranches in H2 of Y-1. Its 2021–2025 results are in-sample; from
2027 every new year and market is scored out of sample. The plain optimiser stays available as "model-only" (not
recommended: procyclical). The **bad case** shown to clients is the higher of the model's expected shortfall (average
of the worst 5% of scenarios) and the bill in a repeat of the 2021→2022 shock (labelled model / stress), because the
model's 12-month ranges held only 72% of outcomes before the 2021–22 crisis. The objective is mean-variance, so this
doesn't change the advised share. The trade-off chart shows expected bill vs this bad case for every h.

## 5. Validation (out of sample, `validation.py`)
At 76 month ends from June 2020 the model is fitted on data up to that date only (a test proves no look-ahead) and
forecasts the average gas and power price over the next 1, 3 and 12 months. Five variants (GARCH-t, GJR-GARCH-t,
GARCH + jumps, no anchoring, block bootstrap) were scored on coverage, Kupiec/Christoffersen tests, CRPS and pinball
loss, with a selection rule written down before the results. **Result:** no variant was better by more than 3% on
12-month CRPS or had acceptable coverage, so GARCH-t stayed. 12-month 5–95% coverage was 71–72% (target 90%): **every
model failed in the 2021–22 build-up** (28–40% coverage for forecasts made before July 2022) and was well calibrated
afterwards (90–92%). Anchoring to the 30-day proxy hurts at 1 month (the proxy lags a trend) but not at 12 months.

## 6. Backtest and robustness
* **Backtest 2021–2025** (`backtest.py`): every decision re-fits the model on data up to that date; the realised bill
  uses real monthly prices. 60% in monthly tranches and a 2-year layered plan cut the worst year by 7–21% for 3–5% more
  in total; fixing 100% in October was 25% dearer than staying variable. The model-driven optimiser was procyclical
  and lost to the simple rules.
* **Other markets, 2020–2025:** the same ranking with NL, DE-LU and BE power (coupled markets, same gas: one history).
* **Synthetic futures** (`robustness.py`): 8 delivery years on futures simulated from the model, plus 3 named stress
  paths, 1,000 futures. The HedgeWise ladder and the 2-year layered plan cut the 95th-percentile worst year by 12–13%
  and the biggest jump by 30–37%, but cost 22–29% more than staying variable in the median future; the model-only
  optimiser barely cut the worst year (−1%); fixing 100% made the jumps worse. The pre-registered block bootstrap failed a price sanity check
  (it copies the 2019–26 upward drift and ignores mean reversion in power) and is not used for conclusions.
* **Procurement audit** (`audit.py`): a company's real past contracts vs the same strategies on real prices; checked
  against hand calculations in the tests.

## 7. Green investments (`hedgewise/green.py`)
Each investment changes the bill by Σ_month (monthly price level × change in "MWh at the monthly price") + a fixed
yearly amount, evaluated on the same scenarios as the hedge:
* **Solar:** a typical-day production profile per month; self-consumed power replaces purchases at the hourly price,
  exports earn 80% of it. Capex €650/kWp (placeholder), annuity at 5% over 25 years.
* **Battery:** one cycle a day (charge in the cheapest hours, discharge in the dearest, 88% round trip) plus an assumed
  peak-shaving saving. Capex €350/kWh (placeholder).
* **PPA:** a share of the power volume at a fixed price; at today's fixed price it is exactly a long-dated hedge.
Payback is shown at normal prices and in the plan's worst 5% scenarios.

## 8. Limitations
1. **No real forward prices yet**: the fixed price is a spot-based proxy; with real forwards the premium and the
   advice will move.
2. **The model can't foresee regime changes** (2021–22); the stress tests are there for that.
3. **No mean reversion in gas**; GARCH volatility peaks after spikes, so model-driven hedging is procyclical.
4. **Volumes are assumed known** (no production or weather risk); monthly baseload power, no imbalance or shape cost.
5. **Small samples:** 5–6 real delivery years and one crisis. Synthetic futures inherit the model's assumptions.
6. **Green investments:** typical days only, spreads assumed proportional to the price level, placeholder capex.
7. **Market size:** energy per company assumes use proportional to size within a sector (an upper bound).
