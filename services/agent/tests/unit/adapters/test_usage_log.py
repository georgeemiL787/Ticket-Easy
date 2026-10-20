"""The token usage log: exact provider counts, nulls instead of guesses, no text and no key, off by default."""

import importlib.util
import json
import os
from pathlib import Path
from typing import Any

import httpx
import pytest

from agent_service.adapters.llm import OpenAICompatibleLLM
from agent_service.adapters.llm_registry import LLMRegistry
from agent_service.adapters.usage_log import UsageLog, provider_host
from agent_service.config import Settings
from agent_service.contracts.errors import UpstreamError

SUMMARY = Path(__file__).resolve().parents[3] / "scripts" / "usage_summary.py"
SECRET_PROMPT = "my card is 4111-1111 please"
SECRET_REPLY = "the reply text xyzzy"
USAGE = {
    "prompt_tokens": 120,
    "completion_tokens": 30,
    "total_tokens": 150,
    "completion_tokens_details": {"reasoning_tokens": 12},
    "prompt_tokens_details": {"cached_tokens": 64},
}


def ok(usage: dict[str, Any] | None = USAGE) -> httpx.Response:
    body: dict[str, Any] = {"choices": [{"message": {"role": "assistant", "content": f'{{"r": "{SECRET_REPLY}"}}'}}]}
    if usage is not None:
        body["usage"] = usage
    return httpx.Response(200, json=body)


def lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(raw) for raw in path.read_text(encoding="utf-8").splitlines()]


def llm(handler: Any, path: Path, **kw: Any) -> OpenAICompatibleLLM:
    options: dict[str, Any] = {
        "base_url": "https://api.groq.com/openai/v1?x=1",
        "model": "m1",
        "api_key": "sk-secret",
        **kw,
    }
    return OpenAICompatibleLLM(transport=httpx.MockTransport(handler), usage_log=UsageLog(path, "run7"), **options)


async def ask(client: OpenAICompatibleLLM) -> dict[str, Any]:
    return await client.complete_json(system="be brief", user=SECRET_PROMPT, schema_hint={"r": "str"})


async def test_one_line_with_the_exact_counts(tmp_path: Path) -> None:
    path = tmp_path / "u.jsonl"
    await ask(llm(lambda r: ok(), path))
    [line] = lines(path)
    assert line["service"] == "conversation" and line["run_label"] == "run7" and line["model"] == "m1"
    assert line["provider"] == "api.groq.com" and line["status"] == "ok" and line["http_status"] == 200
    assert (line["input_tokens"], line["output_tokens"]) == (120, 30)
    assert (line["reasoning_tokens"], line["cached_tokens"]) == (12, 64)
    assert line["key_index"] == 0 and line["backup_used"] is False and line["latency_ms"] >= 0


async def test_a_missing_usage_object_is_null_not_zero(tmp_path: Path) -> None:
    path = tmp_path / "u.jsonl"
    await ask(llm(lambda r: ok(None), path))
    [line] = lines(path)
    assert line["status"] == "ok"
    assert [line[f] for f in ("input_tokens", "output_tokens", "reasoning_tokens", "cached_tokens")] == [None] * 4


async def test_a_rate_limited_call_is_written_with_null_tokens_and_the_next_key_is_logged(tmp_path: Path) -> None:
    path = tmp_path / "u.jsonl"
    answers = [httpx.Response(429, text="slow down"), ok()]
    await ask(llm(lambda r: answers.pop(0), path, api_key="k-one,k-two"))
    first, second = lines(path)
    assert (first["status"], first["http_status"], first["key_index"]) == ("rate_limited", 429, 0)
    assert first["input_tokens"] is None and first["output_tokens"] is None
    assert (second["status"], second["key_index"], second["input_tokens"]) == ("ok", 1, 120)


async def test_a_timeout_and_a_server_error_are_written(tmp_path: Path) -> None:
    path = tmp_path / "u.jsonl"

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(UpstreamError):
        await ask(llm(slow, path))
    with pytest.raises(UpstreamError):
        await ask(llm(lambda r: httpx.Response(500, text="boom"), path))
    first, second = lines(path)
    assert (first["status"], first["http_status"], first["input_tokens"]) == ("timeout", None, None)
    assert (second["status"], second["http_status"]) == ("error", 500)


async def test_the_file_never_holds_text_or_a_key(tmp_path: Path) -> None:
    path = tmp_path / "u.jsonl"
    await ask(llm(lambda r: ok(), path, api_key="sk-very-secret-key"))
    text = path.read_text(encoding="utf-8")
    assert "sk-very-secret-key" not in text and "4111" not in text and "xyzzy" not in text and "be brief" not in text
    assert "?x=1" not in text and "/openai/v1" not in text


