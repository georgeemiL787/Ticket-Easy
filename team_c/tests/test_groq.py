"""Groq provider and the ordered multi-step provider chain (LLM_FALLBACK as a comma-separated list).

MOCKED: every provider answers through an httpx MockTransport; all data lives in per-test databases.
"""
import json
import httpx
import pytest
from team_c.config import AppError, Settings
from team_c.llm.groq import GROQ_MAX_TOKENS
from team_c.llm.schemas import output_schema, groq_schema
from team_c.models import GenerationOutput
from team_c.providers import LIVE, Providers
from team_c.storage import Store, now, uid

CONTENT = json.dumps(dict(proposals=[], capability_gaps=[]))
HOSTS = {"api.groq.com": "groq", "openrouter.ai": "openrouter", "127.0.0.1": "ollama"}


def settings(tmp_path, **kwargs):
    values = dict(database_path=str(tmp_path / "g.db"), llm_primary="groq", llm_fallback="ollama",
                  groq_api_key="test-groq-key", openrouter_api_key="test-or-key", openrouter_model="test/or-model")
    return Settings(_env_file=None, **{**values, **kwargs})


def call(tmp_path, answers, job=None, **kwargs):
    """answers maps provider name to handler(request, body); returns (result, error, sent, attempts, store, run)."""
    s = settings(tmp_path, **kwargs)
    store = Store(s.database_path)
    bid = uid()
    with store.connect(write=True) as c:
        c.execute("INSERT INTO businesses VALUES(?,?,?,?)", (bid, "B", "B", now()))
    sent = []
    def handler(request):
        provider = HOSTS[request.url.host]
        sent.append(provider)
        return answers[provider](request, json.loads(request.content))
    run = store.start_run(bid, "generation", {})
    LIVE.job = job
    try:
        result, error = Providers(s, store, httpx.MockTransport(handler)).call("generation", {}, GenerationOutput, run), None
    except AppError as exc:
        result, error = None, exc
    finally:
        LIVE.job = None
    attempts = [(r["provider"], r["status"], json.loads(r["error"])["code"] if r["error"] else None)
                for r in store.all("SELECT provider,status,error FROM attempts WHERE run_id=? ORDER BY id", (run,))]
    return result, error, sent, attempts, store, run


def chat_ok(request, body):
    return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": CONTENT}}], "usage": {"prompt_tokens": 50, "completion_tokens": 9}})


def ollama_ok(request, body):
    return httpx.Response(200, content=json.dumps({"message": {"content": CONTENT}, "done": True, "done_reason": "stop"}).encode())


def status(code):
    return lambda request, body: httpx.Response(code)


def untouched(request, body):
    pytest.fail("must not contact this provider")


def test_groq_request_shape_and_usage(tmp_path):
    seen = []
    def groq(request, body):
        seen.append((str(request.url), request.headers["authorization"], body))
        return chat_ok(request, body)
    result, error, sent, attempts, store, run = call(tmp_path, dict(groq=groq, ollama=untouched))
    assert error is None and result.proposals == []
    url, auth, body = seen[0]
    assert url == "https://api.groq.com/openai/v1/chat/completions" and auth == "Bearer test-groq-key"
    schema, _ = output_schema("generation", {}, GenerationOutput)
    assert body["response_format"] == {"type": "json_schema", "json_schema": {"name": "team_c", "strict": True, "schema": groq_schema(schema)}}
    assert body["model"] == "openai/gpt-oss-120b" and body["temperature"] == 0 and body["max_tokens"] == GROQ_MAX_TOKENS and body["stream"] is False
    assert body["reasoning_effort"] == "low" and body["include_reasoning"] is False and "provider" not in body
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    usage = [json.loads(r["payload"]) for r in store.all("SELECT payload FROM diagnostics WHERE run_id=? AND stage='model_response'", (run,))]
    assert usage[0]["provider"] == "groq" and usage[0]["usage"] == {"prompt_tokens": 50, "completion_tokens": 9}
    assert "test-groq-key" not in json.dumps(store.all("SELECT * FROM diagnostics")) + json.dumps(store.all("SELECT * FROM attempts"))


def test_empty_reasoning_effort_omits_reasoning_parameters(tmp_path):
    bodies = []
    def groq(request, body):
        bodies.append(body)
        return chat_ok(request, body)
    call(tmp_path, dict(groq=groq), groq_reasoning_effort="", groq_model="other/model", llm_fallback="none")
    assert bodies[0]["model"] == "other/model" and "reasoning_effort" not in bodies[0] and "include_reasoning" not in bodies[0]


