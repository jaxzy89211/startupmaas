# HedgeWise model (v0.4)

Energy price-risk model for mid-sized Dutch companies: **consumption in → worst-case bill and hedge recommendation out.**

## Quick start

```bash
cd ~/hedgewise
source .venv/bin/activate          # or: python3 -m venv .venv && pip install -r requirements.txt
python download_data.py            # refresh prices (about 5 min, rate-limited API)
python hedgewise_model.py          # today's view for next calendar year
python backtest.py                 # strict 2021-2025 backtest + pitch chart
python report.py --lang nl         # one-page PDF risk report for a CFO (EN or NL)
streamlit run app.py               # live demo dashboard in the browser
python -m pytest -q                # tests (pip install -r requirements-dev.txt)
```

To put the dashboard online (private GitHub repo + Streamlit Community Cloud), follow `DEPLOY.md`.

Every chart and table is written to `output/`. See `RESULTS.md` for the numbers.

## Files

| File | What it does |
|---|---|
| `download_data.py` | Dutch day-ahead power (Energy-Charts, or ENTSO-E with `--entsoe`) and TTF gas (Yahoo `TTF=F`) from 2019 to today |
| `hedgewise_model.py` | Model fit, Monte Carlo, strategies, risk metrics, recommendation, charts |
| `backtest.py` | Strict backtest: each decision re-fits the model on data available on that date only. Writes `pitch_cycle.png` (main pitch chart) and `pitch_2022.png` |
| `app.py` | Streamlit dashboard for the live demo: bill upload or yearly totals, stress test, PDF download |
| `company_input.py` | Reads a company's consumption: our template, any CSV/Excel export (columns detected, Dutch formats), or yearly totals + sector profile |
| `report.py` | One-page A4 PDF risk report in English or Dutch (`make_report(analysis, path, lang)`), DejaVu Sans embedded, with trade-off and stress charts |
| `forwards.py` | Loads real forward prices from `data/forwards.csv` (if present) to replace the proxy |
| `tests/` | pytest tests: input parsing, stress test, policy, forwards |
| `data/company_example.csv` | Template for pilot data (month, gas_m3, power_kwh), one row per month of bills |
| `data/forwards_example.csv` | **Fake** forward prices, only for the tests. Never copy it to `data/forwards.csv` |
| `DEPLOY.md` | Click-by-click: private GitHub repo + Streamlit Community Cloud |
| `.streamlit/config.toml` | Dashboard theme |

## Useful commands

```bash
python hedgewise_model.py --as-of 2021-06-30                 # what the model said before the crisis
python hedgewise_model.py --as-of 2022-06-30                 # ... and in the middle of it
python hedgewise_model.py --risk-aversion 0.8                # 0.3 / 0.5 / 0.8 now give different answers
python hedgewise_model.py --budget auto                      # smallest hedge that keeps VaR95 within budget
python hedgewise_model.py --budget 1500000
python hedgewise_model.py --company data/pilot_x.csv         # a real company's bills (CSV or Excel, any export)
python hedgewise_model.py --company bills.xlsx --sector metal  # sector profile fills 1-3 missing months
python hedgewise_model.py --yearly-gas 300000 --yearly-power 1500 --sector offices --name "Kantoor Venlo"
python company_input.py bills.xlsx                           # check how a file is read (columns, units, warnings)
python report.py --company data/company_example.csv --lang nl   # -> output/report_<name>_<date>.pdf
python forwards.py data/forwards.csv                         # check a forward-price export
python hedgewise_model.py --solar-kwp 1000                   # green investment: rooftop solar
python hedgewise_model.py --allow-timing                     # let the optimiser fix everything at once
python hedgewise_model.py --demo                             # synthetic data, no files needed
python backtest.py --risk-aversion 0.3 0.5 0.8 --typical-month 11 --years 2021 2022 2023
python backtest.py --company data/pilot_x.csv
```

## How the model works

1. **Gas (and CO₂ if `data/co2_eua.csv` exists):** a GARCH(1,1) with Student-t errors on daily log returns, fitted by maximum likelihood. Prices are simulated as a martingale, so there is no built-in view on direction.
2. **Power:** `log P = a + b·log gas [+ c·log CO₂] + annual seasonality + weekend effect + AR(1) residual`, with Student-t innovations. Fitted on the last 2 years. Power inherits gas crises and keeps its own spikes.
3. **Correlation:** the shocks are correlated using standardized residuals. Fat tails are joint.
4. **Monte Carlo:** 10,000 daily paths from the as-of date to the end of the delivery year.
5. **Anchoring (new in v0.3):** the simulated paths are scaled so the expected delivery price equals today's fixed price minus the premium (ramping from 1 today to the full scale at delivery). Fixed prices quoted on later tranche dates are martingales around today's fixed price. *The market sets the level, the model sets the risk*, so hedging costs exactly the premium in expectation. Switch off with `ANCHOR_TO_FORWARD = False`.
6. **Strategies:** all variable; fix 60% now; fix 100% now; fix in monthly tranches (up to 6); and HedgeWise.
7. **HedgeWise optimizer:** grid search over one hedge ratio for gas and power (0–100% in 5% steps; `SAME_RATIO = False` for separate ratios), executed in tranches. It has two goals:
   * `--risk-aversion γ`: minimize `E[cost] + (γ/2)·Var[cost] / E[all-variable bill]`. Variance is quadratic in the hedge ratio, so the answer is interior (roughly `1 − premium / (γ·CV²)`). The v0.2 objective `(1-λ)·E + λ·ES95` is linear in the ratio (ES is positively homogeneous) and could only answer 0% or 100%; it's still available as `OBJECTIVE = "mean_es"`.
   * `--budget`: the smallest hedge that keeps the 95% VaR within budget. `auto` means the last 12 months' bill plus 20%.
