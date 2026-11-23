"""Read energy invoices, annual statements and supplier contracts (PDF, Dutch or English) into numbers.

The AI only READS documents. It never decides hedge ratios or computes risk: the tested model does that. A person
must confirm every extracted number (the app shows an editable table with the source quotes) before it is used.

Two engines:
    (a) "rules": pdfplumber text + regular expressions for common layouts. Always available, nothing leaves the
        machine. Scanned PDFs (no text layer) give empty fields and a warning.
    (b) "ai": the Anthropic API (model in config.yaml `extract.ai_model`) with a strict JSON schema. Used ONLY if the
        environment variable ANTHROPIC_API_KEY is set AND the caller passes consent=True (in the app: the box
        "send this document to the AI service"), because bills are confidential. Otherwise engine (a) is used.

Every extracted field carries the page it came from, the quoted text and a confidence (0-1). Units are converted
with the existing helpers (company_input for volumes, audit.to_eur_per_mwh for prices) and validate() flags anything
implausible: a month more than 3x the median, prices outside 1-1000 EUR/MWh, missing months, overlapping periods.

    python extract.py bill1.pdf bill2.pdf [--ai]      (--ai = consent to send the files to the AI service)
"""

from __future__ import annotations

import argparse
import base64
import dataclasses
import io
import json
import os
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from . import audit as au
from . import company_input as ci
from .config import load_config

_X = load_config()["extract"]
COMMODITIES = ("gas", "power")


# --------------------------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------------------------
@dataclasses.dataclass
class Field:
    """One extracted value with its source: page (1-based), the quoted text, and a confidence 0-1."""

    value: Any = None
    page: int | None = None
    quote: str = ""
    confidence: float = 0.0


@dataclasses.dataclass
class Extraction:
    """Everything read from one document. Volumes are in m3 (gas) and kWh (power); prices also in EUR/MWh."""

    source: str
    engine: str
    supplier: Field = dataclasses.field(default_factory=Field)
    period_start: Field = dataclasses.field(default_factory=Field)
    period_end: Field = dataclasses.field(default_factory=Field)
    contract_type: Field = dataclasses.field(default_factory=Field)  # "fixed" / "variable"
    contract_start: Field = dataclasses.field(default_factory=Field)
    contract_end: Field = dataclasses.field(default_factory=Field)
    fixed_on: Field = dataclasses.field(default_factory=Field)
    prices: list[dict] = dataclasses.field(default_factory=list)  # commodity, value, unit, eur_mwh, page, quote, conf.
    monthly: list[dict] = dataclasses.field(default_factory=list)  # month, gas_m3, power_kwh, page, quote, conf.
    totals: dict = dataclasses.field(default_factory=dict)  # period totals (gas_m3, power_kwh, quote, ...) if no months
    warnings: list[str] = dataclasses.field(default_factory=list)


# --------------------------------------------------------------------------------------------
# Small parsers (Dutch and English)
# --------------------------------------------------------------------------------------------
_MONTHS = {
    **{
        m: i + 1
        for i, m in enumerate(
            [
                "januari",
                "februari",
                "maart",
                "april",
                "mei",
                "juni",
                "juli",
                "augustus",
                "september",
                "oktober",
                "november",
                "december",
            ]
        )
    },
    **{
        m: i + 1
        for i, m in enumerate(
            [
                "january",
                "february",
                "march",
                "april",
                "may",
                "june",
                "july",
                "august",
                "september",
                "october",
                "november",
                "december",
            ]
        )
    },
}
_DATE = (
    r"(\d{1,2}[-/.]\d{1,2}[-/.]\d{4}|\d{4}-\d{2}-\d{2}|\d{1,2}\s+(?:"
    + "|".join(sorted(_MONTHS, key=len, reverse=True))
    + r")\s+\d{4})"
)
_NUM = r"(\d{1,3}(?:[.,]\d{3})+(?!\d)(?:[.,]\d+)?|\d+(?:[.,]\d+)?)"  # '1.234,5' | '0,10230' (not '0,102')
SUPPLIERS = [
    "Vattenfall",
    "Eneco",
    "Essent",
    "Engie",
    "Greenchoice",
    "Budget Energie",
    "TotalEnergies",
    "Energiedirect",
    "Vandebron",
    "Pure Energie",
    "Innova",
    "Oxxio",
    "Frank Energie",
    "Coolblue Energie",
    "Delta Energie",
    "Gazprom",
    "Uniper",
    "Axpo",
    "Shell Energy",
    "Scholt Energy",
    "Next Energy",
    "Powerpeers",
    "Mega",
]


