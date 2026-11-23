"""policy_plan: equal monthly tranches, never front-loaded, target rise capped at 20 points."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hedgewise_model as hw  # noqa: E402


def fake(target):
    return SimpleNamespace(recommendation={"hedge_gas": target, "hedge_power": target})


def test_equal_tranches_not_front_loaded():
    p = hw.policy_plan(fake(0.6), months_ahead=6)
    assert p["buy_now"] == pytest.approx(0.1)
    assert all(x == pytest.approx(0.1) for x in p["schedule"]) and sum(p["schedule"]) == pytest.approx(0.6)


def test_cap_on_rise_between_dates():
    p = hw.policy_plan(fake(0.9), months_ahead=4, prev_target=0.3, already=0.1)
    assert p["capped"] and p["target"] == pytest.approx(0.5)
    assert p["buy_now"] == pytest.approx((0.5 - 0.1) / 4)


def test_first_date_uncapped_and_falls_are_free():
    assert hw.policy_plan(fake(0.9), 6)["target"] == pytest.approx(0.9)
    p = hw.policy_plan(fake(0.2), 3, prev_target=0.8, already=0.5)
    assert p["target"] == pytest.approx(0.2) and p["buy_now"] == 0  # never sells back


def test_cap_still_allows_full_hedge_in_one_season():
    target, done = None, 0.0
    for n_left in range(6, 0, -1):
        p = hw.policy_plan(fake(1.0), n_left, target, done)
        target, done = p["target"], done + p["buy_now"]
    assert done == pytest.approx(1.0)
