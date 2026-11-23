"""Every text of the dashboard (app.py) in English and Dutch. Sentences shared with the PDF come from texts.py."""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

from .texts import TEXT as REPORT_TEXT
from .texts import _num, money, pct

UI = {
    "en": {
        "lang": "Taal / Language",
        "caption": "How bad can next year's energy bill get, and how much should you fix? Prices: Dutch day-ahead "
        "power + TTF gas up to {date}. Power data: Energy-Charts (CC BY 4.0, Bundesnetzagentur | SMARD.de).",
        "interview": "Interview mode",
        "interview_help": "A step-by-step flow for interviews: no model settings, sensible defaults.",
        "interview_on": "Interview mode is on: follow steps 1-5 on the right. Switch it off for all settings.",
        "company_h": "Your company",
        "name": "Company name",
        "source": "Consumption data",
        "src_yearly": "Yearly totals + sector",
        "src_upload": "Upload bills (CSV / Excel)",
        "src_pdf": "Invoices or contracts (PDF)",
        "pdf_upload": "Invoices, annual statements or supplier contracts (PDF; several at once)",
        "pdf_consent": "Send these documents to the AI service (Anthropic) to read them. Only with the company's "
        "permission: bills are confidential.",
        "pdf_no_key": "AI reading is off (no ANTHROPIC_API_KEY); documents are read on this computer with simple "
        "rules.",
        "pdf_reading": "Reading the documents ...",
        "pdf_check": "Check every number against the document (the quote shows where it came from), correct it in "
        "the table, then confirm. Nothing is used before you confirm.",
        "pdf_engine": "{file}: read with {engine}",
        "pdf_contracts": "Prices found (these go to 'past contracts'; the share is set to 100%: check it)",
        "pdf_confirm": "Confirm these numbers",
        "pdf_confirmed": "Confirmed. HedgeWise now uses these monthly volumes.",
        "pdf_wait": "Check the table and click 'Confirm these numbers'.",
        "pdf_empty": "No monthly consumption was found. Enter the numbers in the table, or use yearly totals.",
        "source_help": "Upload 12-24 months of bills or a supplier-portal export, or just enter yearly totals.",
        "sector": "Sector (seasonal profile)",
        "sector_help": "Used to spread yearly totals over the months and to fill 1-3 missing months. "
        "The profiles are assumptions.",
        "upload": "Monthly bills or portal export",
        "read_error": "Could not read this file: {e}",
        "check_cols": "Check the columns HedgeWise found:",
        "col_date": "Month / date column",
        "col_gas": "Gas column",
        "unit_gas": "Gas unit",
        "col_power": "Power column",
        "unit_power": "Power unit",
        "none": "(none)",
        "preview": "Preview of the file",
        "upload_wait": "Upload a file, or switch to yearly totals.",
        "gas_year": "Gas per year",
        "power_year": "Electricity per year",
        "volumes": "{gas} m³ gas · {power} MWh power per year",
        "green_h": "Green investments",
        "solar": "Rooftop solar (kWp)",
        "battery": "Battery capacity (kWh)",
        "battery_kw": "Battery power (kW; 0 = half the capacity)",
        "ppa": "PPA: share of the power volume (%)",
        "ppa_price": "PPA price (EUR/MWh; 0 = today's fixed price)",
        "ppa_years": "PPA length (years)",
        "decision_h": "Decision",
        "date": "Decision date",
        "date_today": "Today",
        "date_2021": "30 Jun 2021 (before the crisis)",
        "date_2022": "30 Jun 2022 (mid-crisis)",
        "date_custom": "Custom",
        "as_of": "As of",
        "goal": "Goal",
        "goal_ladder": "HedgeWise ladder (recommended, pre-registered)",
        "goal_model": "Model-only (not recommended: procyclical, see RESULTS.md)",
        "prev_target": "Ladder: share of next year already fixed last season (%, 0 = none)",
        "m_bad_src": "{src}",
        "src_model": "from the model",
        "src_stress": "from the 2021->22 shock",
        "goal_balance": "Balance cost and risk",
        "goal_budget": "Stay within a budget",
        "risk": "Risk aversion",
        "risk_help": "0 = only care about the average bill, 1 = care a lot about bad years. The chart 'Pick your "
        "trade-off' shows every other choice.",
        "budget": "Energy budget (EUR, 0 = last 12 months + 20%)",
        "timing": "Allow fixing everything at once (market timing)",
        "scenarios": "Scenarios",
        "tab_today": "Today's risk",
        "tab_audit": "Your past contracts",
        "tab_tracker": "Plan tracker",
        "tr_client": "Client (clients/*.yaml)",
        "tr_none": "No client files yet: copy clients/example.yaml (see MONITORING.md).",
        "tr_plan": "Plan for {year}: target {target}, bought {bought}, planned by now {planned}",
        "tr_table": "Tranches: planned vs bought (cumulative share of the yearly volume)",
        "tr_month": "month",
        "tr_planned": "planned",
        "tr_bought": "bought",
        "tr_history": "Bad case over time (EUR m), from the monthly updates",
        "tr_no_history": "No monthly updates yet for this client.",
        "tr_now": "bad case, bought so far",
        "tr_full": "bad case, full plan",
        "tr_run": "Compute this month's update (draft)",
        "tr_running": "Computing the update ...",
        "tr_draft": "DRAFT: check the numbers before you send anything. Nothing is sent automatically.",
        "tr_pdf": "Download the update (PDF)",
        "tr_email": "Email draft",
        "spinner": "Fitting the price model and simulating price scenarios ...",
        "m_expected": "Expected bill, all variable",
        "m_bad": "Bad case, all variable",
        "m_bad_help": "The average of the worst 5% of the simulated price scenarios.",
        "m_bad_hw": "Bad case with HedgeWise",
        "m_share": "Recommended share to fix",
        "share_split": "gas {g} · power {p}",
        "report_lang": "Report language",
        "add_audit": "Add page 2: your past contracts",
        "download": "Download report (PDF)",
        "frontier_caption": "Each dot = a share of next year's energy fixed now; the star is the advice. ",
        "more_charts": "More charts (price scenarios, cost distribution; English)",
        "stress_h": "What if 2022 happens again?",
        "stress_line": "**{label}** (gas avg €{g:.0f}, power €{p:.0f}/MWh): all variable {v} · HedgeWise plan {h} · "
        "fix 100% {f}",
        "stress_note": "Historical stress scenarios, not forecasts. (a) The realised 2022 monthly prices exactly as "
        "they were. (b) Today's fixed price × (2022 average / 2021 average) per commodity, with the 2022 monthly "
        "shape. The hedged share is priced at today's fixed price.",
        "strategies_h": "Strategy comparison (EUR k)",
        "what_happened": "What actually happened: the all-variable bill for {y} was **{r}** (model expected {e}, "
        "bad case {b}).",
        "green_section": "Green investments",
        "green_none": "Choose rooftop solar, a battery or a PPA in the sidebar to see what each does to the expected "
        "and the bad-case bill, and its payback in normal and crisis conditions.",
        "plan_plus": "HedgeWise plan {inv}",
        "assumptions": "Assumptions",
        "model_details": "Model details",
        "fixed_used": "**Fixed prices used:** ",
        "model_note": "Commodity cost only (no network tariffs, energy tax or supplier margin). Without "
        "data/forwards.csv the fixed price is a stand-in for real forward prices: 30-day average spot (power "
        "seasonally adjusted) + 5%. The simulated price level is anchored to the fixed price (minus the premium); "
        "the model supplies the risk around it.",
        "audit_intro": "Enter what you actually bought per delivery year: the share fixed, the price and its unit, "
        "and (optional) the date you fixed. Write **variable** as the price for a share you left variable. Prices "
        "**without** energy tax, network costs and VAT. Your consumption profile comes from the sidebar.",
        "audit_upload": "... or upload a table (CSV / Excel: year, commodity, share, price, unit, fixed_on)",
        "audit_run": "Compare with the strategies",
        "audit_wait": "Running the strategies on real prices (each decision re-fits the model; about a minute) ...",
        "audit_table": "Realised commodity cost per year (EUR k)",
        "audit_total": "TOTAL",
        "audit_worst": "WORST YEAR",
        "audit_detail": "How your cost was computed",
        "audit_proxy": "Your fixed prices vs the market proxy (30-day spot + 5%) on the day you fixed, EUR/MWh",
        "audit_note": "Same rules and real prices as the backtest (RESULTS.md). The strategies are not tuned to your "
        "data. The HedgeWise ladder was designed after seeing 2021-2025, so its numbers are not independent "
        "evidence.",
        "audit_cols": {
            "year": "year",
            "commodity": "commodity",
            "share_%": "share_%",
            "price": "price",
            "unit": "unit",
            "fixed_on": "fixed_on",
        },
        "step1": "Step 1 · Company name",
        "step2": "Step 2 · Energy use",
        "step2_help": "Upload the bills or a supplier-portal export (CSV/Excel), or type the yearly totals from the "
        "annual statement.",
        "step3": "Step 3 · Past contracts (optional)",
        "step3_check": "The company shared what it paid in past years",
        "step4": "Step 4 · Results",
        "step5": "Step 5 · Download the report",
        "step_wait": "Fill in step 2 first.",
        "pdf_ready": "The PDF is in the language chosen at the top of the sidebar.",
    },
    "nl": {
        "lang": "Taal / Language",
        "caption": "Hoe hoog kan de energierekening van volgend jaar worden, en hoeveel moet u vastzetten? Prijzen: "
        "Nederlandse day-ahead stroom + TTF-gas t/m {date}. Stroomdata: Energy-Charts (CC BY 4.0, "
        "Bundesnetzagentur | SMARD.de).",
        "interview": "Interviewmodus",
        "interview_help": "Een stap-voor-stap-route voor interviews: geen modelinstellingen, verstandige standaarden.",
        "interview_on": "Interviewmodus staat aan: volg stap 1-5 rechts. Zet hem uit voor alle instellingen.",
        "company_h": "Uw bedrijf",
        "name": "Bedrijfsnaam",
        "source": "Verbruiksgegevens",
        "src_yearly": "Jaartotalen + sector",
        "src_upload": "Facturen uploaden (CSV / Excel)",
        "src_pdf": "Facturen of contracten (PDF)",
        "pdf_upload": "Facturen, jaarafrekeningen of leverancierscontracten (PDF; meerdere tegelijk)",
        "pdf_consent": "Stuur deze documenten naar de AI-dienst (Anthropic) om ze te lezen. Alleen met toestemming "
        "van het bedrijf: facturen zijn vertrouwelijk.",
        "pdf_no_key": "Lezen met AI staat uit (geen ANTHROPIC_API_KEY); de documenten worden op deze computer met "
        "eenvoudige regels gelezen.",
        "pdf_reading": "Documenten lezen ...",
        "pdf_check": "Controleer elk getal met het document (het citaat laat zien waar het vandaan komt), verbeter "
        "het in de tabel en bevestig. Niets wordt gebruikt voordat u bevestigt.",
        "pdf_engine": "{file}: gelezen met {engine}",
        "pdf_contracts": "Gevonden prijzen (die gaan naar 'eerdere contracten'; het aandeel staat op 100%: "
        "controleer dat)",
        "pdf_confirm": "Bevestig deze getallen",
        "pdf_confirmed": "Bevestigd. HedgeWise gebruikt nu deze maandvolumes.",
        "pdf_wait": "Controleer de tabel en klik op 'Bevestig deze getallen'.",
        "pdf_empty": "Er is geen maandverbruik gevonden. Vul de getallen in de tabel in, of gebruik jaartotalen.",
        "source_help": "Upload 12-24 maanden facturen of een export uit het leveranciersportaal, of vul alleen "
        "jaartotalen in.",
        "sector": "Sector (seizoensprofiel)",
        "sector_help": "Om jaartotalen over de maanden te verdelen en 1-3 ontbrekende maanden aan te vullen. De "
        "profielen zijn aannames.",
        "upload": "Maandfacturen of portaalexport",
        "read_error": "Dit bestand kon niet worden gelezen: {e}",
        "check_cols": "Controleer de kolommen die HedgeWise vond:",
        "col_date": "Kolom maand / datum",
        "col_gas": "Kolom gas",
        "unit_gas": "Eenheid gas",
        "col_power": "Kolom stroom",
        "unit_power": "Eenheid stroom",
        "none": "(geen)",
        "preview": "Voorbeeld van het bestand",
        "upload_wait": "Upload een bestand, of kies jaartotalen.",
        "gas_year": "Gas per jaar",
        "power_year": "Elektriciteit per jaar",
        "volumes": "{gas} m³ gas · {power} MWh stroom per jaar",
        "green_h": "Groene investeringen",
        "solar": "Zonnepanelen op het dak (kWp)",
        "battery": "Batterijcapaciteit (kWh)",
        "battery_kw": "Batterijvermogen (kW; 0 = de helft van de capaciteit)",
        "ppa": "PPA: deel van het stroomvolume (%)",
        "ppa_price": "PPA-prijs (EUR/MWh; 0 = de vaste prijs van vandaag)",
        "ppa_years": "Looptijd PPA (jaren)",
        "decision_h": "Beslissing",
        "date": "Beslisdatum",
        "date_today": "Vandaag",
        "date_2021": "30 jun 2021 (voor de crisis)",
        "date_2022": "30 jun 2022 (midden in de crisis)",
        "date_custom": "Zelf kiezen",
        "as_of": "Per datum",
        "goal": "Doel",
        "goal_ladder": "HedgeWise-ladder (aanbevolen, vooraf vastgelegd)",
        "goal_model": "Alleen model (niet aanbevolen: procyclisch, zie RESULTS.md)",
        "prev_target": "Ladder: deel van volgend jaar dat vorig seizoen al vastlag (%, 0 = niets)",
        "m_bad_src": "{src}",
        "src_model": "uit het model",
        "src_stress": "uit de schok als 2021->22",
        "goal_balance": "Kosten en risico afwegen",
        "goal_budget": "Binnen een budget blijven",
        "risk": "Risicoaversie",
        "risk_help": "0 = alleen de gemiddelde rekening telt, 1 = slechte jaren tellen zwaar. De grafiek 'Kies uw "
        "afweging' toont alle andere keuzes.",
        "budget": "Energiebudget (EUR, 0 = laatste 12 maanden + 20%)",
        "timing": "Alles in één keer vastzetten toestaan (markttiming)",
        "scenarios": "Scenario's",
        "tab_today": "Risico vandaag",
        "tab_audit": "Uw contracten",
        "tab_tracker": "Plan volgen",
        "tr_client": "Klant (clients/*.yaml)",
        "tr_none": "Nog geen klantbestanden: kopieer clients/example.yaml (zie MONITORING.md).",
        "tr_plan": "Plan voor {year}: doel {target}, gekocht {bought}, gepland tot nu {planned}",
        "tr_table": "Tranches: gepland vs gekocht (cumulatief deel van het jaarvolume)",
        "tr_month": "maand",
        "tr_planned": "gepland",
        "tr_bought": "gekocht",
        "tr_history": "Slecht scenario door de tijd (EUR mln), uit de maandupdates",
        "tr_no_history": "Nog geen maandupdates voor deze klant.",
        "tr_now": "slecht scenario, nu gekocht",
        "tr_full": "slecht scenario, hele plan",
        "tr_run": "Bereken de update van deze maand (concept)",
        "tr_running": "Update berekenen ...",
        "tr_draft": "CONCEPT: controleer de getallen voordat u iets verstuurt. Er wordt niets automatisch verzonden.",
        "tr_pdf": "Update downloaden (PDF)",
        "tr_email": "Concept-e-mail",
        "spinner": "Prijsmodel schatten en prijsscenario's simuleren ...",
        "m_expected": "Verwachte rekening, alles variabel",
        "m_bad": "Slecht scenario, alles variabel",
        "m_bad_help": "Het gemiddelde van de 5% duurste gesimuleerde prijsscenario's.",
        "m_bad_hw": "Slecht scenario met HedgeWise",
        "m_share": "Advies: deel vastzetten",
        "share_split": "gas {g} · stroom {p}",
        "report_lang": "Taal van het rapport",
        "add_audit": "Pagina 2 toevoegen: uw contracten",
        "download": "Rapport downloaden (PDF)",
        "frontier_caption": "Elk punt = een deel van volgend jaar dat u nu vastzet; de ster is het advies. ",
        "more_charts": "Meer grafieken (prijsscenario's, kostenverdeling; Engelstalig)",
        "stress_h": "Wat als 2022 zich herhaalt?",
        "stress_line": "**{label}** (gas gem. € {g:.0f}, stroom € {p:.0f}/MWh): alles variabel {v} · HedgeWise-plan "
        "{h} · 100% vast {f}",
        "stress_note": "Historische stressscenario's, geen voorspellingen. (a) De gerealiseerde maandprijzen van 2022 "
        "precies zoals ze waren. (b) De vaste prijs van vandaag × (gemiddelde 2022 / gemiddelde 2021) per product, "
        "met het maandpatroon van 2022. Het vastgezette deel tegen de vaste prijs van vandaag.",
        "strategies_h": "Strategieën vergeleken (EUR k)",
        "what_happened": "Wat er echt gebeurde: de rekening bij alles variabel was in {y} **{r}** (model verwachtte "
        "{e}, slecht scenario {b}).",
        "green_section": "Groene investeringen",
        "green_none": "Kies zonnepanelen, een batterij of een PPA in de zijbalk om te zien wat elk doet met de "
        "verwachte rekening en het slechte scenario, en de terugverdientijd bij normale prijzen en in een crisis.",
        "plan_plus": "HedgeWise-plan {inv}",
        "assumptions": "Aannames",
        "model_details": "Modeldetails",
        "fixed_used": "**Gebruikte vaste prijzen:** ",
        "model_note": "Alleen de kosten van de energie zelf (geen netbeheerkosten, energiebelasting of "
        "leveranciersmarge). Zonder data/forwards.csv is de vaste prijs een benadering van echte termijnprijzen: "
        "30-daags gemiddelde spotprijs (stroom seizoensgecorrigeerd) + 5%. Het gesimuleerde prijsniveau is gekoppeld "
        "aan de vaste prijs (min de premie); het model levert het risico eromheen.",
        "audit_intro": "Vul in wat u echt heeft ingekocht per leveringsjaar: het vastgezette aandeel, de prijs en de "
        "eenheid, en (optioneel) de datum waarop u vastzette. Schrijf **variabel** als prijs voor een deel dat "
        "variabel bleef. Prijzen **zonder** energiebelasting, netkosten en btw. Uw verbruiksprofiel komt uit de "
        "zijbalk.",
        "audit_upload": "... of upload een tabel (CSV / Excel: jaar, product, aandeel, prijs, eenheid, datum)",
        "audit_run": "Vergelijk met de strategieën",
        "audit_wait": "Strategieën doorrekenen op echte prijzen (elk besluit schat het model opnieuw; ongeveer een "
        "minuut) ...",
        "audit_table": "Gerealiseerde energiekosten per jaar (EUR k)",
        "audit_total": "TOTAAL",
        "audit_worst": "DUURSTE JAAR",
        "audit_detail": "Hoe uw kosten zijn berekend",
        "audit_proxy": "Uw vaste prijzen vs de marktprijs-benadering (30-daags spot + 5%) op de dag van vastzetten, "
        "EUR/MWh",
        "audit_note": "Zelfde regels en echte prijzen als de backtest (RESULTS.md). De strategieën zijn niet "
        "afgestemd op uw data. De HedgeWise-ladder is ontworpen na het zien van 2021-2025; die cijfers zijn dus geen "
        "onafhankelijk bewijs.",
        "audit_cols": {
            "year": "jaar",
            "commodity": "product",
            "share_%": "aandeel_%",
            "price": "prijs",
            "unit": "eenheid",
            "fixed_on": "datum",
        },
        "step1": "Stap 1 · Bedrijfsnaam",
        "step2": "Stap 2 · Energieverbruik",
        "step2_help": "Upload de facturen of een export uit het leveranciersportaal (CSV/Excel), of typ de "
        "jaartotalen van de jaarafrekening over.",
        "step3": "Stap 3 · Eerdere contracten (optioneel)",
        "step3_check": "Het bedrijf heeft gedeeld wat het in eerdere jaren betaalde",
        "step4": "Stap 4 · Resultaten",
        "step5": "Stap 5 · Rapport downloaden",
        "step_wait": "Vul eerst stap 2 in.",
        "pdf_ready": "De PDF is in de taal die bovenaan de zijbalk is gekozen.",
    },
}