def parse_date(text: str) -> pd.Timestamp | None:
    """01-02-2025 (day first), 2025-02-01, '1 februari 2025', '1 February 2025'."""
    t = text.strip().lower()
    m = re.fullmatch(r"(\d{1,2})\s+([a-z]+)\s+(\d{4})", t)
    if m and m[2] in _MONTHS:
        return pd.Timestamp(int(m[3]), _MONTHS[m[2]], int(m[1]))
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", t):
        return pd.Timestamp(t)
    try:
        return pd.Timestamp(pd.to_datetime(t.replace("/", "-").replace(".", "-"), dayfirst=True))
    except (ValueError, TypeError):
        return None


class _Numbers:
    """Reads numbers consistently within one document: Dutch (1.234,5) or English (1,234.5) is decided once,
    from all numbers in the document (company_input.parse_numbers)."""

    def __init__(self, text: str) -> None:
        tokens = re.findall(_NUM, text)
        self.map = dict(zip(tokens, ci.parse_numbers(tokens), strict=True)) if tokens else {}

    def __call__(self, token: str) -> float:
        v = self.map.get(token)
        return float(v) if v is not None else float(ci.parse_numbers([token])[0])


# --------------------------------------------------------------------------------------------
# Engine (a): rules
# --------------------------------------------------------------------------------------------
def _pages_text(src: str | Path | bytes) -> list[str]:
    import pdfplumber

    f = io.BytesIO(src) if isinstance(src, bytes) else src
    with pdfplumber.open(f) as pdf:
        return [p.extract_text() or "" for p in pdf.pages]


def _first(pages: list[str], pattern: str, conf: float, group: int = 1, flags: int = re.I) -> Field:
    for i, text in enumerate(pages, start=1):
        for line in text.splitlines():
            m = re.search(pattern, line, flags)
            if m:
                return Field(m[group], i, line.strip(), conf)
    return Field()


def _commodity(line: str) -> str | None:
    s = line.lower()
    if re.search(r"elektriciteit|stroom|electricity|power", s):
        return "power"
    if re.search(r"\bgas", s):
        return "gas"
    return None


