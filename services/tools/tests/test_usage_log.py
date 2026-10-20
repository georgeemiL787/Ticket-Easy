"""The token usage log of the tool service: one line per provider attempt, tokens only. MOCKED: no network."""
import importlib.util
import json
from pathlib import Path
import httpx
import pytest
from tools_service.config import AppError, Settings
from tools_service.models import GenerationOutput
from tools_service.providers import LIVE, Providers
from tools_service.storage import Store, now, uid

CONTENT = json.dumps(dict(proposals=[], capability_gaps=[]))
HOSTS = {"api.groq.com": "groq", "openrouter.ai": "openrouter", "127.0.0.1": "ollama"}
SUMMARY = Path(__file__).resolve().parents[2] / "agent" / "scripts" / "usage_summary.py"
KEYS = dict(groq_api_key="gsk-secret-one", groq_api_key_2="gsk-secret-two")


def run_call(tmp_path, answers, log=True, **kwargs):
    values = dict(database_path=str(tmp_path / "g.db"), llm_primary="groq", llm_fallback="openrouter,ollama", **KEYS,
                  openrouter_api_key="or-secret", openrouter_model="test/or-model")
    if log:
        values.update(usage_log=str(tmp_path / "u.jsonl"), usage_run_label="unit")
    s = Settings(_env_file=None, **{**values, **kwargs})
    store = Store(s.database_path)
    bid = uid()
    with store.connect(write=True) as c:
        c.execute("INSERT INTO businesses VALUES(?,?,?,?)", (bid, "B", "B", now()))
    run = store.start_run(bid, "generation", {})
    LIVE.job = None
    try:
        Providers(s, store, httpx.MockTransport(lambda r: answers[HOSTS[r.url.host]](r))).call("generation", {}, GenerationOutput, run)
    except AppError:
        pass
    return tmp_path / "u.jsonl"


def lines(path):
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]


def chat_ok(usage=None):
    body = {"choices": [{"finish_reason": "stop", "message": {"content": CONTENT}}]}
    if usage is not None:
        body["usage"] = usage
    return lambda request: httpx.Response(200, json=body)


def ollama_ok(request):
    return httpx.Response(200, content=json.dumps({"message": {"content": CONTENT}, "done": True, "done_reason": "stop", "prompt_eval_count": 321, "eval_count": 12}).encode())


def test_a_successful_groq_attempt_has_the_exact_counts_and_the_key_position(tmp_path):
    usage = {"prompt_tokens": 500, "completion_tokens": 40, "completion_tokens_details": {"reasoning_tokens": 7}}
    [line] = lines(run_call(tmp_path, dict(groq=chat_ok(usage))))
    assert (line["service"], line["job"], line["kind"], line["run_label"]) == ("tools", "generation", "chat", "unit")
    assert (line["provider"], line["model"], line["status"], line["http_status"]) == ("api.groq.com", "openai/gpt-oss-120b", "ok", 200)
    assert (line["input_tokens"], line["output_tokens"], line["reasoning_tokens"], line["cached_tokens"]) == (500, 40, 7, None)
    assert line["key_index"] == 0 and line["backup_used"] is False and line["estimated"] is False


def test_a_rate_limited_key_and_the_next_key_are_separate_lines(tmp_path):
    answers = [httpx.Response(429), chat_ok({"prompt_tokens": 5, "completion_tokens": 1})(None)]
    first, second = lines(run_call(tmp_path, dict(groq=lambda r: answers.pop(0))))
    assert (first["status"], first["http_status"], first["key_index"], first["input_tokens"]) == ("rate_limited", 429, 0, None)
    assert (second["status"], second["key_index"], second["input_tokens"]) == ("ok", 1, 5)


def test_a_fallback_to_another_provider_is_its_own_line_and_ollama_counts_are_read(tmp_path):
    got = lines(run_call(tmp_path, dict(groq=lambda r: httpx.Response(503), openrouter=lambda r: httpx.Response(503), ollama=ollama_ok)))
    assert [(x["provider"], x["status"]) for x in got] == [("api.groq.com", "error"), ("api.groq.com", "error"), ("openrouter.ai", "error"), ("127.0.0.1:11434", "ok")]
    assert [x["key_index"] for x in got] == [0, 1, None, None]
    assert (got[-1]["input_tokens"], got[-1]["output_tokens"]) == (321, 12)
    assert all(x["input_tokens"] is None for x in got[:-1])


def test_a_missing_usage_object_is_null_not_zero(tmp_path):
    [line] = lines(run_call(tmp_path, dict(groq=chat_ok(None))))
    assert line["status"] == "ok" and line["input_tokens"] is None and line["output_tokens"] is None


def test_a_timeout_is_written(tmp_path):
    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)

    got = lines(run_call(tmp_path, dict(groq=slow, openrouter=slow, ollama=slow)))
    assert {x["status"] for x in got} == {"timeout"} and all(x["http_status"] is None for x in got)


def test_the_file_never_holds_a_key_or_text(tmp_path):
    path = run_call(tmp_path, dict(groq=chat_ok({"prompt_tokens": 1, "completion_tokens": 1})))
    text = path.read_text(encoding="utf-8")
    assert "gsk-secret" not in text and "or-secret" not in text and "proposals" not in text and "/openai/v1" not in text


def test_nothing_is_written_when_the_log_is_off(tmp_path):
    run_call(tmp_path, dict(groq=chat_ok({"prompt_tokens": 1, "completion_tokens": 1})), log=False)
    assert not (tmp_path / "u.jsonl").exists() and Settings(_env_file=None).usage_log == ""


def test_an_unwritable_log_does_not_break_the_call(tmp_path):
    folder = tmp_path / "folder"
    folder.mkdir()
    run_call(tmp_path, dict(groq=chat_ok({"prompt_tokens": 1, "completion_tokens": 1})), usage_log=str(folder))


def test_the_summary_script_reads_a_tool_line_with_lines_of_the_other_services(tmp_path):
    path = run_call(tmp_path, dict(groq=chat_ok({"prompt_tokens": 100, "completion_tokens": 20})))
    other = tmp_path / "o.jsonl"
    common = {"status": "ok", "kind": "chat", "latency_ms": 10, "estimated": False}
    other.write_text(json.dumps({**common, "service": "conversation", "run_label": "c", "job": "reader", "model": "m", "input_tokens": 5, "output_tokens": 1}) + "\n"
                     + json.dumps({**common, "service": "knowledge", "run_label": "k", "job": "embed_query", "kind": "embedding", "model": "bge-m3", "input_tokens": 4, "output_tokens": None}) + "\n", encoding="utf-8")
    spec = importlib.util.spec_from_file_location("usage_summary", SUMMARY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rows = {r["service"]: r for r in module.summarise(module.read_lines([path, other]))}
    assert set(rows) == {"tools", "conversation", "knowledge"} and rows["tools"]["total_tokens"] == 120
