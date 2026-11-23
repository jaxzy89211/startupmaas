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
    # the purchases add up: base layer + all top-ups = the share fixed
    assert bt.LADDER_BASE + sum(p["bought"] for p in log) == pytest.approx(info["ladder_fixed_share"], abs=1e-3)
    # never front-loaded: the first top-up is at most an equal 1/6 share of the gap to the (capped) target
    assert log[0]["bought"] <= (log[0]["target"] - bt.LADDER_BASE) / 6 + 1e-4   # log rounds to 4 decimals