def extract_rules(src: str | Path | bytes, name: str = "") -> Extraction:
    """Engine (a): text + rules. Fields it can't find stay empty."""
    pages = _pages_text(src)
    ex = Extraction(source=name or Path(str(src)).name if not isinstance(src, bytes) else name, engine="rules")
    if not any(p.strip() for p in pages):
        ex.warnings.append(
            "No text layer (a scanned document?): nothing could be read. Type the numbers in, or "
            "use the AI service if the company agrees."
        )
        return ex
    num = _Numbers("\n".join(pages))
    # supplier: a known name (first page first), else "Leverancier:/Supplier:"
    for i, text in enumerate(pages, start=1):
        hit = next((s for s in SUPPLIERS if re.search(rf"\b{re.escape(s)}\b", text, re.I)), None)
        if hit:
            line = next(ln for ln in text.splitlines() if re.search(rf"\b{re.escape(hit)}\b", ln, re.I))
            ex.supplier = Field(hit, i, line.strip(), 0.9)
            break
    if ex.supplier.value is None:
        ex.supplier = _first(pages, r"(?:leverancier|supplier)\s*:\s*(.+)", 0.6)
    # periods and dates
    per = _first(
        pages,
        rf"(?:factuurperiode|periode|billing period|period|looptijd)\s*:?\s*{_DATE}\s*"
        rf"(?:t/m|tot en met|-|–|to|until)\s*{_DATE}",
        0.9,
        group=0,
    )
    if per.value:
        a, b = re.findall(_DATE, per.value, re.I)[:2]
        is_term = "looptijd" in per.quote.lower()
        target = ("contract_start", "contract_end") if is_term else ("period_start", "period_end")
        for attr, d in zip(target, (a, b), strict=True):
            setattr(ex, attr, Field(parse_date(d), per.page, per.quote, 0.9))
    for attr, pat in (
        ("contract_start", r"(?:ingangsdatum|startdatum|contract start|start date)\s*:?\s*" + _DATE),
        (
            "contract_end",
            r"(?:einddatum|contract end|end date|einde contract|looptijd\s+(?:t/m|tot en met|tot)|"
            r"loopt\s+(?:t/m|tot en met|tot))\s*:?\s*" + _DATE,
        ),
        ("fixed_on", r"(?:prijs vastgezet op|prijs vastgelegd op|price fixed on|fixed on)\s*:?\s*" + _DATE),
    ):
        f = _first(pages, pat, 0.85)
        if f.value:
            setattr(ex, attr, Field(parse_date(f.value), f.page, f.quote, f.confidence))
    # contract type
    ct = _first(
        pages,
        r"\b(variabel\w*|variable|vast(?:e)?\s+(?:tarief|prijs|contract|\d+\s+jaar)|tarieven\s+zijn\s+vast|"
        r"contract(?:type|vorm)?\s*:\s*vast|zakelijk\s+vast|fixed(?:\s+price)?)\b",
        0.8,
    )
    if ct.value:
        ex.contract_type = Field(
            "variable" if ct.value.lower().startswith("variab") else "fixed", ct.page, ct.quote, ct.confidence
        )
    ex.prices = _price_lines(pages, num)
    ex.monthly = _monthly_rows(pages, num)
    if not ex.monthly:
        ex.totals = _consumption_totals(pages, num)
        ps, pe = ex.period_start.value, ex.period_end.value
        if ex.totals and ps is not None and pe is not None and (ps.year, ps.month) == (pe.year, pe.month):
            ex.monthly = [
                {
                    "month": f"{ps.year}-{ps.month:02d}",
                    "gas_m3": ex.totals.get("gas_m3"),
                    "power_kwh": ex.totals.get("power_kwh"),
                    "page": ex.totals["page"],
                    "quote": ex.totals["quote"],
                    "confidence": 0.8,
                }
            ]
            ex.totals = {}
    if any("terugle" in ln.lower() for t in pages for ln in t.splitlines()):
        ex.warnings.append("Feed-in (teruglevering) found: not counted as consumption. Check solar with the company.")
    if not ex.monthly and not ex.totals and not ex.prices:
        ex.warnings.append("No consumption or prices found with the standard layouts; check the document by hand.")
    return ex


