"""Live progress for AI actions: thinking is streamed to the page while the action runs and is never stored.

MOCKED / AUTHORED: Ollama responses come from an httpx MockTransport and suggestions from an AUTHORED TEST
SUBSTITUTE; all data lives in per-test databases. No live model is called.
"""
import json
import re
import threading
import time
import httpx
import pytest
from team_c.config import AppError, Settings
from team_c.models import SuggestionOutput
from team_c.providers import LIVE, Providers, check_cancelled, live_step
from team_c.storage import Store, now, uid
from test_second_domain import make_desk
from test_tool_requests import RequestDesk

THOUGHT = "TEST-ONLY thinking: which operations help customers?"


ANSWER = json.dumps(dict(suggestions=[]))
STOPPED = {"message": {"content": ""}, "done": True, "done_reason": "stop", "prompt_eval_count": 1, "eval_count": 4}


def provider_call(tmp_path, streams, job):
    """Run one suggestion call against scripted Ollama NDJSON streams (one per request) with a live job."""
    settings = Settings(_env_file=None, database_path=str(tmp_path / "b.db"), llm_fallback="none")
    store = Store(settings.database_path)
    bid = uid()
    with store.connect(write=True) as c:
        c.execute("INSERT INTO businesses VALUES(?,?,?,?)", (bid, "B", "B", now()))
    sent = []
    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, content="\n".join(json.dumps(c) for c in streams[len(sent) - 1]).encode())
    run = store.start_run(bid, "suggestion", {})
    LIVE.job = job
    try:
        result = Providers(settings, store, httpx.MockTransport(handler)).call("suggestion", dict(business=dict(name="B")), SuggestionOutput, run)
    except AppError as exc:
        result = exc
    finally:
        LIVE.job = None
    return result, sent, store, run


def test_ollama_stream_sends_thinking_to_the_live_step_only(tmp_path):
    chunks = [{"message": {"thinking": THOUGHT[:20], "content": ""}, "done": False}, {"message": {"thinking": THOUGHT[20:], "content": ""}, "done": False},
              {"message": {"content": ANSWER[:8]}, "done": False}, {"message": {"content": ANSWER[8:]}, "done": False}, STOPPED]
    job = dict(steps=[])
    result, sent, store, run = provider_call(tmp_path, [chunks], job)
    assert result.suggestions == [] and sent[0]["stream"] is True and sent[0]["think"] is True
    step = job["steps"][0]
    assert step["thinking"] == THOUGHT and step["answer_tokens"] == 2 and step["finished"] and step["thinking_on"]
    stored = [r["payload"] for r in store.all("SELECT payload FROM diagnostics WHERE run_id=?", (run,))]
    assert stored and not any("TEST-ONLY thinking" in p for p in stored)


def test_thinking_that_runs_out_of_room_is_redone_once_without_thinking(tmp_path):
    cut = [{"message": {"thinking": THOUGHT, "content": ""}, "done": False}, {"message": {"content": ""}, "done": True, "done_reason": "length", "eval_count": 8192}]
    job = dict(steps=[])
    result, sent, store, run = provider_call(tmp_path, [cut, [{"message": {"content": ANSWER}, "done": False}, STOPPED]], job)
    assert result.suggestions == [] and [s["think"] for s in sent] == [True, False] and sent[0]["messages"] == sent[1]["messages"]
    assert [(s["thinking_on"], s["retry"]) for s in job["steps"]] == [(True, False), (False, True)]
    retry = store.all("SELECT payload FROM diagnostics WHERE run_id=? AND stage='thinking_retry'", (run,))
    assert len(retry) == 1 and json.loads(retry[0]["payload"]) == dict(eval_count=8192, reason="output limit reached while thinking")
    assert [a["status"] for a in store.all("SELECT status FROM attempts WHERE run_id=?", (run,))] == ["succeeded"]
    # A cut-off answer without thinking is not retried again.
    again, sent, _, _ = provider_call(tmp_path / "again", [cut, cut], dict(steps=[]))
    assert isinstance(again, AppError) and again.code == "invalid_model_output" and len(sent) == 2


def test_a_stopped_job_ends_the_model_call_without_fallback(tmp_path):
    result, sent, store, run = provider_call(tmp_path, [[STOPPED]], dict(steps=[], cancelled=True))
    assert isinstance(result, AppError) and result.code == "cancelled" and sent == []
    attempts = store.all("SELECT status,error FROM attempts WHERE run_id=?", (run,))
    assert len(attempts) == 1 and attempts[0]["status"] == "failed" and json.loads(attempts[0]["error"])["code"] == "cancelled"


class SlowDesk(RequestDesk):
    """AUTHORED TEST SUBSTITUTE: a suggestion run that thinks, then waits until the test releases it."""
    gate = None

    def call(self, kind, payload, output_model, run):
        if kind == "suggestion":
            step = live_step(kind, "TEST_SUBSTITUTE", True)
            step["thinking"] += THOUGHT
            SlowDesk.gate.wait(10)
            step["finished"] = time.time()
            check_cancelled()
        return super().call(kind, payload, output_model, run)


@pytest.fixture
def desk(tmp_path, monkeypatch):
    monkeypatch.setattr("test_second_domain.DeskSubstitute", SlowDesk)
    monkeypatch.setattr("team_c.web.LIVE_WAIT_SECONDS", 0.2)
    SlowDesk.gate = threading.Event()
    d = make_desk(tmp_path, "base")
    d.service = d.app.state.service
    d.bid = d.service.spec(d.spec["id"])["business_id"]
    yield d
    SlowDesk.gate.set()
    d.client.__exit__(None, None, None)


