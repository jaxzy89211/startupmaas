"""Smoke test: the dashboard renders the 'Today' view without errors (Streamlit's headless AppTest)."""

from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "app.py")


def test_today_view_renders():
    at = AppTest.from_file(APP, default_timeout=300).run()
    assert not at.exception
    labels = [m.label for m in at.metric]
    assert "Expected bill, all variable" in labels and "Recommended share to fix" in labels
    assert any("Fixing everything costs" in c.value for c in at.caption)


def test_sector_and_upload_modes():
    at = AppTest.from_file(APP, default_timeout=300).run()
    at.sidebar.selectbox[0].select("offices").run()
    assert not at.exception and any("offices" in w.value for w in at.sidebar.warning)
    at.sidebar.radio[0].set_value("Upload bills (CSV / Excel)").run()
    assert not at.exception and at.sidebar.info


def test_past_contracts_tab():
    at = AppTest.from_file(APP, default_timeout=600).run()
    btn = next(b for b in at.button if b.label == "Compare with the strategies")
    btn.click().run()
    assert not at.exception
    ok = [s.value for s in at.success if s.value.startswith("You paid")]
    assert ok and "2-year ladder would have cost €6.60m" in ok[0]  # default company = the backtest's numbers
    at.radio(key="audit_lang").set_value("NL").run()
    assert not at.exception and any(s.value.startswith("U betaalde") for s in at.success)
