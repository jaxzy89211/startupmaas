"""Task 1: the HedgeWise ladder as the recommendation. Task 2: the honest bad case (never below the 2021->22 shock)."""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hedgewise as hw  # noqa: E402
from hedgewise import strategies as S  # noqa: E402
from hedgewise.texts import TEXT  # noqa: E402


@pytest.fixture(scope="module")
def df():
    return hw.load_prices()


@pytest.fixture(scope="module")
def base(df):
    return hw.analyse(hw.fit(df), hw.Company(), None, 3000, 42, 0.5)


def test_hedge_costs_reproduce_analyse(base):
    for k, v in base.costs.items():
        if k == "All variable":
            assert np.allclose(S.hedge_costs(base, 0, 0, "now"), v)
        elif k == "Fix 100% now":
            assert np.allclose(S.hedge_costs(base, 1, 1, "now"), v)
        elif k.startswith("Tranches"):
            assert np.allclose(S.hedge_costs(base, 1, 1, "tranches"), v)
        elif k.startswith("HedgeWise"):
            r = base.recommendation
            assert np.allclose(S.hedge_costs(base, r["hedge_gas"], r["hedge_power"], r["execution"]), v)


@pytest.mark.parametrize("prev, cap", [(None, 0.50), (0.30, 0.40), (0.60, 0.70), (0.95, 1.05)])
def test_ladder_rule(base, prev, cap):
    raw = base.recommendation["hedge_gas"]
    a = S.apply_ladder(base, prev)
    r = a.recommendation
    assert r["rule"] == "ladder" and r["ladder_cap"] == pytest.approx(cap) and r["model_target"] == raw
    assert r["hedge_gas"] == pytest.approx(min(1.0, max(0.40, min(raw, cap))))
    assert list(a.costs)[-1].startswith("HedgeWise ladder (") and any(k.startswith("Model-only (") for k in a.costs)
    assert not any(k.startswith("HedgeWise (") for k in a.costs)


def test_ladder_next_year_base_layer_and_guards(df, base):
    nx = S.apply_ladder(base).recommendation["next_year"]  # 23 Nov 2026: H2 of Y-2 for 2028, 2 months left
    assert nx == {"year": 2028, "base": 0.40, "buy_now": pytest.approx(0.20), "months_left": 2}
    spring = hw.analyse(hw.fit(df, "2026-03-31"), hw.Company(), None, 500, 1, 0.5)
    assert S.apply_ladder(spring).recommendation["next_year"] is None
    with pytest.raises(ValueError):
        S.apply_ladder(hw.analyse(hw.fit(df), hw.Company(), None, 500, 1, 0.8))
    b = hw.analyse(hw.fit(df), hw.Company(), None, 500, 1, 0.5, budget="auto")
    assert S.apply_ladder(b) is b


def test_recommend_rules(df):
    m = hw.fit(df)
    lad = hw.recommend(m, hw.Company(), df, None, 1000, 1)
    mod = hw.recommend(m, hw.Company(), df, None, 1000, 1, rule="model-only", risk_aversion=0.8)
    assert lad.recommendation["rule"] == "ladder" and mod.recommendation["rule"] == "model-only"
    assert list(mod.costs)[-1].startswith("Model-only (")
    with pytest.raises(ValueError):
        hw.recommend(m, hw.Company(), df, rule="magic")


def test_honest_bad_case_is_never_below_either_input(df, base):
    a = S.honest_bad_case(S.apply_ladder(base), df)
    t, f = a.table, a.frontier
    assert (t.bad_case >= t.es95 - 1e-6).all() and (t.bad_case >= t.shock - 1e-6).all()
    assert (f.bad_case >= f.es95 - 1e-6).all() and (f.bad_case >= f.shock - 1e-6).all()
    for k in t.index:  # the label names the input that wins
        assert a.bad_case_source[k] == ("stress" if t.loc[k, "shock"] > t.loc[k, "es95"] * (1 + 1e-9) else "model")
    # the shock bill of the plan equals the stress test's 'shock' scenario
    st = hw.stress_test(a, df)["scenarios"][1]["bills"]
    assert t.iloc[-1]["shock"] == pytest.approx(st["HedgeWise"]) and t.loc["All variable", "shock"] == pytest.approx(
        st["All variable"]
    )
    assert S.bad_case(a) == (pytest.approx(t.iloc[-1]["bad_case"]), a.bad_case_source[t.index[-1]])
    # the recommendation is not changed by the bad-case definition (mean-variance objective)
    assert a.recommendation == S.apply_ladder(base).recommendation


def test_labels_en_nl():
    assert (
        TEXT["en"]["bad_from"].format(src=TEXT["en"]["bad_src"]["stress"]).endswith("(source: 2021->22 shock)</font>")
    )
    assert TEXT["nl"]["bad_from"].format(src=TEXT["nl"]["bad_src"]["model"]).endswith("(bron: model)</font>")
    assert "72% of outcomes instead of 90%" in TEXT["en"]["honest_note"]
    assert "72% van de uitkomsten in plaats van 90%" in TEXT["nl"]["honest_note"]


def test_no_look_ahead_in_the_honest_bad_case(df):
    """Before the end of 2022 the 2021->22 shock is not known yet: the bad case stays the model's ES95."""
    a = hw.recommend(hw.fit(df, "2021-06-30"), hw.Company(), df, None, 500, 1)
    assert "bad_case" not in a.table and hw.bad_case(a) == (pytest.approx(a.table.iloc[-1]["es95"]), "model")
    b = hw.recommend(hw.fit(df, "2023-06-30"), hw.Company(), df, None, 500, 1)
    assert "bad_case" in b.table
