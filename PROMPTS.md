# HedgeWise: Claude Code prompts, in order

Run one prompt per session: open Terminal, `cd ~/hedgewise`, type `claude`, paste the prompt.
When a session finishes, run the REVIEW prompt at the bottom in the same session.
Send the summary to the claude.ai chat before you start the next prompt.

---

## Prompt 1: fixes (stress test, PDF, HedgeWise ladder)

```text
Context: ~/hedgewise is HedgeWise, an energy price-risk tool for mid-sized Dutch companies (Brightlands
startup competition, Speeddate 8 Dec). Read README.md and RESULTS.md first.
Rules: don't change the model maths or existing results unless a task says so. Never choose a model or
rule because it looks good on the 2021-2025 backtest. After each task: run pytest and the scripts you
touched, open every chart and PDF you produced and fix overlapping or cut-off text, then git commit.
If something blocks you, skip it, write down why, and continue. End with a short summary with numbers.

1. Stress test: replace the current scenario (2022 vs H1-2021 ratios applied to today's level; it
   implies gas ~550 EUR/MWh for a whole year, which is not credible) with two clearly labelled scenarios:
   (a) "2022 prices repeat": the realised 2022 monthly gas and power prices as they were;
   (b) "A shock as big as 2021->2022, from today": today's fixed price x (2022 annual average /
   2021 annual average) per commodity, with the 2022 monthly shape.
   Show both in the app chart, the PDF and the CLI, and print the implied average gas and power price
   of each scenario so a reader can sanity-check it.
2. PDF: "m³" renders as "m " because built-in Helvetica has no superscript 3. Register and embed a TTF
   (DejaVu Sans from matplotlib's font folder, no download) for all report text, in EN and NL.
3. PDF: use the empty bottom third of the page for a small stress chart. Keep it to one page.
4. Trade-off chart: add one sentence under it that makes the scale explicit ("fixing everything costs
   EUR X more on average and cuts the bad case by EUR Y"), in EN and NL.
5. Add the strategy "HedgeWise ladder": 40% bought in 6 monthly tranches in H2 of Y-2, then up to the
   model's target in H2 of Y-1; the target may not exceed the previous season's target by more than
   10 points. Add it to backtest.py and RESULTS.md next to the others, with an explicit note that this
   rule was designed after seeing the 2021-2025 results, so the backtest is not independent evidence.
```

---

## Prompt 2: code quality and structure (results must stay identical)

```text
Context: ~/hedgewise is HedgeWise, an energy price-risk tool for mid-sized Dutch companies (Brightlands
startup competition, Speeddate 8 Dec). Read README.md and RESULTS.md first.
Rules: this task must NOT change any number. Before you start, save the outputs of
`python backtest.py` and `python hedgewise_model.py` (all three as-of dates) and the test results;
after every step, check they are identical. Commit after each step. End with a short summary.

1. Split hedgewise_model.py into a package hedgewise/ (data.py, company.py, models.py (GARCH, power),
   simulate.py, strategies.py (analyse, policies, stress), charts.py, report.py, audit-ready
   interfaces). Keep thin wrappers so `python hedgewise_model.py`, `python backtest.py`,
   `python report.py` and `streamlit run app.py` work exactly as before.
2. Type hints and docstrings on all public functions; ruff (lint + format) clean; no magic numbers in
   code: move every assumption (premium, lookback, floors, caps, scenario count, seed, solar yield,
   sector profiles, company default) into config.yaml, loaded once, with comments explaining each.
3. Reproducibility: every output file (CSV, JSON, PNG metadata, PDF footer) records the config hash,
   the data date range and a hash of the data files. Add requirements.lock with exact versions.
4. Speed: profile the app. The "Today" view must appear in under 5 seconds on a laptop: cache the fit,
   vectorise the simulation loop where it helps. Report before/after timings.
5. Tests: add tests until non-plotting code has 80%+ coverage (pytest-cov), including a "golden
   numbers" test that fails if the backtest totals change by more than EUR 1.
6. GitHub Actions workflow: ruff + pytest on every push (Python 3.11 and 3.12).
```

---

## Prompt 3: out-of-sample model validation

