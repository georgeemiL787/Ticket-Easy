"""Cancellation during a provider attempt ends the call with 409 and never moves on to the fallback.

MOCKED: both providers answer through an httpx MockTransport; all data lives in per-test databases.
"""
import json
import httpx
import pytest
from team_c.config import AppError, Settings
from team_c.models import GenerationOutput
from team_c.providers import LIVE, Providers
from team_c.storage import Store, now, uid

CONTENT = json.dumps(dict(proposals=[], capability_gaps=[]))


def call(tmp_path, primary, fallback, job):
    settings = Settings(_env_file=None, database_path=str(tmp_path / "c.db"), llm_primary=primary, llm_fallback=fallback,
                        openrouter_model="test/model", openrouter_api_key="test-key")
    store = Store(settings.database_path)
    bid = uid()
    with store.connect(write=True) as c:
        c.execute("INSERT INTO businesses VALUES(?,?,?,?)", (bid, "B", "B", now()))
    sent = []
    def handler(request):
        provider = "ollama" if request.url.host == "127.0.0.1" else "openrouter"
        sent.append(provider)
        job["cancelled"] = True  # the owner stops the action while the model is answering
        if provider == "ollama":
            return httpx.Response(200, content=json.dumps({"message": {"content": CONTENT}, "done": True, "done_reason": "stop"}).encode())
        return httpx.Response(503)
    run = store.start_run(bid, "generation", {})
    LIVE.job = job
    try:
        with pytest.raises(AppError) as error:
            Providers(settings, store, httpx.MockTransport(handler)).call("generation", dict(business=dict(name="B")), GenerationOutput, run)
    finally:
        LIVE.job = None
    attempts = [(r["provider"], r["status"], json.loads(r["error"])["code"]) for r in store.all("SELECT provider,status,error FROM attempts WHERE run_id=? ORDER BY id", (run,))]
    return error.value, sent, attempts


def test_cancelling_while_ollama_streams_does_not_try_the_openrouter_fallback(tmp_path):
    error, sent, attempts = call(tmp_path, "ollama", "openrouter", dict(steps=[]))
    assert error.code == "cancelled" and error.status == 409
    assert sent == ["ollama"] and attempts == [("ollama", "failed", "cancelled")]


def test_cancelled_job_stops_an_openrouter_primary_before_any_request_or_fallback(tmp_path):
    error, sent, attempts = call(tmp_path, "openrouter", "ollama", dict(steps=[], cancelled=True))
    assert error.code == "cancelled" and error.status == 409
    assert sent == [] and attempts == [("openrouter", "failed", "cancelled")]