def _monthly_rows(pages: list[str], num: _Numbers) -> list[dict]:
    """Monthly table rows ('januari 2025  11.850,5  34,4' under a header naming the columns and units; several power
    columns such as normal + off-peak add up). One-month invoices are handled by _consumption_totals."""
    rows = []
    for i, text in enumerate(pages, start=1):
        cols = None  # [(commodity, unit)] from the header line
        for line in text.splitlines():
            low = line.lower()
            if re.search(r"\b(maand|month)\b", low) and ("gas" in low or "stroom" in low or "electric" in low):
                cols = []
                for m in re.finditer(
                    r"(gas|stroom|elektriciteit|electricity|power)(?:\s+(?:normaal|dal|piek|laag|enkel|normal|off-peak|"
                    r"peak))?\s*\(([^)]+)\)",
                    low,
                ):
                    unit = m[2].replace("³", "3").strip()
                    unit = {"m3": "m3", "kwh": "kWh", "mwh": "MWh", "gj": "GJ"}.get(unit, unit)
                    cols.append(("gas" if m[1] == "gas" else "power", unit))
                continue
            if cols is None:
                continue
            first = re.match(r"\s*([a-z]+\.?\s*-?\s*'?\d{2,4}|\d{4}-\d{2}|\d{1,2}-\d{4})", low)
            if not first:
                continue
            mo, yr = ci.parse_month(first[1]), ci.parse_year(first[1])
            if mo is None or yr is None:
                continue
            vals = re.findall(_NUM, line[first.end() :])
            row = {
                "month": f"{yr}-{mo:02d}",
                "gas_m3": None,
                "power_kwh": None,
                "page": i,
                "quote": line.strip(),
                "confidence": 0.85,
            }
            for (c, unit), tok in zip(cols, vals, strict=False):
                v = num(tok)
                key, val = ("gas_m3", ci.gas_to_m3(v, unit)) if c == "gas" else ("power_kwh", ci.power_to_kwh(v, unit))
                row[key] = (row[key] or 0.0) + float(val)  # several columns (normal + off-peak) add up
            rows.append(row)
    return rows


_NOT_SUPPLY = re.compile(
    r"belasting|\bode\b|opslag|netbeheer|transport|vastrecht|vaste|btw|heffing|terugle|korting|capaciteit", re.I
)
_UNITS = {"kwh": "kWh", "mwh": "MWh", "m3": "m3", "m³": "m3", "gj": "GJ"}
_REGISTER = re.compile(r"\b(normaal|dal|piek|laag|enkel)\b", re.I)


def _price_lines(pages: list[str], num: _Numbers) -> list[dict]:
    """Supply prices: 'Leveringstarief gas: € 0,4410 per m3', '9,85 ct/kWh', '48,50 ct/m3', and quantity x price
    lines ('120.400 kWh x € 0,10230'). Energy tax, network costs, VAT, levies and feed-in are not supply prices."""
    pat_a = rf"(?:€|eur)?\s*{_NUM}\s*(?:(ct|cent)\s*/\s*(kwh|m3|m³)|(?:per|/)\s*(kwh|mwh|m3|m³|gj))"
    pat_b = rf"{_NUM}\s*(kwh|mwh|m3|m³|gj)\s*x\s*(?:€|eur)?\s*{_NUM}"
    out = []
    for i, text in enumerate(pages, start=1):
        for line in text.splitlines():
            c = _commodity(line)
            if not c or _NOT_SUPPLY.search(line):
                continue
            reg = _REGISTER.search(line)
            b = re.search(pat_b, line, re.I)
            a = re.search(pat_a, line, re.I)
            if b:
                unit, qty, v = _UNITS[b[2].lower()], num(b[1]), num(b[3])
                unit_txt = {"kWh": "EUR/kWh", "MWh": "EUR/MWh", "m3": "EUR/m3", "GJ": "EUR/GJ"}[unit]
            elif a and re.search(r"tarief|prijs|price|rate", line, re.I):
                qty = None
                v = num(a[1])
                unit_txt = (
                    f"ct/{_UNITS[a[3].lower()]}"
                    if a[2]
                    else {"kwh": "EUR/kWh", "mwh": "EUR/MWh", "m3": "EUR/m3", "m³": "EUR/m3", "gj": "EUR/GJ"}[
                        a[4].lower()
                    ]
                )
            else:
                continue
            try:
                eur = au.to_eur_per_mwh(v, unit_txt, c)
            except ValueError:
                continue
            out.append(
                {
                    "commodity": c,
                    "value": v,
                    "unit": unit_txt,
                    "eur_mwh": eur,
                    "qty": qty,
                    "register": reg[1].lower() if reg else None,
                    "page": i,
                    "quote": line.strip(),
                    "confidence": 0.85,
                }
            )
    return out


