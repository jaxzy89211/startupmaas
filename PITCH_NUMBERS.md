# Pitch numbers (frozen at git tag `pitch-freeze-v1`, 23 Nov 2026)

Every number the team may put on a slide or say to a judge. Each has its **source**, the **wording allowed** and the
**caveat that must go with it**. If a number isn't here, don't use it. Company: the hypothetical Limburg food
processor (1.2m m³ gas + 5 GWh power a year), commodity cost only (no network costs, taxes or supplier margin).

## 1. Backtest 2021–2025, real Dutch prices
Source: `RESULTS.md` (Backtest 2021–2025), `output/backtest_results.csv`, `output/pitch_cycle.png`.

| Number | Allowed wording | Must go with it |
|---|---|---|
| Worst year: all variable €2.67m, 60% tranches €2.47m, layered 2 years €2.11m | "Fixing 60% in monthly steps or layering over two years cut the worst year by 7–21% versus staying variable." | "Over 2021–2025, one 5-year history." |
| Total: all variable €6.40m, 60% tranches €6.71m (+5%), layered €6.60m (+3%) | "…for 3–5% more in total over those five years." | "That's what 2021–2025 cost; in simulations hedging usually costs more (section 3)." |
| Fix 100% in October: total €7.98m (+25%), worst year €4.31m (1.6×) | "Fixing 100% once a year in October was the most expensive habit: 25% more than staying variable, worst year 60% higher." | "Five years, one market." |
| Biggest jump: variable €1.58m, layered €0.93m (−41%) | "Layering cut the biggest year-on-year jump by about 40%." | "2021–2025." |

## 2. The HedgeWise ladder (our rule)
Source: `PREREGISTRATION.md`, `RESULTS.md` (HedgeWise ladder section).

| Number | Allowed wording | Must go with it |
|---|---|---|
| Pre-registered 23 Nov 2026, rule commit `5023af4` | "Our rule is the HedgeWise ladder, pre-registered on 23 November 2026." | "2021–2025 is in-sample; we score every year from 2027 on out of sample." |
| 2021–2025: worst year €1.89m, biggest jump €0.86m, total €6.71m | Only as "how the rule we chose looks on the years we designed it on". | "In-sample: designed after seeing these years. Not proof." |

## 3. Robustness: 1,000 simulated 8-year futures (model family)
Source: `RESULTS.md` (Robustness), `output/robustness_table.csv`, `output/robustness_summary.txt`. n = 1,000.

| Number | Allowed wording | Must go with it |
|---|---|---|
| Ladder: P95 worst year −13%, P95 biggest jump −37% vs all variable | "In 1,000 simulated futures our ladder cut the worst year (1-in-20 futures) by about 13% and the biggest jump by about a third." | "Futures from our own model, fitted on the same history." |
| Layered 2 years: −12% / −30% | Same wording for the 2-year ladder. | Same. |
| Median 8-year cost: ladder +22%, layered +29% vs variable; variable cheapest in 84% of futures | "In a typical future, hedging costs more than staying variable: it's insurance." | Always say this together with the tail cut. |
| Model-only optimiser: P95 worst year −1% | "The model on its own doesn't protect the bad year; the rule does." | — |

## 4. Other markets (Germany, Belgium), 2020–2025
Source: `RESULTS.md` (Other markets), `output/backtest_*_extended.csv`.

| Number | Allowed wording | Must go with it |
|---|---|---|
| Same ranking in NL, DE-LU, BE; layered cuts the worst year by ~21% for 4–5.5% more | "The same pattern holds with German and Belgian power prices." | "Coupled markets and the same gas price: not independent proof." |

## 5. Stress test and today's advice (as of 23 Nov 2026, delivery 2027)
Source: `RESULTS.md` (What the model says, Stress test), `output/model_today_log.txt`, the PDF report.

| Number | Allowed wording | Must go with it |
|---|---|---|
| Expected bill all variable €2.02m; bad case €5.32m (a 2021→22-size shock) | "If prices jumped like 2021→2022, a variable bill of €2.0m would become about €5.3m." | "A stress scenario, not a forecast." |
| Ladder: fix 50% (model target 55%), bad case €3.72m, extra expected cost €50k | "Our rule: fix 50% in two steps. That takes about €1.6m off the bad case for about €50k a year on average." | "Hypothetical company; proxy fixed prices (recent market + 5%), not real forwards." |
| Each extra 10% fixed: ~€10k more on average, ~€310k less in the bad case | "Every 10% you fix costs about €10k and removes about €310k from the bad case." | "Today's prices, this company." |
| The bad case is never below the 2021→22 shock | "Our bad case is never lower than a repeat of the 2021–22 shock, because our model underestimated that crisis." | — |

## 6. Model validation
Source: `RESULTS.md` (Model validation), `output/validation_table.csv`.

| Number | Allowed wording | Must go with it |
|---|---|---|
| 76 monthly out-of-sample forecasts; 12-month coverage 71–72% overall, 28–40% before July 2022, 90–92% after | "We tested the model honestly on 76 monthly forecasts. Before the 2021–22 crisis no statistical model saw it coming (ours held 28–40% of outcomes in its 90% range); afterwards 90–92%." | "That's why we always show the stress test." |

## 7. Market size
Source: `MARKET.md`, `output/market_summary.csv` (CBS tables 81589NED, 81578NED, 83583NED, 83989NED).

| Number | Allowed wording | Must go with it |
|---|---|---|
| ~3,600 target companies in NL, ~250 in Limburg | "About 3,600 Dutch companies with 10–250 people in energy-intensive sectors use 1 GWh or more a year." | "Estimate from CBS data; energy per company is an upper bound." |
| Revenue at 10% adoption €1.3–1.8m a year (NL); Limburg ~€0.1m | "At 10% of them, €1.3–1.8m a year." | "Adoption rates are assumptions; Limburg is the pilot, not the market." |
| A customer is worth ~€5,000 a year | "€3,000 subscription + €150 per GWh." | "Pricing is an assumption." |

## May NOT say
* **"For 3–5% extra"** as a general cost of hedging. That is 2021–2025 only; in simulated futures hedging costs ~20–30% more in a typical future.
* **"Fix 60% in July"** as a strategy. Its good 2021–2025 numbers are luck of the month (€5.56m–€7.60m depending on the month).
* **The ladder as proof** ("the backtest proves the ladder works"). It was designed after seeing 2021–2025.
* **"HedgeWise saves you money" / "beats the market."** It reduces bad years; on average it costs a little.
* **"Our optimiser beats simple rules."** It doesn't (procyclical).
* **"Proven", "always", "guaranteed", "worst case".** The bad case is a plausible bad year, not a ceiling.
* Green-investment paybacks as firm numbers (placeholder capex, today's high prices flatter them).
* Any number for a real company without its own data and real contract prices.
* The 200-history robustness figures (−20%, +6%): superseded by n = 1,000.
