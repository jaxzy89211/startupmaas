"""Data loading (incl. investing.com and CO2 files), config validation, provenance writers, model and strategy
branches that the end-to-end tests don't reach."""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import hedgewise as hw  # noqa: E402
from hedgewise import config, data, provenance, strategies  # noqa: E402


# ------------------------------------------------------------------ data
@pytest.fixture(scope="module")
def investing_dir(tmp_path_factory):
    """A data folder with gas and CO2 in investing.com export format (US dates, 'Price' column) + real power."""
    d = tmp_path_factory.mktemp("data")
    real = hw.load_prices()
    gas = real.loc["2019-01-01":"2021-12-31"]
    gas = gas[gas.gas_trade]["gas"]
    pd.DataFrame(
        {"Date": gas.index.strftime("%m/%d/%Y"), "Price": [f"{v:,.2f}" for v in gas.values], "Change %": "0.1%"}
    ).iloc[::-1].to_csv(d / "gas_ttf.csv", index=False)
    rng = np.random.default_rng(0)
    co2 = 25 * np.exp(np.cumsum(rng.normal(0, 0.02, len(gas))))
    pd.DataFrame({"Date": gas.index.strftime("%m/%d/%Y"), "Price": co2.round(2)}).to_csv(d / "co2_eua.csv", index=False)
    (d / "power_nl_daily.csv").write_bytes((ROOT / "data" / "power_nl_daily.csv").read_bytes())
    return d


def test_load_investing_format_with_co2(investing_dir):
    df = data.load_prices(investing_dir)
    assert {"gas", "power", "co2", "co2_trade"} <= set(df.columns)
    assert df.index.max() <= pd.Timestamp("2022-01-03")
    prov = provenance.current()
    assert set(prov["data_files"]) == {"gas_ttf.csv", "power_nl_daily.csv", "co2_eua.csv"}
    m = hw.fit(df, "2021-06-30")
    assert m.drivers == ["gas", "co2"] and m.corr.shape == (3, 3)
    assert "co2" in hw.describe(m)
    sims = hw.simulate(m, "2022-12-31", 200, 1)
    assert sims["co2"].shape == sims["gas"].shape
    a = hw.analyse(m, hw.Company(), None, 200, 1, sims=sims)
    assert np.isfinite(a.table.expected).all()
    hw.load_prices()  # restore the real data as "current" provenance for later tests


def test_demo_data_registers_demo_provenance():
    df = hw.demo_prices()
    assert df.index.min() == pd.Timestamp("2019-01-01") and (df.power > 0).all()
    assert provenance.current()["data_hash"] == "demo-data"
    hw.load_prices()


def test_fit_needs_a_year_of_data():
    with pytest.raises(ValueError, match="need at least"):
        hw.fit(hw.load_prices().iloc[:100])


# ------------------------------------------------------------------ config
def _write_cfg(tmp_path, mutate):
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    mutate(cfg)
    p = tmp_path / f"cfg_{len(list(tmp_path.iterdir()))}.yaml"
    p.write_text(yaml.safe_dump(cfg))
    return str(p)


@pytest.mark.parametrize(
    "mutate, msg",
    [
        (lambda c: c["fixed_price"].pop("premium"), "missing 'fixed_price.premium'"),
        (lambda c: c["fixed_price"].update(premium="5%"), "must be"),
        (lambda c: c["simulation"].update(n_paths=True), "got a bool"),
        (lambda c: c["solar"].update(monthly_share=[1, 2, 3]), "needs 12 values"),
        (lambda c: c["company_default"].update(gas_monthly_share_pct=[1]), "needs 12 values"),
        (lambda c: c["sector_profiles"]["metal"].update(gas=[1, 2]), "needs 12 values"),
    ],
)
def test_config_validation_errors(tmp_path, mutate, msg):
    with pytest.raises(ValueError, match=msg):
        config._load(_write_cfg(tmp_path, mutate))


def test_config_loads_and_hashes(monkeypatch, tmp_path):
    cfg = config.load_config()
    assert cfg["fixed_price"]["premium"] == 0.05
    h = config.config_hash()
    assert len(h) == 12
    other = tmp_path / "c.yaml"
    other.write_text((ROOT / "config.yaml").read_text() + "\n# changed\n")
    monkeypatch.setenv(config.ENV_VAR, str(other))
    assert config.config_path() == other and config.config_hash() != h


