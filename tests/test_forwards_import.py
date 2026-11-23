"""Forward-price importers: one small FAKE file per vendor layout (tests/fixtures/forwards/)."""

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from hedgewise import forwards_import as fi  # noqa: E402

FX = ROOT / "tests" / "fixtures" / "forwards"


def run(name):
    return fi.normalise(fi.read_any(FX / name), name)


def test_refinitiv_long_keeps_only_the_calendar_year():
    out, rep = run("refinitiv_long.csv")
    assert rep["format"] == "refinitiv" and list(out["product"].unique()) == ["TTF Cal-27"]
    assert out.price.tolist() == [92.10, 93.40, 94.05] and rep["kept"] == 3 and sum(rep["rejected"].values()) == 1


def test_codes_are_mapped_from_config(monkeypatch):
    out, rep = run("refinitiv_wide.csv")
    assert rep["kept"] == 0 and set(rep["rejected_examples"]) == {"TFMBYZ7", "TRNLBYZ7"}  # unknown codes: reported
    monkeypatch.setattr(fi, "CODES", {"TFMBYZ7": "TTF Cal-27", "TRNLBYZ7": "NL Base Cal-27"})
    out, rep = run("refinitiv_wide.csv")
    assert rep["kept"] == 4 and set(out["product"]) == {"TTF Cal-27", "NL Base Cal-27"}


def test_bloomberg_rejects_other_hubs():
    out, rep = run("bloomberg_long.csv")
    assert rep["format"] == "bloomberg" and set(out["product"]) == {"TTF Cal-27", "NL Base Cal-27"}
    assert "NBP Gas Cal 2027 Comdty" not in set(out.source.str.replace("Bloomberg ", ""))  # NBP is not TTF
    assert out.date.iloc[0] == pd.Timestamp("2026-11-18")


def test_eex_baseload_calendar_only_dutch_numbers():
    out, rep = run("eex_datasource.csv")
    assert rep["format"] == "eex" and list(out["product"].unique()) == ["NL Base Cal-27"]
    assert out.price.tolist() == [186.0, 188.5] and rep["kept"] == 2  # peak and Q1 rejected


def test_ice_units_and_strips():
    out, rep = run("ice_endex.csv")
    assert rep["format"] == "ice" and list(out["product"].unique()) == ["TTF Cal-27"]
    assert out.price.tolist() == [92.10, 93.0]  # 9.30 ct/kWh -> 93.0 EUR/MWh; Dec-26 and GBp/therm rejected
    p = rep["products"][0]
    assert p["gaps"] == ["2026-11-18 -> 2026-12-02 (9 business days)"]
    assert "gaps" in fi.report_text(rep)


def test_our_own_format_and_cli(tmp_path):
    out, rep = fi.normalise(fi.read_any(ROOT / "data" / "forwards_example.csv"), "example")
    assert rep["format"] == "hedgewise" and set(out["product"]) == {
        "TTF Cal-27",
        "NL Base Cal-27",
        "TTF Cal-22",
        "NL Base Cal-22",
    }
    target = tmp_path / "forwards.csv"
    fi.main([str(FX / "ice_endex.csv"), str(FX / "eex_datasource.csv"), "--out", str(target)])
    w = pd.read_csv(target)
    assert list(w.columns) == ["date", "product", "price", "source"] and len(w) == 4
    with pytest.raises(SystemExit):  # never overwrite without --force
        fi.main([str(FX / "ice_endex.csv"), "--out", str(target)])


def test_unknown_format():
    with pytest.raises(ValueError, match="unknown export format"):
        fi.normalise(pd.DataFrame({"a": [1], "b": [2]}))
