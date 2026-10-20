"""No old name comes back: the repository-wide check (ops/check_names.py) finds nothing in any tracked file or name."""

import subprocess
import sys
from pathlib import Path

from agent_service.config import PROJECT_ROOT

REPO = PROJECT_ROOT.parent.parent
SCRIPT = REPO / "ops" / "check_names.py"


def run(*paths: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *paths], cwd=REPO, capture_output=True, text=True, encoding="utf-8"
    )


def test_the_repository_has_no_old_names() -> None:
    result = run()
    assert result.returncode == 0, result.stdout[-3000:]


def test_the_check_fails_when_an_old_name_is_written(tmp_path: Path) -> None:
    bad = REPO / "ops" / "_names_probe.txt"
    bad.write_text("Team " + "B owns this\n", encoding="utf-8")
    try:
        result = run("ops/_names_probe.txt")
    finally:
        bad.unlink()
    assert result.returncode == 1 and "_names_probe.txt:1" in result.stdout


def test_shop_staff_phrases_are_allowed_but_other_uses_are_not(tmp_path: Path) -> None:
    probe = REPO / "ops" / "_names_probe.txt"
    probe.write_text("Our customer care " + "team will call you. Ask our " + "team.\n", encoding="utf-8")
    try:
        allowed = run("ops/_names_probe.txt")
        probe.write_text("The billing " + "team decides.\n", encoding="utf-8")
        refused = run("ops/_names_probe.txt")
    finally:
        probe.unlink()
    assert allowed.returncode == 0 and refused.returncode == 1
