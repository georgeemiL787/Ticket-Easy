"""The chat and inbox pages keep every element their scripts look up, and the shared look is served.

A safety net for restyling the pages: markup may change, the ids the scripts use may not.
"""

import re
from pathlib import Path

import httpx
import pytest

from agent_service.config import PROJECT_ROOT

WEB = PROJECT_ROOT / "web"
LOOKUP = re.compile(r"""(?:getElementById|\$)\(\s*["']([\w-]+)["']\s*\)""")
CREATED = re.compile(r"""\bid:\s*["']([\w-]+)["']""")  # elements the script builds itself


def ids_the_script_needs(script: Path) -> set[str]:
    source = script.read_text(encoding="utf-8")
    return set(LOOKUP.findall(source)) - set(CREATED.findall(source))


@pytest.mark.parametrize(("path", "script"), [("/chat", "chat/chat.js"), ("/inbox", "inbox/inbox.js")])
async def test_the_page_has_every_element_its_script_looks_up(chat: httpx.AsyncClient, path: str, script: str) -> None:
    page = await chat.get(path)
    assert page.status_code == 200 and "text/html" in page.headers["content-type"]
    needed = ids_the_script_needs(WEB / script)
    assert needed, f"no element lookups found in {script}: update the LOOKUP pattern"
    missing = sorted(name for name in needed if f'id="{name}"' not in page.text)
    assert not missing, f"{path} lost elements {script} needs: {missing}"


async def test_the_shared_look_is_served_and_linked(chat: httpx.AsyncClient) -> None:
    sheet = await chat.get("/ui/aurora-light.css")
    assert sheet.status_code == 200 and "css" in sheet.headers["content-type"]
    for path in ("/chat", "/inbox"):
        page = (await chat.get(path)).text
        assert "/ui/aurora-light.css" in page, path
        assert "/ui/aurora.css" not in page, path  # the old stylesheet is no longer linked
