"""Market size: every number recomputed by hand from the CBS snapshot in data/cbs/ (offline)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hedgewise import market as mk  # noqa: E402

SBI = "BedrijfstakkenBranchesSBI2008"


@pytest.fixture(scope="module")
def inp():
    return mk.load_inputs(offline=True)


def test_paper_by_hand(inp):
    f = inp["firms"].set_index(SBI).loc["317100"]
    n = {c: float(f[col]) for c, col in mk.FIRM_COLS.items()}
    n_100_250 = float(f["k_0Tot250WerkzamePersonen_11"]) - sum(n.values())  # 100-250 = (0-250) - classes below 100
    e = inp["energy"]
    e = e[e.Sectoren == "317100"].set_index("Energiedragers")
    gwh = (
        float(e.loc["E006560", "FinaalEnergieverbruik_19"])
        + float(e.loc["E006560", "InzetElektriciteitWKKOmzetting_12"])
        + float(e.loc["E006607", "FinaalEnergieverbruik_19"])
    ) * 277.7778  # PJ -> GWh
    j = inp["jobs"]
    jobs = float(j[(j[SBI] == "317100") & (j.Bedrijfsgrootte == "T001098")].BanenVanWerknemersInDecember_1.iloc[0])
    per_person = gwh / (jobs * 1000)
    est = inp["est"].set_index(["RegioS", SBI]).Vestigingen_1.astype(float)
    share = est[("PV31", "317100")] / est[("NL01", "317100")]
    out = mk.estimate(inp).set_index(["sector", "size_class"]).loc["Paper (SBI 17)"]
    assert out.loc["100-250", "companies_nl"] == n_100_250
    assert out.loc["20-50", "companies_nl"] == n["20-50"]
    assert out.loc["50-100", "avg_gwh_per_company"] == pytest.approx(70 * per_person)
    assert out.loc["10-20", "companies_limburg"] == pytest.approx(n["10-20"] * share)


def test_summary_and_revenue_formula(inp):
    est = mk.estimate(inp)
    s = mk.summary(est).loc["Netherlands"]
    t = est[est.target]
    assert (t.avg_gwh_per_company >= 1.0).all() and (est[~est.target].avg_gwh_per_company < 1.0).all()
    n, gwh = t.companies_nl.sum(), (t.companies_nl * t.avg_gwh_per_company).sum()
    assert s.target_companies == pytest.approx(n)
    assert s["revenue_5%_adoption_eur"] == pytest.approx(0.05 * (n * 3000 + gwh * 150))
    half = mk.summary(mk.estimate(inp, intensity=0.5)).loc["Netherlands"]
    assert half.energy_use_gwh < s.energy_use_gwh and half.target_companies <= s.target_companies


def test_food_limburg_uses_the_sum_of_sbi_10_11_12(inp):
    est = mk.estimate(inp)
    food = est[est.sector.str.startswith("Food")]
    assert food.limburg_share.notna().all() and 0.02 < food.limburg_share.iloc[0] < 0.15


def test_offline_without_snapshot_explains_what_to_download(tmp_path, monkeypatch):
    monkeypatch.setattr(mk, "CBS", tmp_path)
    monkeypatch.setattr(mk, "_odata", lambda *a: (_ for _ in ()).throw(OSError("no network")))
    with pytest.raises(RuntimeError, match="Download table 81589NED"):
        mk.fetch("81589NED", "x")


def test_cli_offline(tmp_path, monkeypatch):
    monkeypatch.setattr(mk, "OUT", tmp_path)
    mk.main(["--offline"])
    assert (tmp_path / "market.png").stat().st_size > 20_000 and (tmp_path / "market_summary.csv").exists()
