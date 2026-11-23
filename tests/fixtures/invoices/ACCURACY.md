# Extraction accuracy on the fixture invoices (rules engine)

Five FAKE invoices in the layouts of publicly published Dutch bill explanations (see make_fixtures.py).

| Field | Correct | Checked | Accuracy |
|---|---|---|---|
| supplier | 5 | 5 | 100% |
| period_start | 3 | 3 | 100% |
| period_end | 3 | 3 | 100% |
| contract_type | 5 | 5 | 100% |
| contract_end | 2 | 2 | 100% |
| total power_kwh | 2 | 2 | 100% |
| total gas_m3 | 2 | 2 | 100% |
| price power (EUR/MWh) | 4 | 4 | 100% |
| price gas (EUR/MWh) | 4 | 4 | 100% |
| monthly values | 26 | 26 | 100% |
| contract_start | 1 | 1 | 100% |
| fixed_on | 1 | 1 | 100% |

Overall: 58 of 58 fields correct (100%).
