"""An Ollama fallback's context budget applies only when Ollama is attempted; it never blocks the Groq primary."""
import json
import httpx
from team_c.config import AppError, Settings
from team_c.models import GenerationOutput
from team_c.providers import GENERATION_NUM_PREDICT, OLLAMA_MARGIN_TOKENS, Providers
from team_c.storage import Store, now, uid

# Far above the byte bound of a small Ollama context, so reaching Ollama requires a token count.
PAYLOAD = dict(business=dict(description="TEST-ONLY " * 2000))
CONTEXT = GENERATION_NUM_PREDICT + OLLAMA_MARGIN_TOKENS + 1000
CONTENT = json.dumps(dict(proposals=[], capability_gaps=[]))


def call(tmp_path, groq, ollama):
    settings = Settings(_env_file=None, database_path=str(tmp_path / "f.db"), llm_primary="groq", llm_fallback="ollama",
                        groq_model="test/model", groq_api_key="test-key", ollama_context=CONTEXT)
    store = Store(settings.database_path)
    bid = uid()
    with store.connect(write=True) as c:
        c.execute("INSERT INTO businesses VALUES(?,?,?,?)", (bid, "B", "B", now()))
    sent = []
    def handler(request):
        provider = "ollama" if request.url.host == "127.0.0.1" else "groq"
        sent.append((provider, json.loads(request.content)))
        return (groq if provider == "groq" else ollama)(request, sent[-1][1])
    run = store.start_run(bid, "generation", {})
    try:
        result, error = Providers(settings, store, httpx.MockTransport(handler)).call("generation", PAYLOAD, GenerationOutput, run), None
    except AppError as exc:
        result, error = None, exc
    stages = [r["stage"] for r in store.all("SELECT stage FROM diagnostics WHERE run_id=?", (run,))]
    attempts = [(r["provider"], r["status"]) for r in store.all("SELECT provider,status FROM attempts WHERE run_id=? ORDER BY id", (run,))]
    return result, error, sent, stages, attempts


def groq_ok(request, body):
    return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": CONTENT}}]})


def groq_down(request, body):
    return httpx.Response(503)


def ollama_offline(request, body):
    raise httpx.ConnectError("offline", request=request)


def ollama_counting(measured):
    def handler(request, body):
        if body["options"]["num_predict"] == 1:
            return httpx.Response(200, json={"message": {"content": ""}, "done": True, "done_reason": "length", "prompt_eval_count": measured, "eval_count": 1})
        return httpx.Response(200, content=json.dumps({"message": {"content": CONTENT}, "done": True, "done_reason": "stop"}).encode())
    return handler


def test_offline_ollama_fallback_cannot_block_a_successful_groq_primary(tmp_path):
    result, error, sent, stages, attempts = call(tmp_path, groq_ok, ollama_offline)
    assert error is None and result.proposals == []
    assert [p for p, _ in sent] == ["groq"] and "context_budget" not in stages and attempts == [("groq", "succeeded")]


def test_ollama_budget_is_still_enforced_when_the_fallback_is_needed(tmp_path):
    limit = CONTEXT - GENERATION_NUM_PREDICT - OLLAMA_MARGIN_TOKENS
    result, error, sent, stages, attempts = call(tmp_path, groq_down, ollama_counting(limit + 1))
    assert error.code == "model_input_limit" and "counted by Ollama" in error.message and error.details["context_budget"]["measured_input_tokens"] == limit + 1
    assert [p for p, _ in sent] == ["groq", "ollama"] and sent[1][1]["options"]["num_predict"] == 1 and "context_budget" in stages
    assert attempts == [("groq", "failed"), ("ollama", "failed")]
    # A real count that fits lets the fallback answer with the full output reserve.
    result, error, sent, stages, attempts = call(tmp_path / "fits", groq_down, ollama_counting(limit))
    assert error is None and [p for p, _ in sent] == ["groq", "ollama", "ollama"] and sent[2][1]["options"]["num_predict"] == GENERATION_NUM_PREDICT
    assert attempts == [("groq", "failed"), ("ollama", "succeeded")]


def test_unreachable_fallback_after_a_primary_service_failure_reports_both_attempts(tmp_path):
    result, error, sent, stages, attempts = call(tmp_path, groq_down, ollama_offline)
    assert error.code == "providers_failed" and [a["provider"] for a in error.details["attempts"]] == ["groq", "ollama"]
    assert attempts == [("groq", "failed"), ("ollama", "failed")]