8. **Efficient frontier:** expected bill against bad-case bill for every share fixed, with the recommendation marked. This is the main chart for a CFO.
9. **Risk metrics:** expected cost, median, 95% VaR, 95% Expected Shortfall (the average of the worst 5%), and the worst simulated year.
10. **HedgeWise policy (v0.4, `policy_plan`):** the optimizer's share becomes a *target*. Its rise is capped at 20 percentage points between monthly decision dates, and the rest is bought in equal monthly tranches (never front-loaded, never sold back). Why 20: six monthly dates give five steps, and 5 × 20 = 100%. It isn't tuned on the backtest.
11. **Stress test (`stress_test`):** two historical scenarios instead of one: (a) the 2022 monthly prices repeat exactly; (b) a shock as big as 2021→2022 from today (today's fixed price × 2022 avg / 2021 avg, with the 2022 monthly pattern; gas ×2.81, power ×2.35). The implied average prices are printed everywhere.
12. **Real forwards (v0.4, `forwards.py`):** if `data/forwards.csv` exists (columns date, product such as `TTF Cal-27` / `NL Base Cal-27`, and price), the latest quote on or before the decision date replaces the proxy as the fixed price and as the anchor (expected spot = forward / 1.05). Every output says which source was used. `data/forwards.csv` is git-ignored because licensed data must not be pushed.
13. **HedgeWise ladder (backtest only):** 40% in H2 of Y-2, then a top-up to the model's target in H2 of Y-1, capped at the previous season's target + 10 points. ⚠️ Designed after seeing the 2021–2025 results, so its backtest is not independent evidence.

**Consumption input (v0.4, `company_input.py`):** units m³ / kWh / MWh / GJ for gas (1 GJ = 0.2778 MWh) and kWh / MWh for power. It handles Dutch numbers (`1.234,5`) and month names (`januari 2025`, `mrt-25`), and title rows above the table. With several years it averages per calendar month. If 1–3 months are missing it fills them from the sector profile with a warning; with more missing it raises an error. The sector profiles (food processing, metal, greenhouse horticulture, offices) are **assumptions** in `SECTOR_PROFILES`.

**Company profile (assumption, replace with interview or pilot data):** a hypothetical Limburg food processor using 1.2 million m³ of gas a year (≈11.7 GWh, winter-heavy) and 5 GWh of power (flat, small summer bump). Check against CBS StatLine: search "energieverbruik industrie bedrijfstak" for the food industry (SBI 10). Or better, use your first interview.

## Limitations (say these out loud in Q&A, before the jury does)

1. **No real forward prices yet.** The loader is ready (`forwards.py`), but until `data/forwards.csv` exists the "fixed price" is the 30-day average spot price (power seasonally adjusted) + 5%. Real Cal-year forwards differ, especially in 2021–22 when the curve was steeply backwardated. Get EEX/ICE Endex data via Refinitiv or Bloomberg (ask the UM library or the SBE finance department). With real forwards, the model still assumes a 5% risk premium inside the forward.
2. **Commodity cost only.** Network tariffs, energy tax, ODE and supplier margins are left out. They're the same across strategies but make the total bill bigger.
3. **The model underestimated the 2021–22 crisis.** The realised 2021 and 2022 bills were outside the model's 95% range as of June the year before. A GARCH model fitted on calm 2019–2021 data can't foresee a war, and the ES95 of €2.1m still undershot the €2.67m actual. Use this as a selling point for *stress scenarios on top of the model*, not as a reason to trust the model blindly.
4. **No mean reversion in gas, and the optimizer is procyclical.** Prices are a random walk and GARCH volatility peaks after prices spike, so the optimizer hedges most when prices are highest (H2 2022 → expensive 2023). In the backtest, simple disciplined rules (60% in tranches, layered over 2 years) beat the optimizer. See RESULTS.md.
5. **Consumption is assumed known** (no volume risk), and the premium is a flat 5%. With real forwards the premium varies, which will move the recommendation.
6. **Monthly baseload prices.** There's no hourly profile and no shape or imbalance cost. Simulated power can't go negative (floored at €5/MWh).
7. **Data sources.** Gas is the Yahoo TTF front-month, which has roll jumps. Power comes from Energy-Charts (CC BY 4.0, Bundesnetzagentur | SMARD.de): fine for a pitch, but use ENTSO-E for a product. CO₂ isn't loaded yet; download it manually from investing.com.
8. **Solar** is simplified: 900 kWh/kWp/yr, all of it used on site, valued at the monthly average price.

## Sanity checks done

* Results are stable across random seeds. All-variable ES95 (as of 2021-06-30) was €2.06m, €2.12m and €2.16m for seeds 1, 2 and 3, within ±2.5%.
* The 2022 gas peak in the data was €339/MWh, with a yearly average of €124. Power averaged €243/MWh, which matches public figures.
* The backtest decisions only use `df.loc[:decision_date]` (see `backtest.run_year`).
* v0.3: with anchoring, "Fix 100% now" costs exactly 1.05 × the expected all-variable bill, and tranches cost the same as fixing now in expectation (checked at 3 dates).
* v0.3: risk aversion 0.3 / 0.5 / 0.8 → 40% / 70% / 85% fixed as of 30 Jun 2021 (single ratio, 4,000 scenarios).
* Still to do: a teammate who didn't build the model reruns `python backtest.py` and gets the same numbers.