def combined_prices(ex: Extraction) -> dict[str, dict]:
    """One supply price per commodity (EUR/MWh): quantity-weighted over the registers when the bill shows the
    quantities (normal + off-peak lines), else the plain average; with the quotes it came from."""
    out = {}
    for c in COMMODITIES:
        ps = [p for p in ex.prices if p["commodity"] == c]
        if not ps:
            continue
        if all(p.get("qty") for p in ps):
            w = sum(p["qty"] for p in ps)
            v = sum(p["eur_mwh"] * p["qty"] for p in ps) / w
        else:
            v = float(np.mean([p["eur_mwh"] for p in ps]))
        out[c] = {
            "eur_mwh": v,
            "quote": " | ".join(p["quote"] for p in ps),
            "page": ps[0]["page"],
            "confidence": min(p["confidence"] for p in ps),
        }
    return out


def _consumption_totals(pages: list[str], num: _Numbers) -> dict:
    """Period consumption per commodity from lines like 'Elektriciteit normaal ... 120.400 kWh' (normal + off-peak
    registers add up; a 'totaal' line wins). Lines with euro amounts (costs, tariffs) and feed-in are skipped."""
    found: dict[str, list] = {"gas": [], "power": []}
    for i, text in enumerate(pages, start=1):
        for line in text.splitlines():
            c = _commodity(line)
            if not c or "€" in line or re.search(r"terugle|eur\b|tarief|prijs", line, re.I):
                continue
            hits = list(re.finditer(rf"{_NUM}\s*(kwh|mwh|m3|m³|gj)\b", line, re.I))
            if not hits:
                continue
            h = hits[-1]
            unit = _UNITS[h[2].lower()]
            v = num(h[1])
            val = float(ci.gas_to_m3(v, unit)) if c == "gas" else float(ci.power_to_kwh(v, unit))
            found[c].append((val, "totaal" in line.lower(), i, line.strip()))
    out: dict = {}
    quotes, page = [], None
    for c, key in (("gas", "gas_m3"), ("power", "power_kwh")):
        items = found[c]
        if not items:
            continue
        tot = [x for x in items if x[1]]
        use = tot[:1] if tot else items
        out[key] = sum(x[0] for x in use)
        quotes += [x[3] for x in use]
        page = page or use[0][2]
    if out:
        out.update(page=page, quote=" | ".join(quotes), confidence=0.8)
    return out


# --------------------------------------------------------------------------------------------
# Engine (b): the Anthropic API with a strict JSON schema
# --------------------------------------------------------------------------------------------
def _f(kind: str | list) -> dict:
    """A field object in the schema: value + page + quote + confidence."""
    t = kind if isinstance(kind, list) else [kind, "null"]
    return {
        "type": "object",
        "properties": {
            "value": {"type": t},
            "page": {"type": ["integer", "null"]},
            "quote": {"type": "string"},
            "confidence": {"type": "number"},
        },
        "required": ["value", "page", "quote", "confidence"],
        "additionalProperties": False,
    }


