# HedgeWise: results on real data (v0.4, 23 Nov 2026)

**v0.4 (for the 8 Dec Speeddate)** adds tools for real interviews; it does not change the model maths:
- read real bills: any CSV or Excel export, Dutch formats, or yearly totals + sector
- a one-page CFO report (PDF, English or Dutch)
- a "what if 2022 happens again?" stress test
- a capped, layered HedgeWise policy
- a loader for real forward prices
- Streamlit Cloud deployment prep

The 2021–2025 backtest numbers below are **identical to v0.3** (rerun 23 Nov 2026). The only new column is the HedgeWise policy.

Real Dutch day-ahead power and TTF gas prices, 2019 to today. Company: the hypothetical Limburg food processor (1.2m m³ gas + 5 GWh power a year). Commodity cost only. Reproduce with `python backtest.py` and `python hedgewise_model.py --as-of ...`.

## What changed since v0.2 (and why the numbers moved)

1. **The model's price level is now anchored to the fixed price.** In v0.2 the model's own expected spot price disagreed with the fixed-price proxy by −21% to +23% (gas, 30 Jun 2022: fixed price €112 vs model expectation €142). So inside the model, fixing looked *cheaper than staying variable* in 2021 and 2022, and the optimizer said "100%" for that reason alone. Now the market sets the level and the model sets the risk: hedging costs exactly the 5% premium in expectation.
2. **New optimizer objective.** v0.2 minimized `(1−λ)·E + λ·ES95`. ES is linear in the hedge ratio, so that objective can only ever answer 0% or 100%, which is why risk aversion 0.3, 0.5 and 0.8 gave identical answers. v0.3 minimizes `E + (γ/2)·Var / E[bill]`, which has an interior optimum. It uses one ratio for gas and power (per-commodity ratios improved the objective by <0.5% and gave confusing "100% gas, 0% power" advice).
3. **Efficient-frontier chart** (bottom left in `analysis_*.png` and in the dashboard): expected bill against bad-case bill for every share fixed, 0–100%.
4. **Backtest:** added *Fix 60% in tranches* and *Layered over 2 years*, plus biggest year-on-year jump and a coverage count. The new pitch chart is `output/pitch_cycle.png`.
5. Fan-chart fix: the spike at the start of the power scenarios was a plotting artefact (7-day average over too few days).

## The pitch message (revised)

> **Mid-sized companies either stay fully variable or fix 100% once a year. Over 2021–2025 both were the most expensive way to buy energy in the years that hurt. Fixing 60% in monthly tranches, or layering over two years, cut the worst year by 7–21% and the biggest year-on-year jump by 21–41% versus staying variable, for 3–5% more over five years. Against the "fix 100% in October" habit it was cheaper *and* safer: −16 to −17% total, worst year cut by 43–51%.**

Use `output/pitch_cycle.png` as the main chart and `output/pitch_2022.png` as the "2022 story" chart. They tell the same story; 2022 alone cherry-picks the year where hedging won.

## Backtest 2021–2025 (strict, realised annual bill, €k)

| Year | All variable | Fix 100% Oct (typical) | Tranches 100% | **Tranches 60%** | **Layered 2y (40+40%)** | HedgeWise policy γ=0.5 |
|---|---|---|---|---|---|---|
| 2021 | 1,086 | 346 | 295 | 611 | 480 | 665 |
| 2022 | 2,670 | 1,459 | 1,312 | 1,855 | 1,177 | 1,892 |
| 2023 | 986 | 4,312 | 3,462 | 2,472 | 2,107 | 2,901 |
| 2024 | 793 | 996 | 955 | 890 | 1,925 | 882 |
| 2025 | 867 | 871 | 896 | 884 | 913 | 855 |
| **Total** | 6,403 | 7,985 | 6,919 | 6,713 | 6,602 | 7,195 |
| **Worst year** | 2,670 | 4,312 | 3,462 | 2,472 | **2,107** | 2,901 |
| **Biggest jump** | 1,584 | 2,853 | 2,150 | 1,244 | **930** | 1,227 |

Full table, including γ = 0.3 / 0.8 and "Fix 60% at once": `output/backtest_results.csv`, `backtest_stability.csv`, `backtest_log.txt`.

### The HedgeWise policy (v0.4): what the capped, layered plan changed — nothing, in this backtest

The policy (`policy_plan` in `hedgewise_model.py`) takes the optimizer's target share (γ = 0.5), caps its rise at **20 percentage points** between consecutive monthly decision dates, and buys the remainder in equal monthly tranches. It never front-loads and never sells back.