SECTOR_NL = {
    "food processing": "voedingsmiddelenindustrie",
    "metal": "metaal",
    "greenhouse horticulture": "glastuinbouw",
    "offices": "kantoren",
}
COMMODITY_NL = {"gas": "gas", "power": "stroom"}
MONTH_NL = {"Mar": "mrt", "May": "mei", "Oct": "okt"}


def sector_label(sector: str, lang: str) -> str:
    """Sector name for display (the config key stays English)."""
    return SECTOR_NL.get(sector, sector) if lang == "nl" else sector


def number(x: float, lang: str) -> str:
    """1,200,000 (en) / 1.200.000 (nl)."""
    return _num(x, lang)


_WARN_NL = [
    (
        r"Monthly split uses the '(.+)' seasonal profile \(an assumption, not your bills\)\.",
        lambda m: (
            f"De maandverdeling gebruikt het seizoensprofiel '{sector_label(m[1], 'nl')}' (een aanname, niet "
            "uw facturen)."
        ),
    ),
    (
        r"(gas|power): no data for (.+); filled in from the '(.+)' seasonal profile \(an assumption\)\. "
        r"Check these months with the company\.",
        lambda m: (
            f"{COMMODITY_NL[m[1]]}: geen gegevens voor "
            + ", ".join(MONTH_NL.get(x, x.lower()) for x in m[2].split(", "))
            + f"; aangevuld met het seizoensprofiel '{sector_label(m[3], 'nl')}' (een aanname). Controleer deze "
            "maanden met het bedrijf."
        ),
    ),
    (
        r"(\d+) row\(s\) skipped: '(.+)' is not a recognisable month \(e\.g\. '(.+)'\)\.",
        lambda m: f"{m[1]} rij(en) overgeslagen: '{m[2]}' is geen herkenbare maand (bijv. '{m[3]}').",
    ),
    (
        r"Data covers (\d+) years; using the average per calendar month\.",
        lambda m: f"De gegevens beslaan {m[1]} jaar; het gemiddelde per kalendermaand wordt gebruikt.",
    ),
    (
        r"No (gas|power) column: .+ consumption set to zero\.",
        lambda m: f"Geen kolom voor {COMMODITY_NL[m[1]]}: dat verbruik is op nul gezet.",
    ),
]


