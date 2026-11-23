"""Command lines and the PDF report (run end to end on small scenario counts, writing to a temp folder)."""

import io
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import hedgewise as hw  # noqa: E402
from hedgewise import cli, company_input, forwards, naming, report  # noqa: E402


@pytest.fixture()
def out(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "OUT", tmp_path)
    return tmp_path


def test_cli_as_of_writes_outputs_with_provenance(out, capsys):
    a = cli.main(["--as-of", "2024-06-30", "--paths", "400", "--budget", "auto"])
    assert a.recommendation["mode"] == "budget"
    text = capsys.readouterr().out
    assert "Stress scenarios" in text and "Provenance: config" in text
    rec = json.loads((out / "recommendation_2024-06-30.json").read_text())
    assert rec["_provenance"]["data_to"] == "2024-06-30" or rec["_provenance"]["data_to"] >= "2024-06-30"
    assert (out / "strategies_2024-06-30.csv").read_text().startswith("# hedgewise_version")
    for png in ("analysis_2024-06-30.png", "fit_2024-06-30.png", "stress_2024-06-30.png"):
        assert (out / png).exists()


def test_cli_demo_and_yearly_company(out):
    a = cli.main(
        ["--demo", "--paths", "300", "--yearly-gas", "300000", "--yearly-power", "1500", "--sector", "offices"]
    )
    assert sum(a.company.gas_m3) == pytest.approx(300_000)
    assert (out / "analysis_demo_2021-06-30.png").exists()


def test_package_main_runs_cli(out):
    a = hw.main(["--as-of", "2023-06-30", "--paths", "300", "--allow-timing"])
    assert a.recommendation["execution"] in ("now", "tranches")


@pytest.fixture(scope="module")
def analysis():
    df = hw.load_prices()
    a = hw.analyse(hw.fit(df, "2024-06-30"), hw.Company(), None, n_paths=500, seed=3)
    return a, hw.stress_test(a, df)


@pytest.mark.parametrize("lang", ["en", "nl"])
def test_report_is_one_page_with_fonts_and_provenance(analysis, lang):
    a, stress = analysis
    buf = io.BytesIO()
    report.make_report(a, buf, lang, stress)
    pdf = buf.getvalue()
    assert pdf.startswith(b"%PDF") and len(re.findall(rb"/Type /Page[^s]", pdf)) == 1
    fonts = set(re.findall(rb"/BaseFont /([A-Za-z0-9+\-]+)", pdf))
    assert fonts and all(b"DejaVuSans" in f for f in fonts) and b"Helvetica" not in pdf
    assert b"HedgeWise provenance" in pdf  # document metadata


def test_report_rejects_unknown_language(analysis):
    with pytest.raises(ValueError):
        report.make_report(analysis[0], io.BytesIO(), "de")


def test_report_cli(tmp_path):
    out = report.main(["--paths", "300", "--lang", "nl", "--as-of", "2024-06-30", "--out", str(tmp_path / "r.pdf")])
    assert out.exists() and out.read_bytes().startswith(b"%PDF")


def test_number_and_date_formatting():
    assert report.money(2_074_000, "en") == "€2.07m" and report.money(2_074_000, "nl") == "€ 2,07 mln"
    assert report.money(25_400, "en") == "€25k" and report.money(25_400, "nl") == "€ 25.000"
    assert report.pct(0.55) == "55%"
    assert report.date_str("2026-11-23", "nl") == "23 november 2026"
    assert report.date_str("2026-11-23", "en") == "23 Nov 2026"
    assert report._esc("a<b & c>") == "a&lt;b &amp; c&gt;"


def test_tradeoff_helpers(analysis):
    a, _ = analysis
    d_e, d_es = report.tradeoff_per_10pct(a)
    x, y = hw.frontier_scale(a)
    assert d_e == pytest.approx(x / 10) and d_es == pytest.approx(y / 10)


def test_report_path_names():
    p = naming.report_path("Bakkerij De Vries B.V.", "2026-11-23")
    assert p.name == "report_bakkerij_de_vries_b_v_2026-11-23.pdf"
    assert naming.report_path("???", "2026-11-23").name == "report_company_2026-11-23.pdf"
    assert report.report_path is naming.report_path


def test_company_input_cli(capsys):
    company_input.main([str(ROOT / "data" / "company_example.csv"), "--sector", "metal"])
    assert "1,200,000 m3 gas" in capsys.readouterr().out


def test_forwards_cli(capsys):
    forwards.main([str(ROOT / "data" / "forwards_example.csv")])
    assert "2027" in capsys.readouterr().out
