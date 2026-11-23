# Pre-registration: the HedgeWise ladder is our rule

**Date:** 23 November 2026
**Rule committed in:** `5023af4aad018666502905e6eea4566fe7067027` (also in `config.yaml`, `backtest.ladder_rule_commit`)
**Code:** `hedgewise/strategies.py` → `apply_ladder()` / `recommend(rule="ladder")`; the same rule in the backtest
(`hedgewise/backtest.py`, column "HedgeWise ladder (40% + capped top-up)") and the robustness study.

## The rule (exact)

For delivery year Y:
1. **Base layer:** buy 40% of Y's gas and power volume in 6 equal monthly tranches in the second half of Y-2
   (end of June to end of November in the backtest; a new client catches up over the months left).
2. **Top-up:** in the second half of Y-1, top up in equal monthly tranches to the **target**:
   * the model target = the share the optimiser picks at risk aversion **γ = 0.5** (mean-variance objective,
     anchored model, one share for gas and power, 5% steps),
   * kept **at least 40%** (the base layer) and **at most the previous season's target + 10 points**
     (no previous season: 40% + 10 = 50%),
   * never sold back, never front-loaded.
3. Fixed prices: real forwards when available, otherwise the proxy (30-day average spot, power seasonally adjusted,
   + 5%), exactly as in `config.yaml` on the commit above.

Parameters in `config.yaml` (`backtest`): `ladder_base: 0.40`, `ladder_max_step: 0.10`, `policy_gamma: 0.5`.

## Why this rule

The 2021–2025 backtest showed two things: layering purchases over two years cut the worst year and the biggest
year-on-year jump, and the model-driven optimiser was procyclical (it hedged most right after the 2022 spike and
locked in crisis prices for 2023). The ladder combines a fixed base layer with a model top-up whose rise is capped
between seasons, so the model can't chase a spike.

## What is in-sample

**The rule was designed AFTER seeing the 2021–2025 results.** Its numbers on 2021–2025 (NL) and on the 2020–2025
DE-LU and BE backtests, which share that price history, are **in-sample** and are not evidence that it works. The
synthetic futures in RESULTS.md come from a model fitted on the same history: supportive, not independent.

## The test from now on (fixed today)

Every **new delivery year** (2027 onwards, scored after it ends) and every **new market** we add is scored **out of
sample**, with these measures and nothing else, against two benchmarks:

| Measure | vs all variable | vs layered 2 years (40%+40%) |
|---|---|---|
| Total commodity bill over the scored years | difference in € and % | difference in € and % |
| Worst single year | difference | difference |
| Biggest year-on-year increase | difference | difference |

Rules for the test:
* Same code as the commit above (`python backtest.py --years <new years>`; `--market` for a new market). If the code
  must change for data reasons, the rule and its parameters may not change; any change to the rule is a new rule
  with a new pre-registration and a new out-of-sample start.
* All results are reported, including years where the ladder loses.
* No other measure is added after seeing results.
