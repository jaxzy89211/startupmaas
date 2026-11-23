"""Monthly monitoring: the example client (clients/example.yaml) on the fixed price snapshot in data/."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import hedgewise as hw  # noqa: E402
from hedgewise import monitor as mo  # noqa: E402

EXAMPLE = ROOT / "clients" / "example.yaml"


@pytest.fixture(scope="module")
def df():
    return hw.load_prices()


@pytest.fixture(scope="module")
def client():
    return mo.load_client(EXAMPLE)


def test_plan_status_by_hand(client):
    plan = client["plan"]  # target 55% over Jul-Dec 2026 (6 months: 9.17% each); bought Jul, Aug, Sep
    oct_ = mo.plan_status(plan, pd.Timestamp("2026-10-23"))
    assert oct_["planned"] == pytest.approx(0.55 * 4 / 6) and oct_["bought"] == pytest.approx(0.2751)
    assert oct_["buy_now"] == pytest.approx(0.55 * 4 / 6 - 0.2751) and not oct_["is_overdue"]
    nov = mo.plan_status(plan, pd.Timestamp("2026-11-23"))
    assert nov["buy_now"] == pytest.approx(0.55 * 5 / 6 - 0.2751)
    assert nov["is_overdue"] and nov["overdue"] == pytest.approx(0.55 * 4 / 6 - 0.2751)  # October missed
    jun = mo.plan_status(plan, pd.Timestamp("2026-06-15"))
    assert jun["buy_now"] == 0 and jun["bought"] == 0 and not jun["is_overdue"]  # tranches after as-of don't count
    jan = mo.plan_status(plan, pd.Timestamp("2027-01-05"))
    assert jan["finished"]


def test_position_costs_by_hand(client, df):
    a = hw.analyse(hw.fit(df, "2026-11-23"), client["company"], "2027-01-01", 500, 1, 0.5)
    c = mo.position_costs(a, client["plan"], pd.Timestamp("2026-11-23"))
    vol = {"gas": a.company.gas_mwh, "power": a.company.power_mwh}
    var = {k: vol[k] @ a.spot_month[k] for k in vol}
    t = client["plan"]["tranches"]
    b = sum(x["share"] for x in t)
    now = sum(sum(x["share"] * x[f"{k}_eur_mwh"] for x in t) * vol[k].sum() + (1 - b) * var[k] for k in vol)
    assert np.allclose(c["now"], now) and np.allclose(c["variable"], var["gas"] + var["power"])
    rest = 0.55 - b  # the rest of the target at today's fixed price
    plan = now + sum(rest * a.fwd_now[k] * vol[k].sum() - rest * var[k] for k in vol)
    assert np.allclose(c["plan"], plan)


def test_two_monthly_runs_flags_history_pdf_and_email(client, df, tmp_path):
    h, out = tmp_path / "history", tmp_path / "monitor"
    first = mo.run_client(client, df, "2026-10-23", 500, h, out)
    assert first["prev"] is None and first["flags"] == [] and "first update" not in first["files"][1].read_text()
    second = mo.run_client(client, df, "2026-11-23", 500, h, out)
    assert second["prev"]["run_date"] == "2026-10-23"
    keys = {f["key"] for f in second["flags"]}
    assert "overdue" in keys  # October's tranche was not bought
    hist = pd.read_csv(h / "bakkerij_voorbeeld_fictief.csv", comment="#")
    assert list(hist.run_date) == ["2026-10-23", "2026-11-23"]
    mo.run_client(client, df, "2026-11-23", 500, h, out)  # a rerun on the same day replaces that row
    assert len(pd.read_csv(h / "bakkerij_voorbeeld_fictief.csv", comment="#")) == 2
    mail = (out / "bakkerij_voorbeeld_fictief_2026-11-23_email.txt").read_text()
    assert mail.startswith("[CONCEPT") and "Koop deze maand 18% van het gas" in mail and "te laat" in mail
    assert (out / "bakkerij_voorbeeld_fictief_2026-11-23_update.pdf").stat().st_size > 20_000  # 1 page (checked)


def test_flag_thresholds():
    st = {"is_overdue": False}
    row = {"bad_case_now": 1.16e6, "gas_fixed": 100.0, "power_fixed": 121.0}
    prev = {"bad_case_now": 1.0e6, "gas_fixed": 100.0, "power_fixed": 100.0}
    keys = [(f["key"], f.get("commodity")) for f in mo.flags(row, prev, st)]
    assert keys == [("bad_case", None), ("price", "power")]  # +16% > 15%, +21% > 20%; gas unchanged
    assert mo.flags({**row, "bad_case_now": 1.14e6, "power_fixed": 119.0}, prev, st) == []


def test_english_update_and_cli(client, df, tmp_path, monkeypatch):
    en = {**client, "language": "en", "name": "Example Foods"}
    r = mo.run_client(en, df, "2026-11-23", 500, tmp_path / "h", tmp_path / "m")
    assert mo.todo_text(r, "en").startswith("Buy **18%** of 2027 gas and electricity")
    assert r["files"][1].read_text().startswith("[DRAFT")
    monkeypatch.setattr(mo, "HISTORY", tmp_path / "h2")
    monkeypatch.setattr(mo, "UPDATES", tmp_path / "m2")
    ov = mo.main(["--no-download", "--clients", str(ROOT / "clients"), "--as-of", "2026-11-23"])
    assert list(ov.client) == ["Bakkerij Voorbeeld (fictief)"] and ov.buy_now.iloc[0] == pytest.approx(
        0.55 * 5 / 6 - 0.2751
    )
    assert (tmp_path / "m2" / "overview_2026-11-23.csv").exists() and "overdue" in ov.attention.iloc[0]


def test_bad_client_file(tmp_path):
    bad = tmp_path / "x.yaml"
    bad.write_text("name: X\nlanguage: en\nconsumption: {yearly: {gas: 1000}}\nplan: {delivery_year: 2027}\n")
    with pytest.raises(ValueError, match="target_share"):
        mo.load_client(bad)


def test_offline_download_is_skipped(monkeypatch):
    def boom(*a, **k):
        raise OSError("no network")

    monkeypatch.setattr(mo.subprocess, "run", boom)
    assert mo.refresh_prices().startswith("download skipped")