```text
Context: ~/hedgewise is HedgeWise, an energy price-risk tool for mid-sized Dutch companies (Brightlands
startup competition, Speeddate 8 Dec). Read README.md and RESULTS.md first.
Rules: never choose a model because it looks good on the 2021-2025 backtest. Choose only on the
out-of-sample scores below. Run tests, look at every chart, commit after each step, short summary
with numbers at the end.

1. validation.py: every month from 2020-06 to today, fit on data up to that date only (no look-ahead;
   add a test that proves it), and forecast the distribution of the next 1-, 3- and 12-month average
   gas and power price. Compare with what happened. Report per horizon and commodity:
   - coverage of the 5-95% band (target ~90%) and of the 1-99% band,
   - Kupiec and Christoffersen test p-values for the 95% quantile,
   - CRPS (continuous ranked probability score, lower is better) and the pinball loss at 95%.
2. Compare 5 variants on exactly the same test:
   a) current GARCH-t; b) GJR-GARCH-t (price rises raise volatility more than falls);
   c) GARCH-t with a jump component or a 2-regime (calm/crisis) switching variance;
   d) the current model with the anchoring to the fixed price switched off;
   e) a historical block-bootstrap benchmark (no model).
   Keep each implementation small, documented and tested on synthetic data with known parameters
   (fit should recover them within a tolerance).
3. Output output/validation_table.csv and output/validation.png (coverage and CRPS per variant and
   horizon). Write a "Model validation" section in RESULTS.md and METHODOLOGY.md with the results,
   including where every model fails.
4. Make the default the variant with the best 12-month CRPS AND acceptable coverage (80-95%). If it
   changes, rerun backtest.py, report.py and the three as-of runs, and update RESULTS.md, clearly
   marking old vs new numbers. If no variant is clearly better (CRPS within 3%), keep the current one.
```

---

## Prompt 4: robustness beyond 5 years and one country

```text
Context: ~/hedgewise is HedgeWise, an energy price-risk tool for mid-sized Dutch companies (Brightlands
startup competition, Speeddate 8 Dec). Read README.md and RESULTS.md first.
Rules: don't tune any strategy to the results. Report honestly where our preferred strategies lose.
Run tests, look at every chart, commit after each step, short summary with numbers at the end.

1. robustness.py, synthetic histories: simulate 1,000 synthetic 8-year daily price histories (gas
   and power, jointly) from the default model fitted on all data up to today, plus a historical
   block bootstrap version, plus 3 named stress histories: "2022 repeats", "slow 3-year rise",
   "spike then crash". Run every backtest strategy on each (same decision rules and fixed-price
   proxy as backtest.py; the model inside HedgeWise strategies is re-fitted on each synthetic history
   at each decision date, use fewer scenarios if needed for speed and say so).
   Report per strategy: median 8-year total, 95th percentile of the worst year, 95th percentile of the
   biggest year-on-year jump, and % of histories where it beats all-variable on worst year AND costs
   less than 8% more in total. Chart: output/robustness.png (cost vs risk scatter, one dot per strategy,
   with 5-95% whiskers).
2. Other markets: add German (Energy-Charts bzn "DE-LU") and Belgian ("BE") day-ahead power to
   download_data.py, extend all series as far back as the free sources allow, and rerun the
   real-data backtest per country (same TTF gas). Table per country in RESULTS.md.
3. Write a "Robustness" section in RESULTS.md: which strategy is best on which measure, how often,
   and where it fails. Update output/pitch_cycle.png only if the conclusion changes.
```

---

## Prompt 5: procurement audit ("what would HedgeWise have saved you?")

```text
Context: ~/hedgewise is HedgeWise, an energy price-risk tool for mid-sized Dutch companies (Brightlands
startup competition, Speeddate 8 Dec). Read README.md and RESULTS.md first.
Rules: use only real historical prices; every number must be reproducible by hand for a simple case.
Run tests, look at every chart/PDF, commit after each step, short summary at the end.

1. audit.py: the user enters their actual past purchases per delivery year and commodity: share
   fixed, fixed price (EUR/MWh) or "variable", and the date they fixed (optional). With their
   consumption profile (company_input.py) compute their actual commodity cost per year and compare
   with: all variable, fix 100% in Oct, tranches 60%, the 2-year ladder and the HedgeWise ladder.
   Handle partial data (e.g. only 2022-2024) and contracts quoted in ct/kWh or EUR/m3 (convert).
2. App tab "Your past contracts" (EN/NL): input table, results table, chart, and one sentence
   ("you paid EUR X over 2021-2025; the 2-year ladder would have cost EUR Y; your worst year would have
   been EUR Z lower/higher"). Never hide a result where the company did better than our strategies.
3. Optional second page in the PDF report with the audit.
4. Tests: a made-up company whose results are checked against hand calculations in the test file.
```

---

## Prompt 6: green investments as a hedge