def poll(d, jid, until):
    for _ in range(100):
        job = d.client.get(f"/api/v1/live/{jid}").json()
        if until(job):
            return job
        time.sleep(0.05)
    raise AssertionError(job)


def test_slow_action_moves_to_the_live_page_shows_thinking_then_the_result(desk):
    d = desk
    d.service.providers.ideas.append([])
    page = d.client.get(f"/businesses/{d.bid}")
    csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
    r = d.client.post("/actions/suggest", data=dict(csrf=csrf, business_id=d.bid), headers={"referer": f"http://testserver/businesses/{d.bid}"})
    assert r.url.path.startswith("/live/") and "Looking for useful tools" in r.text and "not saved" in r.text
    jid = r.url.path.rsplit("/", 1)[1]
    running = poll(d, jid, lambda j: j["steps"] and j["steps"][0]["thinking"])
    assert running["status"] == "running" and running["steps"][0]["thinking"] == THOUGHT and running["steps"][0]["label"] == "Thinking of useful tools"
    assert running["back"] == f"/businesses/{d.bid}"
    d.service.providers.gate.set()
    done = poll(d, jid, lambda j: j["status"] != "running")
    assert done["status"] == "done" and done["url"] == f"/businesses/{d.bid}" and done["steps"][0]["thinking"] == ""
    assert d.client.get("/api/v1/live/unknown").status_code == 404


def test_one_ai_job_at_a_time_with_a_banner_and_a_stop_button(desk):
    d = desk
    d.service.providers.ideas.append([])
    csrf = re.search(r'name="csrf" value="([^"]+)"', d.client.get(f"/businesses/{d.bid}").text).group(1)
    first = d.client.post("/actions/suggest", data=dict(csrf=csrf, business_id=d.bid))
    jid = first.url.path.rsplit("/", 1)[1]
    assert f'action="/live/{jid}/cancel"' in first.text and ">Stop</button>" in first.text and "The AI is busy" not in first.text
    # A second AI action does not start: it shows the running job instead.
    second = d.client.post("/actions/suggest", data=dict(csrf=csrf, business_id=d.bid))
    assert second.url.path == f"/live/{jid}" and second.url.query == b"busy=1" and "Your new action was not started" in second.text
    assert len(d.app.state.jobs) == 1
    business = d.client.get(f"/businesses/{d.bid}").text
    assert "The AI is busy: Looking for useful tools" in business and f'href="/live/{jid}"' in business
    # Accepting a suggestion starts AI work too; dismissing does not and is not held back.
    accept = d.client.post("/actions/decide_suggestion", data=dict(csrf=csrf, suggestion_id="unknown", decision="accept"))
    assert accept.url.path == f"/live/{jid}" and accept.url.query == b"busy=1"
    dismiss = d.client.post("/actions/decide_suggestion", data=dict(csrf=csrf, suggestion_id="unknown", decision="dismiss"))
    assert not dismiss.url.path.startswith("/live/")
    # Stop: the flag is set, the substitute sees it and the job ends as stopped.
    d.client.post(f"/live/{jid}/cancel", data=dict(csrf="wrong"))
    assert not d.app.state.jobs[jid].get("cancelled")
    stopping = d.client.post(f"/live/{jid}/cancel", data=dict(csrf=csrf))
    assert stopping.url.path == f"/live/{jid}" and "Stopping..." in stopping.text and poll(d, jid, lambda j: True)["cancelled"]
    SlowDesk.gate.set()
    stopped = poll(d, jid, lambda j: j["status"] != "running")
    assert stopped["status"] == "failed" and stopped["error"]["code"] == "cancelled"
    page = d.client.get(f"/live/{jid}").text
    assert "Stopped. Nothing from this run was used." in page and ">Stop</button>" not in page
    assert "The AI is busy" not in d.client.get(f"/businesses/{d.bid}").text
    run = d.service.store.all("SELECT status FROM runs WHERE kind='suggestion' ORDER BY rowid DESC LIMIT 1")[0]
    assert run["status"] == "failed"


def test_a_failed_check_lists_what_it_found_on_the_live_page(desk):
    d = desk
    error = AppError("invalid_bindings", "Proposal grounding failed", details={"errors": ["s3:body.category: TEST-ONLY check message"], "run_id": "r1"})
    d.app.state.jobs["j1"] = dict(id="j1", action="reconcile", status="failed", url=None, back="/", started=time.time(), steps=[], error=error)
    job = d.client.get("/api/v1/live/j1").json()
    assert job["error"]["errors"] == ["s3:body.category: TEST-ONLY check message"]
    page = d.client.get("/live/j1").text
    assert "What the check found" in page and "s3:body.category: TEST-ONLY check message" in page


def test_fast_actions_redirect_straight_to_the_result(desk, monkeypatch):
    d = desk
    monkeypatch.setattr("team_c.web.LIVE_WAIT_SECONDS", 5)
    SlowDesk.gate.set()
    d.service.providers.ideas.append([])
    csrf = re.search(r'name="csrf" value="([^"]+)"', d.client.get(f"/businesses/{d.bid}").text).group(1)
    r = d.client.post("/actions/suggest", data=dict(csrf=csrf, business_id=d.bid))
    assert r.url.path == f"/businesses/{d.bid}"
