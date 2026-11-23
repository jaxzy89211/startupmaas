"""run_with_forwards: the comparison, the >5% flags, the RESULTS.md section, and the guards (no real run here:
the full chain with data/forwards_example.csv is run by hand, see FORWARD_SOURCES.md / README)."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from hedgewise import forwards_run as fr  # noqa: E402


def test_compare_flags_moves_above_5_percent():
    t = fr.compare({"a": 100.0, "b": 100.0, "c": 100.0}, {"a": 104.9, "b": 94.0, "c": None})
    assert list(t.flag) == ["", "⚠️ >5%", ""] and t.change.iloc[1] == pytest.approx(-0.06)


def test_section_replaces_only_its_own_block(tmp_path):
    res = tmp_path / "RESULTS.md"
    res.write_text("# Results\\n\\nFrozen text stays.\\n")
    fwd = tmp_path / "f.csv"
    fwd.write_text("x")
    t = fr.compare({"a": 1.0}, {"a": 2.0})
    fr.write_section(fr.section(t, fwd, "2026-11-23", "TTF Cal-27"), res)
    fr.write_section(fr.section(fr.compare({"a": 1.0}, {"a": 1.0}), fwd, "2026-11-24", "TTF Cal-27"), res)
    s = res.read_text()
    assert s.startswith("# Results\\n\\nFrozen text stays.") and s.count(fr.START) == 1 and "run 2026-11-24" in s
    assert "run 2026-11-23" not in s


def test_frozen_numbers_come_from_the_tag():
    nums = fr.tracked_numbers(fr._today_alias(fr._read_frozen, "2026-11-23"))
    assert nums["backtest variable: total (EUR k)"] == pytest.approx(6403.4, abs=0.1)
    assert nums["today: ladder share (%)"] == 50 and nums["today: bad case all variable (EUR k)"] == pytest.approx(
        5317.0, abs=1
    )


def test_guards(tmp_path, capsys):
    assert fr.main(["--forwards", str(tmp_path / "missing.csv")]) is None
    assert "nothing to do" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="FAKE"):
        fr.main(["--forwards", str(ROOT / "data" / "forwards_example.csv")])
