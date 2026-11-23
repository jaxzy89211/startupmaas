"""The pre-commit hook blocks confidential and licensed files, even when force-added."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _git(d, *args, check=True):
    return subprocess.run(["git", *args], cwd=d, capture_output=True, text=True, check=check)


@pytest.mark.parametrize(
    "path, blocked",
    [
        ("data/private/acme/bill.pdf", True),
        ("clients/private/acme.yaml", True),
        ("data/forwards.csv", True),
        ("output/history/acme.csv", True),
        ("x.bundle", True),
        ("README.md", False),
        ("data/company_example.csv", False),
    ],
)
def test_hook(tmp_path, path, blocked):
    if shutil.which("git") is None:
        pytest.skip("no git")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.invalid")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "hooks").mkdir()
    shutil.copy(ROOT / "hooks" / "pre-commit", tmp_path / "hooks" / "pre-commit")
    _git(tmp_path, "config", "core.hooksPath", "hooks")
    f = tmp_path / path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("x")
    _git(tmp_path, "add", "-f", path)
    r = _git(tmp_path, "commit", "-q", "-m", "t", check=False)
    assert (r.returncode != 0) == blocked, r.stderr
    if blocked:
        assert "BLOCKED" in r.stderr


def test_gitignore_covers_private_data():
    for p in ("data/private/x.pdf", "clients/private/x.yaml", "clients/acme.yaml", "data/forwards.csv"):
        assert subprocess.run(["git", "check-ignore", "-q", p], cwd=ROOT).returncode == 0, p