def test_missing_groq_key_is_a_configuration_error_before_any_request(tmp_path):
    result, error, sent, attempts, *_ = call(tmp_path, dict(groq=untouched, ollama=untouched), groq_api_key="")
    assert error.code == "model_configuration" and error.status == 503 and error.message == "Missing Groq API key"
    assert sent == [] and attempts == []


def openrouter_ok(request, body):
    return chat_ok(request, body)


def test_groq_then_openrouter_then_ollama(tmp_path):
    result, error, sent, attempts, *_ = call(tmp_path, dict(groq=status(429), openrouter=status(503), ollama=ollama_ok),
                                            llm_fallback="openrouter,ollama")
    assert error is None and result.proposals == []
    assert sent == ["groq", "openrouter", "ollama"]
    assert attempts == [("groq", "failed", "provider_service_failure"),
                        ("openrouter", "failed", "provider_service_failure"),
                        ("ollama", "succeeded", None)]


def test_openrouter_success_skips_local(tmp_path):
    result, error, sent, attempts, *_ = call(tmp_path, dict(groq=status(503), openrouter=openrouter_ok, ollama=untouched),
                                            llm_fallback="openrouter,ollama")
    assert error is None and sent == ["groq", "openrouter"]
    assert attempts == [("groq", "failed", "provider_service_failure"), ("openrouter", "succeeded", None)]


def test_openrouter_request_shape_uses_full_schema(tmp_path):
    seen = []
    def openrouter(request, body):
        seen.append((str(request.url), request.headers["authorization"], body))
        return chat_ok(request, body)
    call(tmp_path, dict(groq=status(429), openrouter=openrouter, ollama=untouched), llm_fallback="openrouter,ollama")
    url, auth, body = seen[0]
    schema, _ = output_schema("generation", {}, GenerationOutput)
    assert url == "https://openrouter.ai/api/v1/chat/completions" and auth == "Bearer test-or-key"
    assert body["response_format"] == {"type": "json_schema", "json_schema": {"name": "team_c", "strict": True, "schema": schema}}
    assert body["provider"] == {"require_parameters": True, "allow_fallbacks": False}
    assert "reasoning_effort" not in body


def test_groq_falls_through_to_local_on_service_failure(tmp_path):
    result, error, sent, attempts, *_ = call(tmp_path, dict(groq=status(429), ollama=ollama_ok))
    assert error is None and result.proposals == []
    assert sent == ["groq", "ollama"]
    assert attempts == [("groq", "failed", "provider_service_failure"), ("ollama", "succeeded", None)]


def test_every_attempt_is_listed_when_the_whole_chain_fails(tmp_path):
    def offline(request, body):
        raise httpx.ConnectError("offline", request=request)
    result, error, sent, attempts, *_ = call(tmp_path, dict(groq=status(500), ollama=offline))
    assert error.code == "providers_failed" and [a["provider"] for a in error.details["attempts"]] == ["groq", "ollama"]
    assert {a["code"] for a in error.details["attempts"]} == {"provider_service_failure"}
    assert [a[:2] for a in attempts] == [("groq", "failed"), ("ollama", "failed")]


def test_groq_success_never_touches_the_fallbacks(tmp_path):
    result, error, sent, attempts, *_ = call(tmp_path, dict(groq=chat_ok, ollama=untouched))
    assert error is None and sent == ["groq"] and attempts == [("groq", "succeeded", None)]


