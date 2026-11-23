# HedgeWise methodology

How the price model works, how it is tested out of sample, and where it fails. All assumptions are in `config.yaml`;
every output records the config hash and a hash of the data it was built on.

## 1. The price model

| Part | Model | Fitted on |
|---|---|---|
| Gas (and CO₂ if present) | Daily log returns: GARCH(1,1) volatility with Student-t shocks (`model.variance: garch_t`). The price is simulated as a martingale (no view on direction) | All gas trading days up to the as-of date |
| Power | `log P = a + b·log gas [+ c·log CO₂] + annual seasonality + weekend effect + AR(1) residual` with Student-t innovations | The last 2 years up to the as-of date |
| Dependence | Correlated shocks (standardised residuals); shared fat tails | Last 2 years |
| Level ("anchoring") | Simulated paths are rescaled so the expected delivery price equals the fixed price ÷ (1 + 5% premium). *The market sets the level, the model sets the risk* | Fixed price = real forward if `data/forwards.csv` exists, else 30-day spot mean (power seasonally adjusted) × 1.05 |

Alternative volatility models (available via `model.variance`, tested below):
* **GJR-GARCH-t** (`gjr_t`): `σ²ₜ = ω + (α + γ·1[rₜ₋₁ > 0])·r²ₜ₋₁ + β·σ²ₜ₋₁`. A price *rise* raises tomorrow's volatility by an
  extra γ·r², the "inverse leverage" typical of energy markets.
* **GARCH + jumps** (`garch_jump`): `r = σₜ·z + J`, with z normal and J = 0 with probability 1-λ, or J ~ N(μ_J, σ_J²) with
  probability λ. The variance recursion uses the total return, and the price drift compensates the jumps so the price
  stays a martingale.

Each fitter is tested on synthetic data with known parameters (`tests/test_variants.py`): the fit must recover α, β, ν,
γ and the jump parameters within stated tolerances.

## 2. Out-of-sample validation (`python validation.py`)

**Design.** At every month end from June 2020 until the last month with a complete horizon, the model is fitted on
data up to that date only. It then forecasts the distribution (2,000 scenarios) of the **average gas and power price
over the next 1, 3 and 12 calendar months**, and each forecast is scored against what actually happened.
`tests/test_validation.py::test_no_look_ahead` proves there's no look-ahead: corrupting every price after the
forecast date leaves all forecasts bit-for-bit identical.

**Variants**, all on exactly the same origins, scenarios and seed:

| | Variant | Level |
|---|---|---|
| a | GARCH-t: **current default** | anchored |
| b | GJR-GARCH-t | anchored |
| c | GARCH + jumps | anchored |
| d | GARCH-t | **not** anchored (the model's own expected level = today's price) |
| e | Block bootstrap: random 30-day blocks of historical daily log changes of gas and power, joined into paths from the last observed prices. **No model**: a benchmark | not anchored |

In the validation, anchoring sets each window's forecast mean to the fixed-price proxy for that window ÷ 1.05, which is
the trailing 30-day spot mean (power seasonally adjusted to the window). This is the product's rule applied to each
forecast window.

**Scores**, per variant, horizon and commodity:
* **Coverage**: the share of outcomes inside the 5–95% band (target 90%) and the 1–99% band (target 98%).
* **Kupiec test**: is the share of outcomes above the forecast 95th percentile consistent with 5%? (p < 0.05 = no)
* **Christoffersen test**: are those exceedances independent (not clustered)? Reported as independence and
  conditional-coverage p-values.
* **CRPS** (continuous ranked probability score, EUR/MWh, lower is better): one number that rewards both a correct
  centre and a correctly sized spread. Also the **pinball loss** at the 95% quantile, which scores the upper tail
  that matters for a buyer.

**Caveat.** 3- and 12-month windows from consecutive month ends overlap, so their outcomes are serially dependent by
construction. The Kupiec and Christoffersen tests assume independent forecasts: they're exact only for the 1-month
horizon and indicative for 3 and 12 months. With about 76 monthly origins (about 65 at 12 months), and one energy
crisis inside them, every score has wide uncertainty.

**Pre-registered selection rule** (written into `config.yaml` and `choose_default()` before any result was seen):
the default becomes the model variant (a–d) with the lowest mean 12-month CRPS relative to the current model
(average over gas and power), but only if it is **more than 3% better** *and* its 12-month 5–95% coverage is
**within 80–95% for both commodities**. Otherwise the current model stays. The bootstrap (e) is a benchmark only: it
cannot power the scenario engine of the product, so it is never chosen. If it beats every model, that is reported as
a finding.

## 3. Results (data up to 23 Nov 2026; 76 origins at 1 month, 74 at 3, 65 at 12)

Full table: `output/validation_table.csv`. Per-forecast scores: `output/validation_forecasts.csv`. Chart:
`output/validation.png`.

