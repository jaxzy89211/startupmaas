"""Report and app wording in English and Dutch, and number formatting per language.

Kept free of reportlab/streamlit imports so the dashboard can use the same sentences as the PDF without loading the
PDF library (report.py re-exports everything here)."""

from __future__ import annotations

TEXT = {
    "en": {
        "doc_title": "Energy price risk report",
        "prepared": "Prepared {today} · market prices up to {as_of} · next year = {delivery}",
        "consumption_h": "Your consumption",
        "consumption": "Gas: <b>{gas_m3} m³</b> per year ({gas_mwh} MWh) · Electricity: <b>{power} MWh</b> per year",
        "solar": " · Rooftop solar: {kwp} kWp",
        "bill_h": "Your bill next year if you stay variable",
        "expected_lbl": "Expected",
        "bad_lbl": "Bad case <font size=8>(average of the worst 5% of scenarios)</font>",
        "bill_note": "Gas and electricity commodity cost only, based on {n} simulated price scenarios.",
        "chart_h": "How much to fix: every option at a glance",
        "chart_note": "Each dot = a share of next year's energy fixed now. Left = a milder bad year; "
        "up = dearer on average. Star = our advice.",
        "rec_h": "Our recommendation",
        "rec": "Fix <b>{h}</b> of next year's gas and electricity, spread over <b>{k} monthly {tranche_word}</b>; "
        "start with <b>{m}</b> this month.",
        "rec_now": "Fix <b>{h}</b> of next year's gas and electricity, all of it this month.",
        "tranche_one": "tranche",
        "tranche_many": "tranches",
        "rec_effect": "With this plan: expected bill {e}, bad case {es} (instead of {es0}).",
        "tradeoff": "Each extra 10% fixed costs about <b>{a}</b> on average and removes about <b>{b}</b> "
        "from the bad case.",
        "stress_h": "What if 2022 happens again?",
        "stress_note": "Historical stress scenarios, not forecasts. Left: 2022 monthly prices as they were. Right: "
        "today's fixed price × (2022 avg ÷ 2021 avg), 2022 monthly pattern. Fixed share at today's price.",
        "honest_note": "Our model's 12-month ranges were too narrow before the 2021-22 crisis (they held 72% of "
        "outcomes instead of 90%), so the bad case is never lower than a repeat of the 2021->2022 price shock.",
        "bad_src": {"model": "model", "stress": "2021->22 shock"},
        "bad_from": "Bad case <font size=8>(source: {src})</font>",
        "rule_ladder": "Rule: the HedgeWise ladder (pre-registered {date}): the model says {m}; the ladder keeps the "
        "share between {base} and last season + 10 points ({cap}).",
        "next_year": "Also start {year}: buy <b>{now}</b> of {year}'s volume this month, {base} by December "
        "(the ladder's base layer).",
        "green": "Green investments:",
        "audit_h": "Your past contracts vs the strategies (realised prices)",
        "audit_intro": "What you actually paid for gas and power (commodity cost only), next to what the same "
        "consumption would have cost with each strategy, using only the real historical prices. Your entries: "
        "{n} contract lines, years {span}.",
        "audit_total": "Total",
        "audit_worst": "Worst year",
        "audit_note": "Same rules and real prices as the HedgeWise backtest; the strategies are not tuned to your "
        "data. Where you did better than a strategy, the table shows it. The HedgeWise ladder was designed after "
        "seeing 2021-2025, so its numbers are not independent evidence. Fixed shares cover the same share of every "
        "month; the rest is priced at the realised monthly average (day-ahead power, TTF gas).",
        "footer": "<b>HedgeWise</b> · Assumptions: commodity cost only (no network tariffs, energy tax or supplier "
        "margin); {fixed}; volumes are "
        "taken as known. Scenarios come from a statistical model of past prices and can miss events "
        "that never happened before. This is decision support, not financial advice.",
        "fixed_proxy": "the fixed price is a proxy for real forward prices (recent market price + 5%)",
        "fixed_real": "fixed prices are real forward prices ({products})",
        "chart": {},
    },
    "nl": {
        "doc_title": "Rapport energieprijsrisico",
        "prepared": "Opgesteld {today} · marktprijzen t/m {as_of} · volgend jaar = {delivery}",
        "consumption_h": "Uw verbruik",
        "consumption": "Gas: <b>{gas_m3} m³</b> per jaar ({gas_mwh} MWh) · Elektriciteit: <b>{power} MWh</b> per jaar",
        "solar": " · Zonnepanelen: {kwp} kWp",
        "bill_h": "Uw rekening volgend jaar als u variabel blijft",
        "expected_lbl": "Verwacht",
        "bad_lbl": "Slecht scenario <font size=8>(gem. van de 5% duurste scenario's)</font>",
        "bill_note": "Alleen de kosten van gas en elektriciteit zelf, op basis van {n} gesimuleerde prijsscenario's.",
        "chart_h": "Hoeveel vastzetten: alle opties in één oogopslag",
        "chart_note": "Elk punt = een deel van volgend jaar dat u nu vastzet. Links = een milder slecht jaar; "
        "hoger = gemiddeld duurder. Ster = ons advies.",
        "rec_h": "Ons advies",
        "rec": "Zet <b>{h}</b> van het gas en de elektriciteit voor volgend jaar vast, verdeeld over "
        "<b>{k} maandelijkse {tranche_word}</b>; begin deze maand met <b>{m}</b>.",
        "rec_now": "Zet <b>{h}</b> van het gas en de elektriciteit voor volgend jaar vast, alles deze maand.",
        "tranche_one": "aankoop",
        "tranche_many": "aankopen",
        "rec_effect": "Met dit plan: verwachte rekening {e}, slecht scenario {es} (in plaats van {es0}).",
        "tradeoff": "Elke extra 10% vastzetten kost gemiddeld ongeveer <b>{a}</b> en haalt ongeveer <b>{b}</b> "
        "van het slechte scenario af.",
        "stress_h": "Wat als 2022 zich herhaalt?",
        "stress_note": "Historische stressscenario's, geen voorspellingen. Links: maandprijzen 2022 zoals ze waren. "
        "Rechts: vaste prijs nu × (gem. 2022 ÷ gem. 2021), maandpatroon 2022. Vast deel tegen de prijs van nu.",
        "honest_note": "De 12-maandsbandbreedtes van ons model waren te smal vóór de crisis van 2021-22 (ze bevatten "
        "72% van de uitkomsten in plaats van 90%), dus het slechte scenario is nooit lager dan een herhaling van de "
        "prijsschok van 2021->2022.",
        "bad_src": {"model": "model", "stress": "schok als 2021->22"},
        "bad_from": "Slecht scenario <font size=8>(bron: {src})</font>",
        "rule_ladder": "Regel: de HedgeWise-ladder (vooraf vastgelegd {date}): het model zegt {m}; de ladder houdt het "
        "aandeel tussen {base} en vorig seizoen + 10 punten ({cap}).",
        "next_year": "Begin ook aan {year}: koop deze maand <b>{now}</b> van het volume voor {year}, {base} vóór "
        "december (de basislaag van de ladder).",
        "green": "Groene investeringen:",
        "audit_h": "Uw contracten vs de strategieën (gerealiseerde prijzen)",
        "audit_intro": "Wat u echt betaalde voor gas en stroom (alleen de energie zelf), naast wat hetzelfde verbruik "
        "had gekost met elke strategie, met alleen de echte historische prijzen. Uw invoer: {n} contractregels, "
        "jaren {span}.",
        "audit_total": "Totaal",
        "audit_worst": "Duurste jaar",
        "audit_note": "Zelfde regels en echte prijzen als de HedgeWise-backtest; de strategieën zijn niet afgestemd "
        "op uw data. Waar u het beter deed dan een strategie, staat dat in de tabel. De HedgeWise-ladder is "
        "ontworpen na het zien van 2021-2025; die cijfers zijn dus geen onafhankelijk bewijs. Een vast aandeel geldt "
        "voor elke maand; de rest tegen het gerealiseerde maandgemiddelde (day-ahead stroom, TTF-gas).",
        "footer": "<b>HedgeWise</b> · Aannames: alleen de kosten van de energie zelf (geen netbeheerkosten, "
        "energiebelasting of leveranciersmarge); {fixed}; het verbruik wordt als bekend aangenomen. "
        "De scenario's komen uit een "
        "statistisch model van prijzen uit het verleden en kunnen gebeurtenissen missen die nog nooit "
        "zijn voorgekomen. Dit is beslisondersteuning, geen financieel advies.",
        "fixed_proxy": "de vaste prijs is een benadering van echte termijnprijzen (recente marktprijs + 5%)",
        "fixed_real": "de vaste prijzen zijn echte termijnprijzen ({products})",
        "chart": {
            "title": "Kies uw afweging: deel van volgend jaar vastgezet",
            "xlabel": "Slecht scenario: gemiddelde van de 5% duurste scenario's",
            "ylabel": "Verwachte rekening",
            "pick": "HedgeWise-advies: {h} vastzetten (risicoaversie {g})",
            "pick_budget": "HedgeWise-advies: {h} vastzetten (budget)",
            "pick_ladder": "HedgeWise-ladder: {h} vastzetten",
            "xlabel_honest": "Slecht scenario: de hoogste van het model (5% duurste) en een schok als 2021->22",
            "stress_points": "open punt = slecht scenario uit de schok",
            "tradeoff": "Elke extra 10% vast: gemiddeld ca. € {e}k meer,\n€ {es}k minder in het slechte scenario",
            "decimal": ",",
        },
    },
}
TEXT["en"]["chart"] = {
    "xlabel": "Bad case: average of the worst 5% of scenarios",
    "ylabel": "Expected bill",
    "xlabel_honest": "Bad case: the worse of the model's worst 5% and a 2021->22-size shock",
}
NL_MONTHS = [
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


def _num(x: float, lang: str, decimals: int = 0) -> str:
    """Thousands separators: 1,234.5 (en) / 1.234,5 (nl)."""
    s = f"{x:,.{decimals}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".") if lang == "nl" else s


def money(x: float, lang: str) -> str:
    """€2.07m / € 2,07 mln for millions; €25k / € 25.000 below."""
    if abs(x) >= 1e6:
        return f"€{_num(x / 1e6, lang, 2)}m" if lang == "en" else f"€\u00a0{_num(x / 1e6, lang, 2)}\u00a0mln"
    if lang == "en":
        return f"€{_num(x / 1e3, lang, 0)}k"
    return f"€\u00a0{_num(round(x, -3), lang, 0)}"


def pct(x: float) -> str:
    """0.55 -> '55%'."""
    return f"{x:.0%}"


def ladder_lines(a: object, lang: str) -> list[str]:
    """The rule line and (in H2 of the year before) next year's base-layer line, if the ladder is the plan."""
    from .settings import BACKTEST

    r, T = a.recommendation, TEXT[lang]
    if r.get("rule") != "ladder":
        return []
    out = [
        T["rule_ladder"].format(
            date=BACKTEST["ladder_preregistered"],
            m=pct(r["model_target"]),
            base=pct(BACKTEST["ladder_base"]),
            cap=pct(r["ladder_cap"]),
        )
    ]
    nx = r.get("next_year")
    if nx:
        out.append(T["next_year"].format(year=nx["year"], now=pct(nx["buy_now"]), base=pct(nx["base"])))
    return out