```text
Context: ~/hedgewise is HedgeWise, an energy price-risk tool for mid-sized Dutch companies (Brightlands
startup competition, Speeddate 8 Dec). Read README.md and RESULTS.md first.
Rules: every assumption goes in config.yaml with a source or "assumption" comment. Run tests, look at
every chart, commit after each step, short summary with numbers at the end.

1. Use the hourly / 15-minute price file (data/power_nl_hourly.csv) to build an hourly price shape per
   month, and use it to simulate hourly prices inside each simulated month (scale the shape to the
   simulated monthly level). Keep the monthly model as it is.
2. Solar: hourly production profile (Dutch typical irradiance shape per month), self-consumption vs
   export at the hourly price (export valued at 80% of day-ahead, parameter), capex and EUR/kWp so
   the app can show payback in expected AND bad-case scenarios.
3. Battery: capacity (kWh), power (kW), round-trip efficiency 88%, simple daily strategy (charge in
   the cheapest hours, discharge in the most expensive, plus peak shaving), capex; show its effect on
   expected and bad-case bill.
4. PPA (long-term green power contract): share of power volume, fixed price, years; show it as a hedge.
5. Put each investment on the trade-off chart as an extra point ("+ solar 1 MWp", "+ PPA 40%",
   "+ battery 1 MWh"), and add an app section "Green investments" (EN/NL) with the cut in expected
   and bad-case bill, simple payback in normal vs crisis conditions, and the assumptions listed.
6. Add one line on green investments to the PDF report when the user selected any.
```

---

## Prompt 7: market size from real data

```text
Context: ~/hedgewise is HedgeWise, an energy price-risk tool for mid-sized Dutch companies (Brightlands
startup competition, Speeddate 8 Dec). Read README.md and RESULTS.md first.
Rules: every number traceable to a source table ID or a written assumption. Commit, short summary.

1. market.py: use CBS StatLine open data (OData API, no account; if unreachable, list the exact table
   IDs and column names and read CSVs I download by hand into data/cbs/). Get the number of companies
   by sector (SBI) and size class, and energy use by sector, for the Netherlands and for Limburg where
   available.
2. Define the target market with parameters: 10-250 employees, energy-intensive sectors (food,
   chemicals, metal, paper, plastics, horticulture, ...), estimated gas + power use above a threshold.
   Estimate the number of target companies (NL and Limburg) and their total energy spend.
3. Revenue model with parameters: subscription EUR 3,000/yr + EUR 150 per GWh consumed, adoption
   1% / 5% / 10%. Output a table (TAM / SAM / SOM style but with plain labels) and output/market.png.
4. MARKET.md: method, sources, all assumptions, and the numbers. Plain English, one page.
```

---

## Prompt 8: handover to the team and final check

```text
Context: ~/hedgewise is HedgeWise, an energy price-risk tool for mid-sized Dutch companies (Brightlands
startup competition, Speeddate 8 Dec). Read README.md, RESULTS.md, METHODOLOGY.md first.
My teammates don't code; they start interviews this weekend. Commit after each step, short summary.

1. App: Dutch/English switch for every text; an "Interview mode" button that hides the model settings
   and goes: company name -> upload bill or yearly totals -> past contracts (optional) -> results ->
   download PDF. It should take under 2 minutes for a non-coder.
2. TEAM_GUIDE.md in plain English: start the app (local and the online link), make a report for an
   interviewee step by step with screenshots (take them with a headless browser), enter past
   contracts, what each number means, which claims we may and may not make in the pitch, what data to
   ask an interviewee for, and the 15 hardest questions a judge could ask about the model with short
   honest answers.
3. METHODOLOGY.md (max 2 pages): data, model, anchoring, objective, validation, robustness, green
   investments, limitations. README: a pipeline diagram (mermaid) and the commands.
4. Final check from a fresh clone in a temporary folder: install from requirements.lock -> ruff ->
   pytest -> backtest -> validation -> robustness (small run) -> app starts -> PDF in EN and NL.
   Check that every number in README, RESULTS, TEAM_GUIDE and the app agrees. List anything left.
```

---

## REVIEW prompt (run at the end of every session)

```text
Act as a strict reviewer (a quant from a trading firm plus a senior Python engineer). Review every change
since the last commit before this session (git diff). Check:
1. Look-ahead: does any decision, fit or fixed price use data after its decision date? Prove it with
   a test if not already covered.
2. Correctness: units (m3, MWh, kWh, EUR, EUR k, EUR m), signs, annual vs monthly, rounding, off-by-one
   dates, leap years, the 15-minute power data since Oct 2025.
3. Consistency: do README, RESULTS, the app, the PDF and the CLI show the same numbers?
4. Honesty: does any text claim more than the results show (cherry-picked years, "proves", "always")?
5. Code: dead code, duplicated logic, untested branches, slow loops, unclear names.
Fix everything you find, rerun all tests and scripts, commit, and list what you found and fixed.
```