@pytest.mark.parametrize("answer,code", [
    (lambda r, b: httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": "not json"}}]}), "invalid_model_output"),
    (lambda r, b: httpx.Response(200, json={"choices": [{"finish_reason": "length", "message": {"content": CONTENT}}]}), "invalid_model_output"),
    (lambda r, b: httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": CONTENT, "refusal": "no"}}]}), "invalid_model_output"),
    (status(400), "provider_request_failure"),
    (status(401), "provider_request_failure"),
    (status(413), "provider_request_failure"),
])
def test_groq_invalid_output_or_rejected_request_stops_the_chain(tmp_path, answer, code):
    result, error, sent, attempts, *_ = call(tmp_path, dict(groq=answer, ollama=untouched))
    assert error.code == code and sent == ["groq"] and attempts == [("groq", "failed", code)]


def test_cancelled_job_stops_before_the_groq_request_and_any_fallback(tmp_path):
    result, error, sent, attempts, *_ = call(tmp_path, dict(groq=untouched, ollama=untouched), job=dict(steps=[], cancelled=True))
    assert error.code == "cancelled" and error.status == 409
    assert sent == [] and attempts == [("groq", "failed", "cancelled")]


@pytest.mark.parametrize("fallback,chain", [
    ("none", ["groq"]),
    ("ollama", ["groq", "ollama"]),
    (" ollama ", ["groq", "ollama"]),
    ("openrouter,ollama", ["groq", "openrouter", "ollama"]),
    (" openrouter , ollama ", ["groq", "openrouter", "ollama"]),
])
def test_fallback_list_parsing(tmp_path, fallback, chain):
    s = settings(tmp_path, llm_fallback=fallback)
    assert s.llm_chain == chain
    assert Providers(s, Store(s.database_path)).configured_chain() == chain


@pytest.mark.parametrize("fallback", ["ollama,ollama", "ollama,groq", "none,ollama", "ollama,", "unknown"])
def test_duplicate_or_unsupported_providers_are_rejected(tmp_path, fallback):
    s = settings(tmp_path, llm_fallback=fallback)
    with pytest.raises(AppError) as error:
        Providers(s, Store(s.database_path)).configured_chain()
    assert error.value.code == "model_configuration" and error.value.status == 503


BACKUP_KEYS = dict(groq_api_key_2="test-groq-second", groq_api_key_3="test-groq-third", groq_api_key_4="test-groq-fourth")
KEYS = ["test-groq-key", *BACKUP_KEYS.values()]


@pytest.mark.parametrize("failure", [429, 500, 502, 503, 504, "timeout", "unreachable"])
@pytest.mark.parametrize("successful_slot", [1, 2, 3, 4])
def test_keys_rotate_only_until_one_succeeds(tmp_path, failure, successful_slot):
    seen = []
    def groq(request, body):
        seen.append(request.headers["authorization"].removeprefix("Bearer "))
        if len(seen) == successful_slot:
            return chat_ok(request, body)
        if failure == "timeout":
            raise httpx.ReadTimeout("TEST-ONLY timeout", request=request)
        if failure == "unreachable":
            raise httpx.ConnectError("TEST-ONLY offline", request=request)
        return httpx.Response(failure)
    result, error, sent, attempts, *_ = call(tmp_path, dict(groq=groq, ollama=untouched), **BACKUP_KEYS)
    assert error is None and result.proposals == []
    assert seen == KEYS[:successful_slot] and sent == ["groq"] * successful_slot
    assert [a[1] for a in attempts] == ["failed"] * (successful_slot - 1) + ["succeeded"]


@pytest.mark.parametrize("last_provider", ["ollama", "none"])
def test_all_groq_keys_precede_local(tmp_path, last_provider):
    seen = []
    def groq(request, body):
        seen.append(request.headers["authorization"].removeprefix("Bearer "))
        return httpx.Response(429)
    answers = dict(groq=groq, ollama=ollama_ok if last_provider == "ollama" else status(503))
    result, error, sent, attempts, *_ = call(tmp_path, answers, **BACKUP_KEYS)
    expected = ["groq"] * len(KEYS) + ["ollama"]
    assert seen == KEYS and sent == expected
    assert [a[0] for a in attempts] == expected
    if last_provider == "none":
        assert error.code == "providers_failed"
        assert [a["provider"] for a in error.details["attempts"]] == expected
    else:
        assert error is None and result.proposals == []


@pytest.mark.parametrize("answer,code", [
    (status(400), "provider_request_failure"),
    (status(401), "provider_request_failure"),
    (status(403), "provider_request_failure"),
    (status(413), "provider_request_failure"),
    (lambda r, b: httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": "not json"}}]}), "invalid_model_output"),
    (lambda r, b: httpx.Response(200, json={"choices": [{"finish_reason": "length", "message": {"content": CONTENT}}]}), "invalid_model_output"),
    (lambda r, b: httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": CONTENT, "refusal": "no"}}]}), "invalid_model_output"),
])
def test_nonservice_failures_stop_without_trying_another_key(tmp_path, answer, code):
    seen = []
    def groq(request, body):
        seen.append(request.headers["authorization"])
        return httpx.Response(429) if len(seen) == 1 else answer(request, body)
    _, error, sent, attempts, *_ = call(tmp_path, dict(groq=groq, ollama=untouched), **BACKUP_KEYS)
    assert error.code == code and sent == ["groq", "groq"]
    assert attempts[-1] == ("groq", "failed", code)


def test_cancelling_during_rotation_stops_remaining_keys_and_providers(tmp_path):
    job = dict(steps=[])
    def groq(request, body):
        if request.headers["authorization"] == "Bearer " + KEYS[1]:
            job["cancelled"] = True
        return httpx.Response(429)
    _, error, sent, attempts, *_ = call(tmp_path, dict(groq=groq, ollama=untouched), job=job, **BACKUP_KEYS)
    assert error.code == "cancelled" and sent == ["groq", "groq"]
    assert attempts[-1] == ("groq", "failed", "cancelled")
    assert all(step["finished"] is not None for step in job["steps"])


def test_previously_rate_limited_key_is_retried_on_next_call(tmp_path):
    s = settings(tmp_path, **BACKUP_KEYS)
    store = Store(s.database_path)
    bid = uid()
    with store.connect(write=True) as c:
        c.execute("INSERT INTO businesses VALUES(?,?,?,?)", (bid, "B", "B", now()))
    seen = []
    def handler(request):
        seen.append(request.headers["authorization"].removeprefix("Bearer "))
        # The first call exhausts every Groq key; a later call finds the quota available again.
        return httpx.Response(429) if len(seen) <= len(KEYS) else chat_ok(request, {})
    s.llm_fallback = "none"
    providers = Providers(s, store, httpx.MockTransport(handler))
    with pytest.raises(AppError, match="All selected provider attempts failed"):
        providers.call("generation", {}, GenerationOutput, store.start_run(bid, "generation", {}))
    result = providers.call("generation", {}, GenerationOutput, store.start_run(bid, "generation", {}))
    assert result.proposals == [] and seen == KEYS + KEYS[:1]
    assert s.groq_api_keys == KEYS


def test_blank_and_duplicate_keys_are_skipped(tmp_path):
    seen = []
    def groq(request, body):
        seen.append(request.headers["authorization"])
        return httpx.Response(429)
    _, error, sent, *_ = call(tmp_path, dict(groq=groq), llm_fallback="none", groq_api_key_2="  ", groq_api_key_3=" test-groq-key ", groq_api_key_4=" test-groq-key ")
    assert error.code == "providers_failed" and sent == ["groq"] and len(seen) == 1


def test_all_keys_are_redacted_from_response_diagnostics(tmp_path):
    def groq(request, body):
        content = json.dumps(dict(proposals=[dict(steps=[dict(id=key) for key in KEYS])]))
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": content}}]})
    _, error, _, _, store, _ = call(tmp_path, dict(groq=groq), **BACKUP_KEYS)
    assert error.code == "invalid_model_output"
    recorded = json.dumps(store.all("SELECT * FROM diagnostics") + store.all("SELECT * FROM attempts"))
    assert all(key not in recorded for key in KEYS)
    assert "[redacted]" in recorded


def test_groq_413_with_explicit_rate_limit_code_rotates_all_keys_then_falls_back(tmp_path):
    seen = []
    def groq(request, body):
        seen.append(request.headers["authorization"].removeprefix("Bearer "))
        return httpx.Response(413, json={"error": {"type": "tokens", "code": "rate_limit_exceeded",
                                                  "message": "Request exceeds tokens per minute allowance"}})
    result, error, sent, attempts, *_ = call(tmp_path, dict(groq=groq, ollama=ollama_ok), **BACKUP_KEYS)
    assert result.proposals == [] and error is None
    assert seen == KEYS and sent == ["groq"] * len(KEYS) + ["ollama"]
    assert all(a[2] == "provider_service_failure" for a in attempts[:len(KEYS)])


@pytest.mark.parametrize("body", [None, [], {"error": "rate_limit_exceeded"}, {"error": {"code": "payload_too_large"}},
                                 {"error": {"message": "rate_limit_exceeded"}}])
def test_ordinary_413_does_not_become_a_rate_limit_from_ambiguous_text(tmp_path, body):
    _, error, sent, *_ = call(tmp_path, dict(groq=lambda r, b: httpx.Response(413, json=body)), **BACKUP_KEYS)
    assert error.code == "provider_request_failure" and sent == ["groq"]


def test_token_limit_error_reports_counts_without_provider_secrets(tmp_path):
    response = lambda r, b: httpx.Response(413, json={"error": {"code": "rate_limit_exceeded",
        "message": "Request too large for private-org test-groq-key. Limit 8000, Requested 11932."}})
    _, error, _, attempts, store, _ = call(tmp_path, dict(groq=response), llm_fallback="none")
    message = error.details["attempts"][0]["message"]
    assert "11932 requested, 8000 allowed" in message
    assert "private-org" not in message and "test-groq-key" not in message
    assert "11932 requested" in store.one("SELECT error FROM attempts")["error"]
