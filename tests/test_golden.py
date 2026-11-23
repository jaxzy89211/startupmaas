"""Golden numbers: the full backtest must reproduce the saved totals to within EUR 1 (any strategy, any year)."""

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import hedgewise as hw  # noqa: E402
from hedgewise import backtest  # noqa: E402

GOLDEN = json.loads((ROOT / "tests" / "golden_backtest.json").read_text())
TOLERANCE_EUR = 1.0


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    out = tmp_path_factory.mktemp("out")
    mp = pytest.MonkeyPatch()
    mp.setattr(hw, "OUT", out)  # backtest writes to hw.OUT: keep the real output/ folder untouched
    try:
        res = backtest.main([])
    finally:
        mp.undo()
    return res, out


def test_backtest_totals_match_golden(run):
    res, _ = run
    assert list(res.index) == GOLDEN["years"]
    for strategy, total in GOLDEN["totals_eur"].items():
        assert res[strategy].sum() == pytest.approx(total, abs=TOLERANCE_EUR), strategy


def test_backtest_per_year_match_golden(run):
    res, _ = run
    for strategy, years in GOLDEN["per_year_eur"].items():
        for year, value in years.items():
            assert res.loc[int(year), strategy] == pytest.approx(value, abs=TOLERANCE_EUR), (strategy, year)


def test_backtest_outputs_carry_provenance(run):
    _, out = run
    head = (out / "backtest_results.csv").read_text().splitlines()[:8]
    assert any(line.startswith("# config_hash:") for line in head)
    assert any(line.startswith("# data_hash:") for line in head)
    back = pd.read_csv(out / "backtest_results.csv", comment="#", index_col=0)
    assert back.shape[0] == len(GOLDEN["years"])
    details = json.loads((out / "backtest_details.json").read_text())
    assert {"config_hash", "data_hash", "data_from", "data_to"} <= set(details["_provenance"])
    for png in ("pitch_2022.png", "pitch_cycle.png", "backtest_all_years.png", "backtest_calibration.png"):
        assert (out / png).exists()