**Why 20 points** (decided before running the backtest, and not tuned on it): there are 6 monthly decision dates per buying season, so 5 steps. 5 × 20 = 100%, so the cap can never stop a plan from reaching a full hedge within one season; it only stops a one-month jump after a volatility spike. The first decision date of a season has no previous target, so it is uncapped.

**Result, compared honestly with the simple rules (€k, 2021–2025):**

| | Total | Worst year | Biggest jump |
|---|---|---|---|
| HedgeWise policy γ=0.5 | 7,195 | 2,901 | 1,227 |
| Tranches 6× (60%) | 6,713 | 2,472 | 1,244 |
| Layered 2 years (40+40%) | **6,602** | **2,107** | **930** |

* **The cap never kicked in.** In all five seasons the optimizer's target was highest on the first date and drifted down afterwards (e.g. for 2023: 85% → 70%). So the policy produced **exactly the same trades as the existing optimizer column** (γ = 0.5).
* It is **worse than both simple rules** on total (+€0.48m vs 60% tranches, +€0.59m vs layered) and on worst year (+€0.43m, +€0.79m). Its biggest jump is the same as 60% tranches (−€17k) and worse than layered (+€0.30m).
* **Why:** the procyclicality is *between* seasons, not within one. In H2 2022, after the price spike, GARCH volatility was high on the very first decision date, so the target started at 85–90% and the policy locked in crisis prices for 2023. A month-to-month cap that starts fresh each season can't see that.
* **What would address it** (not done, because the cap mustn't be tuned on these results): carry the previous season's target over as the starting point of the cap, or add mean reversion to the gas model. Decide either one on reasoning first, then test it once.
* **Pitch implication:** sell the *discipline* (60% in tranches or a 2-year ladder) as the recommendation, and the model as the tool that *measures* the risk (bad-case bill, stress test, frontier). Don't claim the optimizer beats simple rules.

### HedgeWise ladder: ⚠️ designed AFTER seeing the 2021–2025 results, so this backtest is NOT independent evidence

**Rule:** buy 40% in 6 monthly tranches in H2 of Y-2. In H2 of Y-1, top up in equal monthly tranches to the model's target (γ = 0.5). The target may not exceed the **previous season's** target by more than 10 points. "Previous season's target" is the ladder's target on the last decision date for delivery year Y-1. For 2021 there is no previous season in the data, so the 40% base layer is used as the starting point (cap 50%). The rule combines what the earlier sections showed: layering across two years helped, and between-season jumps hurt. **That's exactly why its good numbers below prove nothing yet**; it needs testing on data it wasn't designed on (other years, other markets, or the coming seasons).

| €k, 2021–2025 | Total | Worst year | Biggest jump |
|---|---|---|---|
| All variable | 6,403 | 2,670 | 1,584 |
| Tranches 6× (60%) | 6,713 | 2,472 | 1,244 |
| Layered 2 years (40+40%) | 6,602 | 2,107 | 930 |
| HedgeWise policy γ=0.5 | 7,195 | 2,901 | 1,227 |
| **HedgeWise ladder** (in-sample!) | 6,712 | **1,894** | **855** |

Per year: 729 / 1,584 / 1,612 / 1,894 / 893. The ladder fixed 48%, 50%, 60%, 65% and 67% for 2021–2025. The cap held the target well below the optimizer (75–95%) in every season, so in practice it behaves like a slowly rising 50–70% two-year ladder. Its total equals 60% tranches, but its worst year and biggest jump are the lowest of all the disciplined rules. Its worst year is now 2024 (it was still carrying 2022–23 purchases), not 2023.

**Pitch use:** if at all, then only as "the rule we will test going forward", never as proof.

### Two things the jury will ask about

* **"Fix 60% at once in July" has the best numbers in the table (total €5.56m), but that's luck.** Fixing 60% once in a different month of the same half-year gives a total of €5.56m–€7.60m and a worst year of €1.85m–€3.60m. Don't pitch it.
* **The HedgeWise optimizer doesn't beat the simple rules in this backtest.** It hedges *more* when volatility is high, and volatility is high when prices are already high (H2 2022), so it locked in crisis prices for 2023. Model-driven hedging on its own is procyclical. Honest framing: the model's job is to *measure* the risk and size the hedge; the *discipline* (tranches and layering) is what protects you. Fixing the procyclicality (a vol-target that doesn't chase, or mean reversion in gas) is the next modelling step.

## What the model says (anchored, γ = 0.5, 10,000 scenarios)

| As of | Delivery | Expected bill, all variable | Bad case (ES95), all variable | HedgeWise: share fixed | Bad case with HedgeWise | Extra expected cost |
|---|---|---|---|---|---|---|
| 30 Jun 2021 | 2022 | €0.71m | €1.91m | 70% | €1.27m | €25k |
| 30 Jun 2022 | 2023 | €2.31m | €7.96m | 90% | €4.28m | €104k |
| 23 Nov 2026 (today) | 2027 | €2.02m | €4.82m | 55% | €3.34m | €56k |

Today's frontier: each extra 10% fixed costs about €10k on average and removes about €260k from the bad case. The exact recommended share moves by ±5 percentage points with the number of scenarios (2,000 in the dashboard vs 10,000 here).

## Stress test: "what if 2022 happens again?" (two scenarios, revised)

**Historical stress scenarios, not forecasts.** The v0.4 scenario applied the 2022/H1-2021 ratios to today's level. That implied gas at about €550/MWh for a whole year, which isn't credible, so it has been replaced by two scenarios:

* **(a) 2022 prices repeat:** the realised 2022 monthly gas and power prices, exactly as they were.
* **(b) A shock as big as 2021→2022, from today:** today's fixed price × (2022 annual average / 2021 annual average), with the 2022 monthly shape. The multipliers are gas ×2.81 (€47.1 → €132.1) and power ×2.35 (€103.0 → €241.9).

The fixed share is priced at today's fixed price. "Implied avg" is the volume-weighted average price the company would pay if it stayed variable. Hypothetical food processor, €m:

| Decision date (plan) | Scenario | Implied avg gas / power (€/MWh) | All variable | HedgeWise plan | Fix 60% now | Fix 100% now |
|---|---|---|---|---|---|---|
| 30 Jun 2021 (fix 70%) | 2022 repeats | 124 / 243 | 2.67 | 1.32 | 1.52 | 0.75 |
| | shock from today | 80 / 183 | 1.86 | 1.08 | 1.19 | 0.75 |
| 30 Jun 2022 (fix 90%) | 2022 repeats | 124 / 243 | 2.67 | 2.45 | 2.53 | 2.43 |
| | shock from today | 295 / 527 | 6.10 | 2.80 | 3.90 | 2.43 |
| **23 Nov 2026, today (fix 55%)** | 2022 repeats | 124 / 243 | **2.67** | **2.37** | 2.34 | 2.12 |
| | shock from today | 253 / 471 | **5.32** | **3.56** | 3.40 | 2.12 |

* Sanity check: as of 30 Jun 2021, "2022 repeats" is what actually happened, and its all-variable bill (€2.67m) equals the realised 2022 bill in the backtest.
* Today's fixed prices (gas €96, power €199/MWh) are already close to the 2022 averages, so "2022 repeats" adds only about €0.65m to a variable bill. The shock scenario is the one that hurts: €5.32m variable vs €3.56m with the plan.
* By construction, fixing more always looks better in a price-rise scenario. The frontier and the backtest show the other side (2023, when prices fell).
* It appears in the dashboard, the PDF and the CLI (with the implied prices), and in `output/stress_<date>.png`.

## Calibration

The realised 2021 and 2022 bills were above the model's 95th percentile (2 of 5 years outside the 5–95% range; a calibrated model would miss ~1 in 10). After the crisis the bands are very wide (2023 ES95 €7.7m against €0.99m realised). GARCH reacts to volatility after it has happened, so it is too calm before a crisis and too nervous after one. Next step: a stress scenario on top (for example "2022 repeats") and/or a regime-switching component.

## Limitations

1. No real forward prices yet: the fixed price is 30-day spot (power seasonally adjusted) + 5%. The layered strategy uses the same proxy two years ahead, which is rougher still. The loader is ready: put an EEX/ICE Endex export in `data/forwards.csv` and every number switches to real forwards (the backtest prints how many fixed prices came from each source).
2. Commodity cost only; hypothetical company.
3. The model is fat-tailed but backward-looking (see calibration).
4. Five delivery years is a tiny sample: treat the backtest as an illustration, not proof.
5. For the 2021 layered tranches (H2 2019), there wasn't a year of history yet to fit the power seasonality, so those prices aren't seasonally adjusted.
