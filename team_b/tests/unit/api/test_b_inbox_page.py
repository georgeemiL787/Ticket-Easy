"""The inbox page: it is served, it never builds HTML from customer text, and its button rules match the server's."""

import json
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

from team_b.config import PROJECT_ROOT

INBOX = PROJECT_ROOT / "web" / "inbox"
NODE = shutil.which("node")


async def test_the_page_and_its_files_are_served(chat: httpx.AsyncClient) -> None:
    page = await chat.get("/inbox")
    assert page.status_code == 200 and "text/html" in page.headers["content-type"]
    assert "/inbox/static/inbox.js" in page.text and 'id="agent"' in page.text
    for name, kind in (("inbox.js", "javascript"), ("style.css", "css")):
        file = await chat.get(f"/inbox/static/{name}")
        assert file.status_code == 200 and kind in file.headers["content-type"]


def test_customer_text_is_never_put_in_the_page_as_html() -> None:
    source = (INBOX / "inbox.js").read_text(encoding="utf-8")
    for unsafe in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert unsafe not in source, unsafe
    assert source.count('dir: "auto"') >= 5  # Arabic and English text each read in their own direction


def run_node(expression: str) -> object:
    script = f"const m = require({json.dumps(str(INBOX / 'inbox.js'))}); console.log(JSON.stringify({expression}));"
    done = subprocess.run([NODE or "node", "-e", script], capture_output=True, text=True, check=True, timeout=30)
    return json.loads(done.stdout)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_buttons_are_offered_only_when_the_server_would_accept_them() -> None:
    case = {"status": "open", "claimed_by": None, "pending_approval": None}
    claimed = {"status": "claimed", "claimed_by": "sara", "pending_approval": None}
    waiting = {**claimed, "pending_approval": {"capability": "create_refund"}}
    off = {"claim": False, "release": False, "reply": False, "resolve": False, "returnToAgent": False, "decide": False}
    assert run_node(f"m.availableActions({json.dumps(case)}, 'sara')") == {**off, "claim": True}
    assert run_node(f"m.availableActions({json.dumps(case)}, '')") == off  # no name, no buttons
    mine = {"claim": False, "release": True, "reply": True, "resolve": True, "returnToAgent": True, "decide": False}
    assert run_node(f"m.availableActions({json.dumps(claimed)}, 'sara')") == mine
    assert run_node(f"m.availableActions({json.dumps(claimed)}, 'omar')") == off  # someone else's case
    assert run_node(f"m.availableActions({json.dumps(waiting)}, 'sara')") == {**mine, "decide": True}
    done = {"status": "resolved", "claimed_by": "sara", "pending_approval": None}
    assert run_node(f"m.availableActions({json.dumps(done)}, 'sara')") == off


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_small_helpers() -> None:
    assert run_node("m.ageText('2026-09-28T12:00:00Z', Date.parse('2026-09-28T12:00:30Z'))") == "30 s"
    assert run_node("m.ageText('2026-09-28T12:00:00Z', Date.parse('2026-09-28T12:10:00Z'))") == "10 min"
    assert run_node("m.ageText('2026-09-28T12:00:00Z', Date.parse('2026-09-28T15:00:00Z'))") == "3 h"
    assert run_node("m.ageText('2026-09-28T12:00:00Z', Date.parse('2026-10-01T12:00:00Z'))") == "3 d"
    body = {"error": {"code": "INVALID_STATE", "message": "the case is claimed by sara"}}
    assert run_node(f"m.errorMessage(409, {json.dumps(body)})") == "the case is claimed by sara (INVALID_STATE)"
    assert run_node("m.errorMessage(500, null)") == "Something went wrong (HTTP 500)."
    ai = {"summary": "template", "summary_source": "ai", "ai_summary": "written"}
    assert run_node(f"m.summaryToShow({json.dumps(ai)})") == {"text": "written", "ai": True}
    assert run_node("m.summaryToShow({summary: 't', summary_source: 'template', ai_summary: null})") == {
        "text": "t",
        "ai": False,
    }


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_javascript_has_no_syntax_errors() -> None:
    done = subprocess.run([NODE or "node", "--check", str(INBOX / "inbox.js")], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_the_reason_filter_lists_every_escalation_reason() -> None:
    from team_b.domain.decision import EscalationReason

    source = (INBOX / "inbox.js").read_text(encoding="utf-8")
    assert all(f'"{reason.value}"' in source for reason in EscalationReason)
    assert Path(INBOX / "index.html").read_text(encoding="utf-8").count("<script") == 1
