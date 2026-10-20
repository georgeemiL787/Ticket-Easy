"""The product has one name for owners and customers: no page, text or label shows "Team A", "Team B" or "Team C"."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from team_b.config import PROJECT_ROOT

REPO = PROJECT_ROOT.parent
NAME = re.compile(r"team[ _-]?[abc]\b", re.IGNORECASE)
VISIBLE = [
    REPO / "dashboard" / "src" / "i18n",
    REPO / "dashboard" / "index.html",
    PROJECT_ROOT / "web" / "chat",
    PROJECT_ROOT / "web" / "inbox",
    PROJECT_ROOT / "web" / "shared",
    PROJECT_ROOT / "data" / "locales",
    REPO / "tool_generation_mcp" / "tool_generation_mcp" / "web" / "templates",
]
# i18n keys and CSS class names may still carry the old names: only the TEXT people read must be clean
KEY_LINE = re.compile(r'^\s*"[\w.]+":\s*"(.*)",?\s*$')


def files() -> list[Path]:
    found: list[Path] = []
    for place in VISIBLE:
        found += (
            [place]
            if place.is_file()
            else [p for p in place.rglob("*") if p.is_file() and p.suffix in {".ts", ".html", ".js", ".css", ".json"}]
        )
    return found


def shown_text(path: Path) -> list[tuple[int, str]]:
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if path.suffix == ".ts":
            match = KEY_LINE.match(line)
            if match:
                rows.append((number, match.group(1)))
        elif path.suffix in {".css"}:
            continue
        else:
            rows.append((number, line))
    return rows


@pytest.mark.parametrize("path", files(), ids=lambda p: str(p.relative_to(REPO)))
def test_no_page_text_names_the_internal_teams(path: Path) -> None:
    bad = [(n, text[:100]) for n, text in shown_text(path) if NAME.search(text)]
    assert not bad, f"{path.relative_to(REPO)}: {bad[:3]}"


def test_the_shared_translator_knows_arabic_and_fills_placeholders() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    script = f"""
      globalThis.window = undefined; globalThis.location = {{ search: '?lang=ar', href: 'http://x/?lang=ar' }};
      const lib = require({json.dumps(str(PROJECT_ROOT / "web" / "shared" / "i18n.js"))});
      const TE = globalThis.TE;
      const wrong = TE.tr('Something went wrong ({{status}}). Please try again.', {{status: 500}});
      console.log(JSON.stringify([TE.lang, TE.tr('Send'), wrong, TE.tr('not translated')]));
    """
    done = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    lang, send, wrong, plain = json.loads(done.stdout)
    assert lang == "ar" and send != "Send" and "500" in wrong and "{" not in wrong and plain == "not translated"