# ------------------------------------------------------------------ provenance
def test_provenance_writers(tmp_path):
    hw.load_prices()
    p = provenance.current()
    assert p["data_from"] == "2019-01-02" and len(p["data_hash"]) == 12
    assert provenance.one_line().startswith(f"config {p['config_hash']} · data {p['data_hash']}")
    provenance.write_csv(pd.DataFrame({"x": [1.5, 2.5]}), tmp_path / "t.csv")
    assert pd.read_csv(tmp_path / "t.csv", comment="#")["x"].tolist() == [1.5, 2.5]
    provenance.write_json({"a": 1}, tmp_path / "t.json")
    assert json.loads((tmp_path / "t.json").read_text())["_provenance"]["config_hash"] == p["config_hash"]
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from PIL import Image

    fig, _ = plt.subplots()
    provenance.save_figure(fig, tmp_path / "t.png")
    provenance.save_figure(fig, tmp_path / "t.pdf")  # non-PNG: written without PNG text chunks
    plt.close(fig)
    assert p["data_hash"] in Image.open(tmp_path / "t.png").text["Description"]
    assert (tmp_path / "t.pdf").read_bytes().startswith(b"%PDF")


def test_provenance_before_any_data(monkeypatch):
    monkeypatch.setattr(provenance, "_current", {})
    p = provenance.current()
    assert "data_hash" not in p and provenance.one_line().startswith("config ")


# ------------------------------------------------------------------ company
def test_company_from_csv_and_solar(tmp_path):
    c = hw.Company.from_csv(ROOT / "data" / "company_example.csv")
    assert len(c.gas_m3) == 12 and sum(c.power_kwh) == pytest.approx(5_000_000)
    c.solar_kwp = 1000
    assert c.power_mwh.sum() == pytest.approx(5000 - 900)
    bad = tmp_path / "bad.csv"
    bad.write_text("month,gas_m3,power_kwh\n1,1,1\n2,1,1\n")
    with pytest.raises(ValueError, match="12 calendar months"):
        hw.Company.from_csv(bad)


# ------------------------------------------------------------------ strategies
@pytest.fixture(scope="module")
def model_and_sims():
    df = hw.load_prices()
    m = hw.fit(df, "2024-06-30")
    return m, hw.simulate(m, "2025-12-31", 400, 2)


@pytest.mark.parametrize("budget", ["auto", 1e9, 1.0])
def test_budget_modes(model_and_sims, budget):
    m, s = model_and_sims
    a = hw.analyse(m, hw.Company(), None, 400, 2, sims=s, budget=budget)
    r = a.recommendation
    assert r["mode"] == "budget" and r["budget"] > 0
    if budget == 1e9:  # easily met: the smallest hedge (0%) is enough
        assert r["hedge_gas"] == 0
    if budget == 1.0:  # impossible: falls back to the lowest bad case
        assert r["hedge_gas"] == pytest.approx(a.frontier.loc[a.frontier.es95.idxmin(), "hedge_gas"])
    assert "Budget EUR" in hw.recommendation_text(a)


def test_allow_timing_and_mean_es_objective(model_and_sims, monkeypatch):
    m, s = model_and_sims
    a = hw.analyse(m, hw.Company(), None, 400, 2, sims=s, allow_timing=True)
    assert set(a.frontier.execution) == {"now", "tranches"}
    monkeypatch.setattr(strategies, "OBJECTIVE", "mean_es")
    b = hw.analyse(m, hw.Company(), None, 400, 2, sims=s, risk_aversion=0.9)
    assert b.recommendation["hedge_gas"] in (0.0, 1.0)  # a mean-ES objective is linear: corner solutions only


def test_mid_year_delivery_and_text(model_and_sims):
    m, _ = model_and_sims
    a = hw.analyse(m, hw.Company(), "2025-04-01", 300, 2)
    assert a.delivery_start == pd.Timestamp("2025-04-01")
    assert "Delivery Apr 2025 - Mar 2026" in hw.recommendation_text(a)


def test_trailing_bill_and_tranches():
    df = hw.load_prices()
    assert hw.trailing_bill(df.loc[:"2022-12-31"], hw.Company()) > hw.trailing_bill(df.loc[:"2020-12-31"], hw.Company())
    t = hw.tranche_dates("2026-06-30", "2027-01-01")
    assert t[0] == pd.Timestamp("2026-06-30") and len(t) == 6
    assert hw.tranche_dates("2026-12-20", "2027-01-01") == [pd.Timestamp("2026-12-20")]
