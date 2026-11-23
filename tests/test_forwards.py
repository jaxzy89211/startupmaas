"""forwards.py: parsing, strict as-of lookup, and the switch from proxy to real forwards."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import forwards  # noqa: E402
import hedgewise_model as hw  # noqa: E402

EXAMPLE = ROOT / "data" / "forwards_example.csv"


@pytest.mark.parametrize(
    "name, parsed",
    [
        ("TTF Cal-27", ("gas", 2027)),
        ("TTF Gas CAL27", ("gas", 2027)),
        ("NL Base Cal-27", ("power", 2027)),
        ("Dutch Power Baseload Cal 2027", ("power", 2027)),
        ("NL Peak Cal-27", None),
        ("TTF Q1-27", None),
        ("TTF Month Jan-27", None),
        ("something else", None),
    ],
)
def test_parse_product(name, parsed):
    assert forwards.parse_product(name) == parsed


def test_latest_quote_on_or_before_as_of():
    assert forwards.forward_price("gas", "2026-11-20", 2027, EXAMPLE) == pytest.approx(73.10)
    assert forwards.forward_price("gas", "2026-11-15", 2027, EXAMPLE) == pytest.approx(72.25)  # Dutch decimals
    assert forwards.forward_price("gas", "2026-10-01", 2027, EXAMPLE) is None  # nothing yet
    assert forwards.forward_price("power", "2026-11-23", 2027, EXAMPLE) == pytest.approx(151.40)  # base, not peak
    assert forwards.forward_price("power", "2026-11-23", 2030, EXAMPLE) is None


def test_no_file_means_none(tmp_path):
    assert forwards.forward_price("gas", "2026-11-20", 2027, tmp_path / "missing.csv") is None


def test_fake_example_is_not_the_live_file():
    live = forwards.FORWARDS_PATH
    if live.exists():
        assert "FAKE" not in live.read_text(errors="ignore"), "data/forwards.csv contains the fake example data"


def test_switch_proxy_to_forward(monkeypatch):
    df = hw.load_prices()
    m = hw.fit(df, "2026-11-20")
    monkeypatch.setattr(forwards, "FORWARDS_PATH", ROOT / "data" / "does_not_exist.csv")
    a0 = hw.analyse(m, hw.Company(), None, n_paths=300, seed=1)
    assert a0.recommendation["price_source"]["gas"].startswith("proxy")
    monkeypatch.setattr(forwards, "FORWARDS_PATH", EXAMPLE)
    a1 = hw.analyse(m, hw.Company(), None, n_paths=300, seed=1)
    assert a1.fwd_now["gas"] == pytest.approx(73.10) and a1.fwd_now["power"] == pytest.approx(151.40)
    assert a1.recommendation["price_source"]["gas"].startswith("forward TTF Cal-27")
    # the forward is the anchor: E[all-variable bill] = fixed-price bill / (1 + premium)
    fixed = a1.company.gas_mwh.sum() * 73.10 + a1.company.power_mwh.sum() * 151.40
    assert a1.costs["All variable"].mean() == pytest.approx(fixed / (1 + hw.FORWARD_PREMIUM), rel=1e-3)
    # a delivery that doesn't start on 1 January can't use a calendar product -> proxy
    a2 = hw.analyse(m, hw.Company(), "2027-04-01", n_paths=300, seed=1)
    assert a2.recommendation["price_source"]["gas"].startswith("proxy")


def test_other_hubs_and_countries_are_rejected():
    from hedgewise.forwards import parse_product

    assert parse_product("TTF Cal-27") == ("gas", 2027) and parse_product("Dutch Power Base Cal-27") == ("power", 2027)
    for name in (
        "NBP Gas Cal-27",
        "THE Gas Cal-27",
        "German Power Base Cal-27",
        "Phelix DE Base Cal-27",
        "Belgian Power Base Cal 2027",
        "PEG Cal-27",
    ):
        assert parse_product(name) is None, name
