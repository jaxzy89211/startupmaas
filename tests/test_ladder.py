"""HedgeWise ladder in the backtest: 40% base layer, top-up capped at previous season's target + 10 points."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backtest as bt  # noqa: E402
import hedgewise_model as hw  # noqa: E402


@pytest.fixture(scope="module")
def df():
    return hw.load_prices()


@pytest.mark.parametrize("prev, cap", [(None, 0.50), (0.70, 0.80)])
def test_cap_and_base(df, prev, cap):
    _, info = bt.run_year(df, hw.Company(), 2023, [0.5], 300, 1, 10, verbose=False, prev_ladder_target=prev)
    log = info["ladder"]
    assert all(p["cap"] == pytest.approx(cap) for p in log)
    assert all(bt.LADDER_BASE - 1e-9 <= p["target"] <= cap + 1e-9 for p in log)
    assert bt.LADDER_BASE - 1e-9 <= info["ladder_fixed_share"] <= cap + 1e-9
    # never front-loaded: no single month buys more than the remaining gap spread over the remaining months
    assert all(p["bought"] <= (cap - bt.LADDER_BASE) / 1 + 1e-9 for p in log)