| Variant | Horizon | Gas cov. 90 / 98 | Power cov. 90 / 98 | Gas hits >95th | Power hits >95th | Gas CRPS (rel.) | Power CRPS (rel.) |
|---|---|---|---|---|---|---|---|
| a GARCH-t (current) | 1 m | 71% / 92% | 76% / 91% | 22% | 14% | 7.55 (1.00) | 17.68 (1.00) |
| | 3 m | 77% / 91% | 72% / 89% | 22% | 20% | 10.55 (1.00) | 21.47 (1.00) |
| | 12 m | 72% / 83% | 71% / 82% | 23% | 25% | 19.72 (1.00) | 35.48 (1.00) |
| b GJR-GARCH-t | 1 m | 72% / 92% | 75% / 92% | 21% | 14% | 7.55 (1.00) | 17.73 (1.00) |
| | 3 m | 76% / 89% | 72% / 86% | 22% | 20% | 10.57 (1.00) | 21.55 (1.00) |
| | 12 m | 69% / 83% | 71% / 80% | 25% | 25% | 19.82 (1.01) | 35.61 (1.00) |
| c GARCH + jumps | 1 m | 74% / 93% | 76% / 92% | 20% | 13% | 7.57 (1.00) | 17.76 (1.00) |
| | 3 m | 77% / 95% | 72% / 91% | 20% | 20% | 10.57 (1.00) | 21.48 (1.00) |
| | 12 m | 71% / 86% | 71% / 83% | 25% | 25% | 19.79 (1.00) | 35.55 (1.00) |
| d GARCH-t, no anchoring | 1 m | 88% / 99% | 78% / 91% | 9% | 21% | 5.16 (0.68) | 15.05 (0.85) |
| | 3 m | 86% / 99% | 66% / 91% | 12% | 30% | 8.88 (0.84) | 20.88 (0.97) |
| | 12 m | 71% / 88% | 68% / 80% | 25% | 28% | 19.29 (0.98) | 36.29 (1.02) |
| e block bootstrap | 1 m | 88% / 96% | 86% / 95% | 5% | 8% | 5.35 (0.71) | 24.32 (1.38) |
| | 3 m | 86% / 97% | 89% / 97% | 8% | 7% | 9.45 (0.90) | 29.82 (1.39) |
| | 12 m | 69% / 86% | 95% / 98% | 22% | 0% | 25.02 (1.27) | 65.95 (1.86) |

Kupiec p-values (95% quantile) are below 0.05 for every model variant (a–d) at every horizon, except unanchored gas
at 1 month (p = 0.13); the bootstrap passes at 1–3 months (p ≥ 0.26) and fails at 12. Christoffersen independence is
rejected at 3 and 12 months for a–c (expected: the windows overlap), not for unanchored gas at 3 months (p = 0.07),
and not at 1 month for any model variant.

**Selection (pre-registered rule): unchanged, GARCH-t stays the default.** No model variant has 12-month coverage
within 80–95% (all 68–72%), and none beats the current model's 12-month CRPS by more than 3% (best: −0.1%).

### Where every model fails

| | Fails when | Evidence |
|---|---|---|
| All models (a–d) | **Regime change: the 2021–22 crisis build-up.** Bands are built from past volatility, so a calm history gives narrow bands just before the largest move in decades | All 12-month misses come from origins mid-2020 to end-2021 (15–18 above the band) plus 3 below in 2022 (the fall). 12-month coverage: 28–40% for origins up to June 2022, 90–92% for origins from July 2022 |
| a–c (anchored) | **Short horizons in a trending market.** The fixed-price proxy is a 30-day average, which lags | 1-month gas coverage 71–74% vs 88% unanchored; after the peak, still 78–82% at 1 month |
| b GJR | Adds nothing: little asymmetry in the data (γ ≈ 0.03 on gas) | CRPS within 0.5% of a |
| c Jumps | Adds nothing measurable: jumps are rare (≈2.4% of days) and mostly absorbed by GARCH | CRPS within 0.5% of a |
| d No anchoring | 12-month power coverage is the lowest (68%); 3-month power hits above the 95th percentile 30% of the time | Table above |
| e Bootstrap | **Power at 12 months**: chaining daily power changes ignores mean reversion, so bands are ~2.5× wider than the model's (95% coverage, 0% hits, CRPS 1.9× worse). It also misses the crisis for gas at 12 months (69%) | Table above |

**Exploratory (not used for selection; the split date was chosen after seeing the chart):** for origins after June
2022, 12-month 5–95% coverage is 90–92% for every model variant. 1-month coverage is 76–82% anchored vs 82–88%
unanchored.

### Limits of this validation
* About 65–76 forecasts per horizon, with one crisis inside them; overlapping windows at 3 and 12 months. A different
  6-year window could rank the variants differently.
* The anchor in this test is the 30-day spot proxy. Real forward prices were not available. They are the product's
  intended anchor and should be validated the same way once available.
* Forecasts are scored on the average price over a window, which is what drives a bill, not on daily prices.