SCHEMA = {
    "type": "object",
    "properties": {
        "supplier": _f("string"),
        "period_start": _f("string"),
        "period_end": _f("string"),
        "contract_type": _f(["string", "null"]),
        "contract_start": _f("string"),
        "contract_end": _f("string"),
        "fixed_on": _f("string"),
        "prices": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "commodity": {"type": "string", "enum": ["gas", "power"]},
                    "value": {"type": "number"},
                    "unit": {"type": "string", "enum": ["EUR/MWh", "ct/kWh", "EUR/kWh", "EUR/m3", "EUR/GJ"]},
                    "page": {"type": "integer"},
                    "quote": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["commodity", "value", "unit", "page", "quote", "confidence"],
                "additionalProperties": False,
            },
        },
        "monthly": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "month": {"type": "string"},
                    "gas_value": {"type": ["number", "null"]},
                    "gas_unit": {"type": ["string", "null"], "enum": ["m3", "kWh", "MWh", "GJ", None]},
                    "power_value": {"type": ["number", "null"]},
                    "power_unit": {"type": ["string", "null"], "enum": ["kWh", "MWh", None]},
                    "page": {"type": "integer"},
                    "quote": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": [
                    "month",
                    "gas_value",
                    "gas_unit",
                    "power_value",
                    "power_unit",
                    "page",
                    "quote",
                    "confidence",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": [
        "supplier",
        "period_start",
        "period_end",
        "contract_type",
        "contract_start",
        "contract_end",
        "fixed_on",
        "prices",
        "monthly",
    ],
    "additionalProperties": False,
}

PROMPT = """You read one energy document of a Dutch company (an invoice, annual statement or supply contract; Dutch \
or English) and copy numbers from it. You only read: do not estimate, advise or calculate anything.

Fill every field from the document. For each value give the page (1-based), the exact text you read it from \
(quote), and your confidence (0-1). If something is not in the document, use null with confidence 0 - never guess.
- Dates as YYYY-MM-DD. contract_type: "fixed" or "variable".
- prices: the supply price (leveringstarief / energy price) per commodity, without tax, with the unit as written.
- monthly: one row per calendar month with consumption (month as YYYY-MM), value and unit exactly as written \
(Dutch numbers: "1.234,5" = 1234.5). For an invoice covering one month, give that month's total."""


def extract_ai(src: str | Path | bytes, name: str = "", client: Any = None) -> Extraction:
    """Engine (b). Raises on API errors; the caller falls back to the rules engine."""
    import anthropic

    data = src if isinstance(src, bytes) else Path(src).read_bytes()
    client = client or anthropic.Anthropic()
    resp = client.beta.messages.create(
        model=_X["ai_model"],
        max_tokens=_X["ai_max_tokens"],
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",  # if the model declines, the API retries on a fallback model in the same call
        output_config={"effort": _X["ai_effort"], "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": base64.standard_b64encode(data).decode(),
                        },
                    },
                    {"type": "text", "text": PROMPT},
                ],
            }
        ],
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("the AI service declined to read this document")
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("the AI answer was cut off (document too long)")
    text = next(b.text for b in resp.content if b.type == "text")
    return from_ai_json(json.loads(text), name or (Path(src).name if not isinstance(src, bytes) else ""))


def from_ai_json(d: dict, name: str) -> Extraction:
    """Turn the schema's JSON into an Extraction, converting units with the shared helpers."""

    def fld(key: str, conv=None) -> Field:  # noqa: ANN001
        f = d.get(key) or {}
        v = f.get("value")
        if v is not None and conv:
            v = conv(v)
        return Field(v, f.get("page"), f.get("quote", ""), float(np.clip(f.get("confidence") or 0, 0, 1)))

    ex = Extraction(source=name, engine="ai")
    ex.supplier = fld("supplier")
    for k in ("period_start", "period_end", "contract_start", "contract_end", "fixed_on"):
        setattr(ex, k, fld(k, lambda v: parse_date(str(v))))
    ex.contract_type = fld("contract_type", lambda v: "variable" if str(v).lower().startswith("var") else "fixed")
    for p in d.get("prices") or []:
        try:
            eur = au.to_eur_per_mwh(p["value"], p["unit"], p["commodity"])
        except ValueError as e:
            ex.warnings.append(f"price not understood: {p.get('quote', '')} ({e})")
            continue
        ex.prices.append({**p, "eur_mwh": eur, "confidence": float(np.clip(p["confidence"], 0, 1))})
    for r in d.get("monthly") or []:
        ex.monthly.append(
            {
                "month": str(r["month"])[:7],
                "gas_m3": None
                if r["gas_value"] is None
                else float(ci.gas_to_m3(r["gas_value"], r["gas_unit"] or "m3")),
                "power_kwh": None
                if r["power_value"] is None
                else float(ci.power_to_kwh(r["power_value"], r["power_unit"] or "kWh")),
                "page": r["page"],
                "quote": r["quote"],
                "confidence": float(np.clip(r["confidence"], 0, 1)),
            }
        )
    return ex


