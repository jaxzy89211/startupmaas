"""Money axes are readable for every company size: unique tick labels and the right unit, in EN and NL.

Each chart is drawn for a small (~EUR 50k bill), medium (~EUR 500k) and large (~EUR 5m) company. Every figure that
is saved is recorded, and every axis whose title names a euro unit is checked after drawing."""

import io
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.figure  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import hedgewise as hw  # noqa: E402
from hedgewise import audit as au  # noqa: E402
from hedgewise import backtest as bt  # noqa: E402
from hedgewise import green as gr  # noqa: E402
from hedgewise import monitor as mo  # noqa: E402
from hedgewise.moneyfmt import log_labels, money_unit, unique_labels  # noqa: E402
from hedgewise.texts import TEXT  # noqa: E402

SIZES = {"small": 0.025, "medium": 0.25, "large": 2.5}  # x the default food processor (expected bill ~EUR 2m)
# The unit follows each axis's own amounts: a medium company's expected bill is ~EUR 500k (k) but its bad case
# reaches ~EUR 1.3m (m), so both units are right there.
UNIT = {
    ("small", "en"): {"€ k"},
    ("medium", "en"): {"€ k", "€ m"},
    ("large", "en"): {"€ m"},
    ("small", "nl"): {"€ duizend"},
    ("medium", "nl"): {"€ duizend", "€ mln"},
    ("large", "nl"): {"€ mln"},
}


@pytest.fixture(scope="module")
def df():
    return hw.load_prices()


@pytest.fixture
def figs(monkeypatch):
    seen = []
    real = matplotlib.figure.Figure.savefig

    def spy(self, *a, **k):
        seen.append(self)
        return real(self, *a, **k)

    monkeypatch.setattr(matplotlib.figure.Figure, "savefig", spy)
    return seen


def company(size):
    c = hw.Company()
    f = SIZES[size]
    return hw.Company(
        name=f"{size} co", gas_m3=tuple(np.array(c.gas_m3) * f), power_kwh=tuple(np.array(c.power_kwh) * f)
    )


def money_axes(fig):
    """(axis object, axis title) for every axis whose title names a euro unit."""
    fig.canvas.draw()
    out = []
    for ax in fig.axes:
        for axis, lab in ((ax.xaxis, ax.get_xlabel()), (ax.yaxis, ax.get_ylabel())):
            if "€" in lab:
                out.append((axis, lab))
    return out


def check(fig, unit, n_min=1):
    axes = money_axes(fig)
    assert len(axes) >= n_min, [a.get_ylabel() for a in fig.axes]
    for axis, lab in axes:
        labels = [t.get_text() for t in axis.get_ticklabels() if t.get_text()]
        assert len(labels) >= 2 and len(set(labels)) == len(labels), (lab, labels)
        assert any(lab.endswith(f"({u})") or lab == u for u in unit), lab


@pytest.mark.parametrize("size", list(SIZES))
@pytest.mark.parametrize("lang", ["en", "nl"])
def test_report_charts(df, figs, size, lang):
    a = hw.recommend(hw.fit(df), company(size), df, None, 1500, 1)
    unit = UNIT[(size, lang)]
    hw.plot_frontier(a, path=io.BytesIO(), text=TEXT[lang]["chart"])
    check(figs[-1], unit, 2)
    hw.plot_stress(hw.stress_test(a, df), io.BytesIO(), lang=lang)
    check(figs[-1], unit)
    t, _ = gr.evaluate(a, gr.Investments(solar_kwp=1000 * SIZES[size], ppa_share=0.4), df=df)
    hw.plot_frontier(
        a,
        path=io.BytesIO(),
        text=TEXT[lang]["chart"],
        extra_points=[(r.investment, r.bad_case, r.expected) for r in t.itertuples()],
    )
    check(figs[-1], unit, 2)


@pytest.mark.parametrize("size", list(SIZES))
def test_audit_and_monitor_charts(df, figs, size):
    c = company(size)
    contracts = au.read_contracts(ROOT / "data" / "audit_example.csv")
    r = au.run_audit(contracts, c, df, n_paths=200)
    for lang in ("en", "nl"):
        au.plot_audit(r, io.BytesIO(), lang)
        # the totals panel and the per-year panel may need different units (totals are ~5x a year)
        fig = figs[-1]
        for axis, lab in money_axes(fig):
            labels = [t.get_text() for t in axis.get_ticklabels() if t.get_text()]
            assert len(set(labels)) == len(labels), (size, lang, lab, labels)
    hist = pd.DataFrame(
        {"run_date": ["2026-10-23", "2026-11-23"], "bad_case_now": np.array([3.28e6, 3.94e6]) * SIZES[size]}
    )
    res = {"status": {"planned": 0.46, "bought": 0.28, "target": 0.55}, "history": hist}
    for lang in ("en", "nl"):
        mo.progress_chart(res, lang, io.BytesIO())
        check(figs[-1], UNIT[(size, lang)])


def test_backtest_and_pitch_charts(figs):
    res = pd.read_csv(ROOT / "output" / "backtest_results.csv", comment="#", index_col=0)
    info = {
        int(k): v for k, v in json.loads((ROOT / "output" / "backtest_details.json").read_text()).items() if k.isdigit()
    }
    for size, f in SIZES.items():
        r, inf = (
            res * f,
            {
                y: {
                    **v,
                    "fix_once_by_month": {k: x * f for k, x in v["fix_once_by_month"].items()},
                    "model_forecast_all_variable": {k: x * f for k, x in v["model_forecast_all_variable"].items()},
                }
                for y, v in info.items()
            },
        )
        unit = UNIT[(size, "en")]
        bt.plot_cycle(r, "Fix 100% in Oct", io.BytesIO())
        check(figs[-1], unit)
        bt.plot_all_years(r, io.BytesIO())
        bt.plot_calibration(inf, r, io.BytesIO())
        check(figs[-1], unit)
        bt.plot_pitch(r, inf, 2022, company(size), io.BytesIO())
        check(figs[-1], unit)
    bt.plot_all_years(res, io.BytesIO())  # totals in EUR m next to yearly bars: both axes unique
    for axis, _ in money_axes(figs[-1]):
        labels = [t.get_text() for t in axis.get_ticklabels() if t.get_text()]
        assert len(set(labels)) == len(labels)


def test_the_helper_itself():
    u = money_unit([132_000, 141_000], "nl")
    assert u.label == "€ duizend" and unique_labels([130_000, 132_500, 135_000, 137_500], u) == [
        "130,0",
        "132,5",
        "135,0",
        "137,5",
    ]
    m = money_unit([2.02e6, 2.12e6], "en")
    assert unique_labels([2.02e6, 2.04e6, 2.06e6], m) == ["2.02", "2.04", "2.06"]
    assert unique_labels([1e6, 1.5e6, 2e6], m) == ["1.0", "1.5", "2.0"]
    assert money_unit([950], "en").label == "€" and money_unit([5e6], "nl").label == "€ mln"
    assert m.text(2.67e6) == "€2.67m" and money_unit([132_000], "nl").text(132_400) == "€ 132.000"
    assert money_unit([132_000], "en").text(132_400) == "€132k"


def test_log_axis_labels():
    m = money_unit([5e7], "en")
    assert log_labels([0.5e6, 1e6, 2e6, 1e7, 1e11], m) == ["0.5", "1", "2", "10", "100,000"]
    assert log_labels([0.5e6, 1e6], money_unit([5e7], "nl")) == ["0,5", "1"]
