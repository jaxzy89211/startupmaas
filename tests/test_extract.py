"""Bill reading: five fake documents (tests/fake_invoices.py) with the rules engine, and the AI engine with a mocked
Anthropic client. No real company documents are used anywhere."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fake_invoices  # noqa: E402

from hedgewise import extract as xt  # noqa: E402
from hedgewise.settings import MWH_PER_M3  # noqa: E402


@pytest.fixture(scope="module")
def docs(tmp_path_factory):
    return fake_invoices.make_all(tmp_path_factory.mktemp("invoices"))


@pytest.fixture(scope="module")
def exs(docs):
    return {name: xt.extract(path, name) for name, path in docs.items()}


def test_monthly_invoice_dutch(exs):
    e = exs["1_vattenfall_monthly_nl.pdf"]
    assert e.engine == "rules" and e.supplier.value == "Vattenfall" and e.contract_type.value == "variable"
    assert e.period_start.value == pd.Timestamp("2025-01-01") and e.period_end.value == pd.Timestamp("2025-01-31")
    assert e.monthly == [
        {
            "month": "2025-01",
            "gas_m3": 142350.0,  # "142.350 m³": Dutch thousands separator
            "power_kwh": 412800.0,
            "page": 1,
            "quote": "Gasverbruik 142.350 m³ | Elektriciteitsverbruik 412.800 kWh",
            "confidence": 0.8,
        }
    ]


def test_annual_statement_english_two_pages(exs):
    e = exs["2_eneco_annual_en.pdf"]
    assert e.contract_type.value == "fixed" and e.fixed_on.value == pd.Timestamp("2023-10-15")
    assert e.contract_start.value == pd.Timestamp("2024-01-01") and e.contract_end.value == pd.Timestamp("2025-12-31")
    m = pd.DataFrame(e.monthly)
    assert len(m) == 12 and (m.page == 2).all()
    assert m.gas_m3.sum() == 1_200_000 and m.power_kwh.iloc[0] == 410_000  # "138,000": English thousands
    p = {x["commodity"]: x for x in e.prices}
    assert p["power"]["eur_mwh"] == pytest.approx(98.5)  # 9.85 ct/kWh
    assert p["gas"]["eur_mwh"] == pytest.approx(0.52 / MWH_PER_M3)  # EUR 0.52 per m3


def test_contract_dutch_prices_in_eur_per_kwh_and_m3(exs):
    e = exs["3_essent_contract_nl.pdf"]
    assert e.contract_start.value == pd.Timestamp("2026-01-01") and e.contract_end.value == pd.Timestamp("2026-12-31")
    assert e.fixed_on.value == pd.Timestamp("2025-09-12") and e.fixed_on.quote == "Prijs vastgelegd op 12-09-2025"
    p = {x["commodity"]: x for x in e.prices}
    assert p["power"]["eur_mwh"] == pytest.approx(89.2) and p["power"]["unit"] == "EUR/kWh"
    assert p["gas"]["eur_mwh"] == pytest.approx(0.412 / MWH_PER_M3) and not e.monthly
    c = xt.contracts_table([e])
    assert list(c.year) == [2026, 2026] and set(c.commodity) == {"gas", "power"} and (c["share_%"] == 100).all()


def test_dutch_table_with_mwh_and_flags(exs):
    e = exs["4_greenchoice_overview_nl.pdf"]
    m = pd.DataFrame(e.monthly)
    assert len(m) == 11 and "2025-08" not in set(m.month)
    assert m.set_index("month").loc["2025-01", "gas_m3"] == 11850.5  # "11.850,5"
    assert m.set_index("month").loc["2025-02", "power_kwh"] == 31200.0  # "31,2" MWh
    flags = " ".join(xt.validate([e]))
    assert "2025-11 gas: 29,999 is more than 3x the median" in flags and "Missing months: 2025-08" in flags


def test_scanned_document_gives_empty_fields_and_a_warning(exs):
    e = exs["5_engie_scanned.pdf"]
    assert e.monthly == [] and e.prices == [] and e.supplier.value is None
    assert "No text layer" in e.warnings[0]


def test_validation_across_documents(exs):
    flags = xt.validate(list(exs.values()))
    assert any("2025-01 appears in more than one document" in f for f in flags)
    bad = xt.Extraction(
        "x.pdf",
        "rules",
        prices=[
            {
                "commodity": "power",
                "value": 8.9,
                "unit": "EUR/kWh",
                "eur_mwh": 8900.0,
                "page": 1,
                "quote": "",
                "confidence": 1,
            }
        ],
    )
    a = xt.Extraction(
        "a.pdf",
        "rules",
        period_start=xt.Field(pd.Timestamp("2025-01-01")),
        period_end=xt.Field(pd.Timestamp("2025-03-31")),
    )
    b = xt.Extraction(
        "b.pdf",
        "rules",
        period_start=xt.Field(pd.Timestamp("2025-03-01")),
        period_end=xt.Field(pd.Timestamp("2025-04-30")),
    )
    f2 = " ".join(xt.validate([bad, a, b]))
    assert "outside 1-1000 EUR/MWh" in f2 and "Periods overlap: a.pdf" in f2


def test_confirmed_table_feeds_company_input(exs):
    m = xt.monthly_table([exs["2_eneco_annual_en.pdf"]])
    res = xt.confirmed_company(m, "Test Foods", "food processing")
    assert sum(res.company.gas_m3) == 1_200_000 and sum(res.company.power_kwh) == pytest.approx(5_000_000)


# ------------------------------------------------------------------ the AI engine, with a mocked client
AI_JSON = {
    "supplier": {"value": "Engie", "page": 1, "quote": "ENGIE - Factuur", "confidence": 0.95},
    "period_start": {
        "value": "2025-02-01",
        "page": 1,
        "quote": "Periode: 01-02-2025 t/m 28-02-2025",
        "confidence": 0.9,
    },
    "period_end": {"value": "2025-02-28", "page": 1, "quote": "Periode: 01-02-2025 t/m 28-02-2025", "confidence": 0.9},
    "contract_type": {"value": None, "page": None, "quote": "", "confidence": 0},
    "contract_start": {"value": None, "page": None, "quote": "", "confidence": 0},
    "contract_end": {"value": None, "page": None, "quote": "", "confidence": 0},
    "fixed_on": {"value": None, "page": None, "quote": "", "confidence": 0},
    "prices": [
        {"commodity": "power", "value": 9.1, "unit": "ct/kWh", "page": 1, "quote": "9,10 ct/kWh", "confidence": 0.7}
    ],
    "monthly": [
        {
            "month": "2025-02",
            "gas_value": None,
            "gas_unit": None,
            "power_value": 39.1,
            "power_unit": "MWh",
            "page": 1,
            "quote": "Elektriciteitsverbruik: 39x.1xx kWh",
            "confidence": 0.3,
        }
    ],
}


class FakeClient:
    """Stands in for anthropic.Anthropic(): records the request, returns a canned response."""

    def __init__(self, payload=None, stop="end_turn", error=None):
        self.calls, self.payload, self.stop, self.error = [], payload or AI_JSON, stop, error
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        if self.error:
            raise self.error
        return SimpleNamespace(
            stop_reason=self.stop, content=[SimpleNamespace(type="text", text=json.dumps(self.payload))]
        )


def test_ai_engine_request_and_parsing(docs):
    fc = FakeClient()
    e = xt.extract(docs["5_engie_scanned.pdf"], "5_engie_scanned.pdf", consent=True, client=fc)
    assert e.engine == "ai" and e.supplier.value == "Engie" and e.period_end.value == pd.Timestamp("2025-02-28")
    assert e.monthly[0]["power_kwh"] == 39_100.0 and e.monthly[0]["gas_m3"] is None  # MWh -> kWh
    assert e.prices[0]["eur_mwh"] == pytest.approx(91.0)
    req = fc.calls[0]
    assert req["model"] == "claude-opus-5-5" and req["output_config"]["format"]["type"] == "json_schema"
    assert req["output_config"]["format"]["schema"] is xt.SCHEMA and req["fallbacks"] == "default"
    doc = req["messages"][0]["content"][0]
    assert doc["type"] == "document" and doc["source"]["media_type"] == "application/pdf"


def test_no_consent_or_no_key_means_no_ai(docs, monkeypatch):
    fc = FakeClient()
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    e = xt.extract(docs["1_vattenfall_monthly_nl.pdf"], "1.pdf", consent=False, client=fc)
    assert e.engine == "rules" and fc.calls == []  # never sent without consent
    e2 = xt.extract(docs["1_vattenfall_monthly_nl.pdf"], "1.pdf", consent=True)  # consent but no key
    assert e2.engine == "rules" and "No ANTHROPIC_API_KEY" in e2.warnings[0]


@pytest.mark.parametrize("client", [FakeClient(stop="refusal"), FakeClient(error=RuntimeError("timeout"))])
def test_ai_failure_falls_back_to_rules(docs, client):
    e = xt.extract(docs["1_vattenfall_monthly_nl.pdf"], "1.pdf", consent=True, client=client)
    assert e.engine == "rules" and "AI service not used" in e.warnings[0] and e.monthly[0]["gas_m3"] == 142350.0


def test_schema_is_strict():
    def check(s):
        if s.get("type") == "object":
            assert s["additionalProperties"] is False and set(s["required"]) == set(s["properties"])
            for v in s["properties"].values():
                check(v)
        if s.get("type") == "array":
            check(s["items"])

    check(xt.SCHEMA)


def test_cli(docs, capsys):
    xt.main([str(docs["4_greenchoice_overview_nl.pdf"])])
    out = capsys.readouterr().out
    assert "Greenchoice" in out and "FLAG: Missing months: 2025-08." in out