def extract(src: str | Path | bytes, name: str = "", consent: bool = False, client: Any = None) -> Extraction:
    """Read one PDF. The AI engine runs only with consent=True AND ANTHROPIC_API_KEY set (or an explicit client);
    otherwise, or if the AI call fails, the rules engine. The engine used is in the result."""
    if consent and (client is not None or os.environ.get("ANTHROPIC_API_KEY")):
        try:
            return extract_ai(src, name, client)
        except Exception as e:  # noqa: BLE001 - any API problem: fall back to reading locally, and say so
            ex = extract_rules(src, name)
            ex.warnings.insert(0, f"AI service not used ({type(e).__name__}: {e}); read with the local rules.")
            return ex
    ex = extract_rules(src, name)
    if consent:
        ex.warnings.insert(0, "No ANTHROPIC_API_KEY set: read with the local rules only.")
    return ex


# --------------------------------------------------------------------------------------------
# Plausibility checks and tables for the person who confirms
# --------------------------------------------------------------------------------------------
def validate(exs: list[Extraction]) -> list[str]:
    """Flags: a month > spike_factor x the median, prices outside the plausible range, missing months between the
    first and last month, months or periods that overlap between documents."""
    flags = []
    m = monthly_table(exs)
    lo, hi = _X["price_range_eur_mwh"]
    for _, doc in m.groupby("source", sort=False):  # per document: compared with that document's median month
        for col, label in (("gas_m3", "gas"), ("power_kwh", "power")):
            s = doc.dropna(subset=[col])
            if len(s) < 3:
                continue
            med = s[col].median()
            for _, r in s[s[col] > _X["spike_factor"] * med].iterrows():
                flags.append(
                    f"{r.month} {label}: {r[col]:,.0f} is more than {_X['spike_factor']:g}x the median "
                    f"month ({med:,.0f}). Typo, or a different unit? ({r.source}, page {r.page})"
                )
    if len(m):
        months = pd.PeriodIndex(m.month.unique(), freq="M")
        full = pd.period_range(months.min(), months.max(), freq="M")
        missing = sorted(set(full) - set(months))
        if missing:
            flags.append("Missing months: " + ", ".join(str(p) for p in missing) + ".")
        dup = m[m.month.duplicated(keep=False)]
        for month, g in dup.groupby("month"):
            flags.append(f"{month} appears in more than one document: " + ", ".join(sorted(set(g.source))) + ".")
    for ex in exs:
        for p in ex.prices:
            if not lo <= p["eur_mwh"] <= hi:
                flags.append(
                    f"{ex.source}: {p['commodity']} price {p['value']:g} {p['unit']} = "
                    f"{p['eur_mwh']:,.1f} EUR/MWh, outside {lo}-{hi} EUR/MWh. Check the unit."
                )
    periods = [
        (ex.source, ex.period_start.value, ex.period_end.value)
        for ex in exs
        if ex.period_start.value is not None and ex.period_end.value is not None
    ]
    for i, (a, s1, e1) in enumerate(periods):
        for b, s2, e2 in periods[i + 1 :]:
            if s1 <= e2 and s2 <= e1:
                flags.append(f"Periods overlap: {a} ({s1.date()} - {e1.date()}) and {b} ({s2.date()} - {e2.date()}).")
    return flags