def warning_text(w: str, lang: str) -> str:
    """A company_input warning in the chosen language (unknown warnings stay as they are)."""
    if lang != "nl":
        return w
    for pat, fmt in _WARN_NL:
        m = re.fullmatch(pat, w)
        if m:
            return fmt(m)
    return w


def _md(s: str) -> str:
    """reportlab markup -> Markdown (<b>x</b> -> **x**, other tags dropped, non-breaking spaces kept)."""
    s = re.sub(r"</?b>", "**", s)
    return re.sub(r"<[^>]+>", "", s)


def recommendation_md(a: object, lang: str) -> str:
    """The advice in the PDF's own words (texts.TEXT), as Markdown, plus the trade-off sentence."""
    from .strategies import frontier_scale_sentence

    T = REPORT_TEXT[lang]
    r, t = a.recommendation, a.table
    hw_row = t.iloc[-1]
    k = len(r["tranches"])
    if r["execution"] == "now" or k == 1:
        rec = T["rec_now"].format(h=pct(r["hedge_gas"]))
    else:
        rec = T["rec"].format(
            h=pct(r["hedge_gas"]),
            k=k,
            m=pct(r["buy_now_gas"]),
            tranche_word=T["tranche_many"] if k > 1 else T["tranche_one"],
        )
    if not np.isclose(r["hedge_gas"], r["hedge_power"]):
        rec += " (" + UI[lang]["share_split"].format(g=pct(r["hedge_gas"]), p=pct(r["hedge_power"])) + ")"
    from .strategies import bad_case
    from .texts import ladder_lines

    eff = T["rec_effect"].format(
        e=money(hw_row.expected, lang), es=money(bad_case(a)[0], lang), es0=money(bad_case(a, "All variable")[0], lang)
    )
    extra = "".join("  \n" + _md(x) for x in ladder_lines(a, lang))
    return _md(rec) + "  \n" + _md(eff) + "  \n" + frontier_scale_sentence(a, lang) + extra


