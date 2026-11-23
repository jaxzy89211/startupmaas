"""pilot.py on a fake company folder (fixture invoices + a contracts table), in a temp dir."""

import shutil
import sys
from pathlib import Path

import pandas as pd
import pdfplumber
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from hedgewise import pilot  # noqa: E402

FX = ROOT / "tests" / "fixtures" / "invoices"


@pytest.fixture
def folder(tmp_path):
    d = tmp_path / "Testfabriek Zuid"
    d.mkdir()
    for f in ("eneco_jaarnota.pdf", "budget_contract.pdf", "greenchoice_jaaroverzicht.pdf"):
        shutil.copy(FX / f, d / f)
    (d / "contracten.csv").write_text(
        "year,commodity,share,price,unit,fixed_on\n2024,gas,60%,38,EUR/MWh,2023-10-02\n"
        "2024,power,60%,9.1,ct/kWh,2023-10-02\n"
    )
    return d


def _text(p: Path) -> str:
    with pdfplumber.open(p) as pdf:
        return "\n".join(pg.extract_text() or "" for pg in pdf.pages)


def test_full_run_with_alias(folder):
    r = pilot.run(folder, alias="Bedrijf A", name="Testfabriek Zuid", yes=True, n_paths=1000)
    assert r["confirmed"] and {p.name for p in r["files"]} == {
        "report_bedrijf_a_en.pdf",
        "report_bedrijf_a_nl.pdf",
        "sanity_check.pdf",
    }
    for p in r["files"]:
        t = _text(p)
        assert "Testfabriek" not in t, p.name  # the alias replaces the company name everywhere
    assert "Bedrijf A" in _text(folder / "sanity_check.pdf")
    assert r["audit"] is not None and r["audit"].years == [2024]  # page 2 of the report
    assert len(pdfplumber.open(folder / "report_bedrijf_a_nl.pdf").pages) == 2
    assert any("230,400 per year" in f for f in r["flags"])  # Eneco totals disagree with the monthly profile
    m = pd.read_csv(folder / "pilot_numbers.csv")
    assert len(m) == 12 and m.gas_m3.sum() == 97_980


def test_not_confirmed_and_edits_are_used(folder):
    r = pilot.run(folder, yes=False, n_paths=500, ask=lambda q: "n")
    assert r == {"confirmed": False} and (folder / "pilot_numbers.csv").exists()
    m = pd.read_csv(folder / "pilot_numbers.csv")
    m["gas_m3"] = m["gas_m3"] * 2  # the team corrects a number in the CSV
    m.to_csv(folder / "pilot_numbers.csv", index=False)
    res, _, source = pilot.numbers(folder, pilot.read_folder(folder), "food processing", "X")
    assert source == "pilot_numbers.csv (your edits)" and sum(res.company.gas_m3) == pytest.approx(2 * 97_980)


def test_totals_only_folder(tmp_path):
    d = tmp_path / "c"
    d.mkdir()
    shutil.copy(FX / "essent_jaarafrekening.pdf", d)
    res, t, source = pilot.numbers(d, pilot.read_folder(d), "metal", "C")
    assert source.startswith("PDF period totals") and sum(res.company.power_kwh) == pytest.approx(111_900)


def test_empty_folder(tmp_path):
    with pytest.raises(ValueError, match="no consumption found"):
        pilot.run(tmp_path, yes=True)