async def test_without_a_log_no_file_is_made(tmp_path: Path) -> None:
    client = OpenAICompatibleLLM(base_url="http://x/v1", model="m", transport=httpx.MockTransport(lambda r: ok()))
    await ask(client)
    assert os.listdir(tmp_path) == []
    registry = LLMRegistry(Settings.from_env({"TEAM_B_LLM": "ollama"}))
    assert registry.llm_for("reader") is not None and Settings.from_env({}).usage_log == ""


async def test_a_log_that_cannot_be_written_never_breaks_the_call(tmp_path: Path) -> None:
    answer = await ask(llm(lambda r: ok(), tmp_path))  # a folder is not a file
    assert answer == {"r": SECRET_REPLY}


def test_only_the_server_is_kept_of_the_url() -> None:
    assert provider_host("https://user:pw@api.groq.com/openai/v1?a=b") == "api.groq.com"
    assert provider_host("http://127.0.0.1:11434/v1") == "127.0.0.1:11434"


async def test_the_registry_writes_the_job_and_marks_backup_calls(tmp_path: Path) -> None:
    path = tmp_path / "u.jsonl"
    answers = {"main": [httpx.Response(404, text="model does not exist")], "backup": [ok()]}

    def server(request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content)["model"]
        return answers[model].pop(0) if answers.get(model) else ok()

    settings = Settings.from_env(
        {
            "TEAM_B_LLM": "openai",
            "TEAM_B_LLM_BASE_URL": "http://x/v1",
            "TEAM_B_LLM_MODEL": "main",
            "TEAM_B_LLM_FALLBACK_MODEL_READER": "backup",
            "TEAM_B_USAGE_LOG": str(path),
            "TEAM_B_USAGE_RUN_LABEL": "reg",
        }
    )
    registry = LLMRegistry(settings, transport=httpx.MockTransport(server))
    reader, writer = registry.llm_for("reader"), registry.llm_for("writer")
    assert reader is not None and writer is not None
    await reader.complete_json(system="s", user="u", schema_hint={})
    await writer.complete_json(system="s", user="u", schema_hint={})
    gone, backup, plain = lines(path)
    assert (gone["job"], gone["model"], gone["backup_used"], gone["status"]) == ("reader", "main", False, "error")
    assert (backup["job"], backup["model"], backup["backup_used"], backup["status"]) == ("reader", "backup", True, "ok")
    assert (plain["job"], plain["model"], plain["backup_used"], plain["run_label"]) == ("writer", "main", False, "reg")


def _load_summary() -> Any:
    spec = importlib.util.spec_from_file_location("usage_summary", SUMMARY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_usage_summary_adds_up_across_files_and_writes_csv(tmp_path: Path) -> None:
    summary = _load_summary()
    base = {"service": "conversation", "run_label": "r", "job": "reader", "model": "m", "status": "ok"}
    first = [
        {**base, "input_tokens": 100, "output_tokens": 10, "latency_ms": 200},
        {**base, "input_tokens": 300, "output_tokens": 30, "latency_ms": 400},
        {**base, "status": "rate_limited", "input_tokens": None, "output_tokens": None, "latency_ms": 5},
    ]
    (tmp_path / "a.jsonl").write_text("\n".join(json.dumps(x) for x in first) + "\n\n", encoding="utf-8")
    other = {"service": "knowledge", "run_label": "k", "job": "embed_query", "model": "bge-m3", "status": "ok"}
    second = {**other, "input_tokens": 7, "output_tokens": None, "latency_ms": 90}
    (tmp_path / "b.jsonl").write_text(json.dumps(second) + "\n", encoding="utf-8")
    out = tmp_path / "t.csv"
    paths = [tmp_path / "a.jsonl", tmp_path / "b.jsonl"]
    assert summary.main([*map(str, paths), "--csv", str(out)]) == 0
    rows = {r["service"]: r for r in summary.summarise(summary.read_lines(paths))}
    conv = rows["conversation"]
    assert (conv["calls"], conv["ok_calls"], conv["input_tokens"], conv["output_tokens"]) == (3, 2, 400, 40)
    assert (conv["total_tokens"], conv["avg_tokens_per_ok_call"]) == (440, 220.0)
    assert (conv["latency_p50_ms"], conv["latency_p95_ms"]) == (200, 400)
    assert rows["knowledge"]["total_tokens"] == 7
    assert out.read_text(encoding="utf-8").splitlines()[0].startswith("service,run_label,job,model,calls")
