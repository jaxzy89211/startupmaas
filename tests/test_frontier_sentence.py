"""The scale sentence under the trade-off chart matches the frontier's two ends."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hedgewise_model as hw  # noqa: E402


def test_frontier_scale_matches_strategy_table():
    df = hw.load_prices()
    a = hw.analyse(hw.fit(df, "2024-06-30"), hw.Company(), None, n_paths=500, seed=1)
    x, y = hw.frontier_scale(a)
    f = a.frontier[a.frontier.execution == a.recommendation["execution"]]
    full, none = f[f.hedge_gas == 1.0].iloc[0], f[f.hedge_gas == 0.0].iloc[0]
    assert x == pytest.approx(full.expected - none.expected) and x > 0
    assert y == pytest.approx(none.es95 - full.es95) and y > 0
    assert "Fixing everything costs" in hw.frontier_scale_sentence(a, "en")
    assert "Alles vastzetten kost gemiddeld" in hw.frontier_scale_sentence(a, "nl")
