"""Smoke tests of the dashboard (Streamlit's headless AppTest): both languages, interview mode, audit, green."""

from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "app.py")


def _app() -> AppTest:
    return AppTest.from_file(APP, default_timeout=600).run()


def test_today_view_renders():
    at = _app()
    assert not at.exception
    labels = [m.label for m in at.metric]
    assert "Expected bill, all variable" in labels and "Recommended share to fix" in labels
    assert any("Fixing everything costs" in c.value for c in at.caption)
    assert any("Fix **" in i.value for i in at.info)  # the advice, in the PDF's own words


def test_dutch_switch_translates_the_page():
    at = _app()
    at.radio(key="lang").set_value("NL").run()
    assert not at.exception
    labels = [m.label for m in at.metric]
    assert "Verwachte rekening, alles variabel" in labels and "Advies: deel vastzetten" in labels
    assert any("Zet **" in i.value for i in at.info)
    assert at.sidebar.header[0].value == "Uw bedrijf"
    assert [t.label for t in at.tabs] == ["Risico vandaag", "Uw contracten"]


def test_sector_and_upload_modes():
    at = _app()
    at.sidebar.selectbox(key="sector").select("offices").run()
    assert not at.exception and any("offices" in w.value for w in at.sidebar.warning)
    at.sidebar.radio(key="source").set_value("upload").run()
    assert not at.exception and at.sidebar.info


def test_interview_mode_hides_settings_and_walks_through_the_steps():
    at = _app()
    at.toggle(key="interview").set_value(True).run()
    assert not at.exception
    assert not [h for h in at.sidebar.header if h.value == "Decision"]  # no model settings
    steps = [s.value for s in at.subheader]
    assert steps[:5] == [
        "Step 1 · Company name",
        "Step 2 · Energy use",
        "Step 3 · Past contracts (optional)",
        "Step 4 · Results",
        "What if 2022 happens again?",
    ]
    assert "Step 5 · Download the report" in steps
    at.text_input(key="iv_name").input("Bakkerij Venlo").run()
    at.radio(key="lang").set_value("NL").run()
    assert not at.exception and "Stap 5 · Rapport downloaden" in [s.value for s in at.subheader]


def test_past_contracts_tab():
    at = _app()
    btn = next(b for b in at.button if b.label == "Compare with the strategies")
    btn.click().run()
    assert not at.exception
    ok = [s.value for s in at.success if s.value.startswith("You paid")]
    assert ok and "2-year ladder would have cost €6.60m" in ok[0]  # default company = the backtest's numbers
    at.radio(key="lang").set_value("NL").run()
    assert not at.exception and any(s.value.startswith("U betaalde") for s in at.success)


def test_green_investments_section():
    at = _app()
    assert any("Choose rooftop solar" in i.value for i in at.info)
    at.sidebar.number_input(key="g_solar").set_value(1000).run()
    at.sidebar.slider(key="g_ppa").set_value(40).run()
    assert not at.exception
    md = " ".join(m.value for m in at.markdown)
    assert "+ solar 1 MWp: expected bill" in md and "+ PPA 40%: expected bill" in md
    at.radio(key="lang").set_value("NL").run()
    assert not at.exception and "+ zon 1 MWp: verwachte rekening" in " ".join(m.value for m in at.markdown)


def test_invoice_pdf_route_waits_for_files_and_confirmation():
    at = _app()
    at.toggle(key="interview").set_value(True).run()
    at.radio(key="source").set_value("pdf").run()
    assert not at.exception
    assert any("Fill in step 2 first" in i.value for i in at.info)  # no file yet: nothing is used
    at.radio(key="lang").set_value("NL").run()
    assert not at.exception and at.radio(key="source").value == "pdf"  # the choice survives the language switch
    assert "Facturen of contracten (PDF)" in at.radio(key="source").options


def test_plan_tracker_tab(tmp_path, monkeypatch):
    at = _app()
    assert [t.label for t in at.tabs] == ["Today's risk", "Your past contracts", "Plan tracker"]
    assert any("Plan for 2027: target 55%" in m.value for m in at.markdown)
    at.radio(key="lang").set_value("NL").run()
    assert not at.exception and any("Plan voor 2027: doel 55%" in m.value for m in at.markdown)