def monthly_table(exs: list[Extraction]) -> pd.DataFrame:
    """One row per month and document: month, gas_m3, power_kwh, source, page, quote, confidence."""
    rows = [{**r, "source": ex.source} for ex in exs for r in ex.monthly]
    cols = ["month", "gas_m3", "power_kwh", "source", "page", "quote", "confidence"]
    return pd.DataFrame(rows, columns=cols).sort_values("month", kind="stable").reset_index(drop=True)


def contracts_table(exs: list[Extraction]) -> pd.DataFrame:
    """Rows for the past-contracts audit (year, commodity, share, price, unit, fixed_on), one per delivery year and
    commodity, with the combined supply price (normal + off-peak lines merged, see combined_prices). A variable
    contract gives 'variable' rows (its prices are not fixed). The share of a fixed contract is set to 100 and must
    be checked by the person: documents rarely state the hedged share."""
    rows = []
    for ex in exs:
        prices = combined_prices(ex)
        if not prices:
            continue
        start = ex.contract_start.value or ex.period_start.value
        end = ex.contract_end.value or ex.period_end.value
        if start is None or end is None:
            continue
        variable = ex.contract_type.value == "variable"
        for y in range(start.year, end.year + 1):
            for c, p in prices.items():
                rows.append(
                    {
                        "year": y,
                        "commodity": c,
                        "share_%": None if variable else 100,
                        "price": "variable" if variable else round(p["eur_mwh"], 2),
                        "unit": "" if variable else "EUR/MWh",
                        "fixed_on": "" if ex.fixed_on.value is None else str(ex.fixed_on.value.date()),
                        "source": ex.source,
                        "quote": p["quote"],
                    }
                )
    return pd.DataFrame(rows, columns=["year", "commodity", "share_%", "price", "unit", "fixed_on", "source", "quote"])


def confirmed_company(table: pd.DataFrame, name: str, sector: str) -> ci.CompanyInput:
    """The confirmed monthly table -> a consumption profile (company_input, incl. its gap filling and warnings)."""
    t = table.groupby("month", as_index=False)[["gas_m3", "power_kwh"]].sum(min_count=1)
    mapping = {"date": "month", "gas": "gas_m3", "power": "power_kwh", "gas_unit": "m3", "power_unit": "kWh"}
    return ci.company_from_frame(t, mapping, name, sector)


def main(argv: list[str] | None = None) -> list[Extraction]:
    """Command line: python extract.py file.pdf [...] [--ai]."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+")
    ap.add_argument("--ai", action="store_true", help="consent: send the files to the AI service (needs the key)")
    args = ap.parse_args(argv)
    exs = [extract(f, Path(f).name, consent=args.ai) for f in args.files]
    pd.set_option("display.width", 200)
    for ex in exs:
        print(f"\n== {ex.source} (engine: {ex.engine})")
        for k in (
            "supplier",
            "period_start",
            "period_end",
            "contract_type",
            "contract_start",
            "contract_end",
            "fixed_on",
        ):
            f = getattr(ex, k)
            if f.value is not None:
                print(f'  {k:15s} {f.value!s:22s} p{f.page} conf {f.confidence:.2f}  "{f.quote}"')
        for p in ex.prices:
            print(
                f'  price {p["commodity"]:5s} {p["value"]:g} {p["unit"]} = {p["eur_mwh"]:.1f} EUR/MWh  "{p["quote"]}"'
            )
        for w in ex.warnings:
            print("  WARNING:", w)
    m = monthly_table(exs)
    if len(m):
        print("\nMonthly consumption (check every number before use):")
        print(m.drop(columns=["quote"]).to_string(index=False))
    for f in validate(exs):
        print("FLAG:", f)
    return exs


__all__ = [
    "Field",
    "Extraction",
    "extract",
    "extract_rules",
    "extract_ai",
    "from_ai_json",
    "validate",
    "monthly_table",
    "contracts_table",
    "confirmed_company",
    "parse_date",
    "SCHEMA",
    "main",
]