def eur_m(x: float, lang: str) -> str:
    """€2.07m / € 2,07 mln."""
    return money(x, lang) if abs(x) >= 1e6 else (f"€{x / 1e6:.2f}m" if lang == "en" else money(x, lang))


def strategy_table(a: object, lang: str) -> pd.DataFrame:
    """The strategy comparison in EUR k with translated row and column names."""
    nl_rows = {
        "All variable": "Alles variabel",
        "Fix 60% now": "60% nu vastzetten",
        "Fix 100% now": "100% nu vastzetten",
    }
    nl_cols = {
        "expected": "verwacht",
        "p50": "mediaan",
        "var95": "VaR95",
        "es95": "slecht scenario (ES95)",
        "worst": "slechtste",
        "best": "beste",
        "cost_at_risk": "risico (ES95 - verwacht)",
        "shock": "schok als 2021->22",
        "bad_case": "slecht scenario (getoond)",
    }
    t = (a.table / 1e3).round(0).astype(int)
    if lang == "nl":
        t = t.rename(columns=nl_cols)
        t.index = [nl_rows.get(i, i.replace("power", "stroom").replace("now", "nu")) for i in t.index]
    return t


__all__ = ["UI", "recommendation_md", "eur_m", "strategy_table", "sector_label", "number", "warning_text"]
