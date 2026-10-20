"""The token usage log of the knowledge service: exact counts, nulls, no text, no key, off by default. No network."""

import dataclasses
import importlib.util
import json
from pathlib import Path

import httpx
import numpy as np
import pytest

from team_a import llm, usage_log
from team_a.config import settings as real_settings
from team_a.knowledge.embeddings import EmbeddingUnavailable, OllamaEmbedder
from team_a.knowledge.retrieval import _cosines
from team_a.policy.risk import classify_risk
from team_a.schemas import ClassifyRiskRequest

SUMMARY = Path(__file__).resolve().parents[2] / "agent" / "scripts" / "usage_summary.py"
KEY = "sk-or-v1-very-secret"
USAGE = {"prompt_tokens": 210, "completion_tokens": 45, "completion_tokens_details": {"reasoning_tokens": 5}}


def lines(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def log(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "usage.jsonl"
    on = dataclasses.replace(
        real_settings,
        usage_log=str(path),
        usage_run_label="unit",
        openrouter_api_key=KEY,
        openrouter_models=["a/one:free", "b/two:free"],
    )
    monkeypatch.setattr(usage_log, "settings", on)
    monkeypatch.setattr(llm, "settings", on)
    return path


def openrouter(monkeypatch, replies: list[httpx.Response]) -> None:
    monkeypatch.setattr(llm, "_http", httpx.Client(transport=httpx.MockTransport(lambda r: replies.pop(0))))


def chat(content: str = '{"categories": [], "rationale": "fine"}', usage: dict | None = USAGE) -> httpx.Response:
    body = {"choices": [{"message": {"content": content}}]}
    if usage is not None:
        body["usage"] = usage
    return httpx.Response(200, json=body)


def test_a_risk_second_opinion_writes_exact_counts_and_no_text(log, monkeypatch):
    openrouter(monkeypatch, [chat()])
    monkeypatch.setattr(llm, "is_configured", lambda: True)
    classify_risk(ClassifyRiskRequest(request_id="r1", tenant_id="shop_001", message="my secret card 4111", use_llm=True))
    [line] = lines(log)
    assert line["service"] == "knowledge" and line["job"] == "risk_second_opinion" and line["kind"] == "chat"
    assert line["run_label"] == "unit" and line["model"] == "a/one:free" and line["provider"] == "openrouter.ai"
    assert (line["input_tokens"], line["output_tokens"], line["reasoning_tokens"]) == (210, 45, 5)
    assert line["cached_tokens"] is None and line["status"] == "ok" and line["key_index"] == 0
    text = log.read_text(encoding="utf-8")
    assert KEY not in text and "4111" not in text and "fine" not in text


def test_a_rate_limited_model_is_written_and_the_next_model_is_its_own_line(log, monkeypatch):
    openrouter(monkeypatch, [httpx.Response(429, text="slow"), chat('{"rules": []}', usage=None)])
    llm.complete_json("s", "u")
    first, second = lines(log)
    assert (first["status"], first["http_status"], first["input_tokens"], first["model"]) == (
        "rate_limited", 429, None, "a/one:free")
    assert (second["status"], second["input_tokens"], second["output_tokens"], second["model"]) == (
        "ok", None, None, "b/two:free")
    assert first["job"] == "rule_extraction"


def test_a_timeout_is_written(log, monkeypatch):
    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)

    monkeypatch.setattr(llm, "_http", httpx.Client(transport=httpx.MockTransport(slow)))
    with pytest.raises(llm.LLMUnavailable):
        llm.complete_json("s", "u")
    assert [x["status"] for x in lines(log)] == ["timeout", "timeout"]


def ollama(counts: bool, calls: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if calls is not None:
            calls.append(body)
        out: dict = {"embeddings": [[1.0, 0.0] for _ in body["input"]]}
        if counts:
            out["prompt_eval_count"] = 17
        return httpx.Response(200, json=out)

    return handler


def embedder(handler) -> OllamaEmbedder:
    e = OllamaEmbedder(model="bge-m3", url="http://127.0.0.1:11434")
    e._client = httpx.Client(transport=httpx.MockTransport(handler))
    return e


def test_embeddings_use_the_reported_count_and_queries_are_told_apart(log):
    e = embedder(ollama(counts=True))
    e.embed(["a passage of the policy"])
    _cosines("where is my order", np.array([[1.0, 0.0]], dtype=np.float32), e)
    doc, query = lines(log)
    assert (doc["job"], doc["kind"], doc["input_tokens"], doc["estimated"]) == ("embed_document", "embedding", 17, False)
    assert (query["job"], query["input_tokens"], query["output_tokens"]) == ("embed_query", 17, None)
    assert doc["provider"] == "127.0.0.1:11434" and doc["model"] == "bge-m3"
    assert "passage" not in log.read_text(encoding="utf-8")


def test_an_unreported_embedding_count_is_an_estimate_and_says_so(log):
    embedder(ollama(counts=False)).embed(["x" * 30])
    [line] = lines(log)
    assert line["input_tokens"] == 10 and line["estimated"] is True


def test_a_failed_embedding_request_is_written_with_null_tokens(log):
    e = embedder(lambda r: httpx.Response(500, text="boom"))
    with pytest.raises(EmbeddingUnavailable):
        e.embed(["text"])
    [line] = lines(log)
    assert (line["status"], line["http_status"], line["input_tokens"], line["estimated"]) == ("error", 500, None, False)


def test_nothing_is_written_when_the_log_is_off(tmp_path, monkeypatch):
    off = dataclasses.replace(real_settings, usage_log="")
    monkeypatch.setattr(usage_log, "settings", off)
    embedder(ollama(counts=True)).embed(["text"])
    assert list(tmp_path.iterdir()) == [] and real_settings.usage_log == ""


def test_an_unwritable_log_does_not_break_the_work(tmp_path, monkeypatch):
    broken = dataclasses.replace(real_settings, usage_log=str(tmp_path))  # a folder, not a file
    monkeypatch.setattr(usage_log, "settings", broken)
    assert embedder(ollama(counts=True)).embed(["text"]).shape == (1, 2)


def test_lines_from_all_three_services_are_read_together_by_usage_summary(log, tmp_path):
    embedder(ollama(counts=True)).embed(["text"])
    others = tmp_path / "others.jsonl"
    common = {"status": "ok", "kind": "chat", "latency_ms": 10, "estimated": False}
    others.write_text(
        json.dumps({**common, "service": "conversation", "run_label": "c", "job": "reader", "model": "m", "input_tokens": 5, "output_tokens": 1})
        + "\n"
        + json.dumps({**common, "service": "tools", "run_label": "t", "job": "generation", "model": "g", "input_tokens": 7, "output_tokens": 3})
        + "\n",
        encoding="utf-8",
    )
    spec = importlib.util.spec_from_file_location("usage_summary", SUMMARY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rows = {r["service"]: r for r in module.summarise(module.read_lines([log, others]))}
    assert set(rows) == {"knowledge", "conversation", "tools"}
    assert rows["knowledge"]["input_tokens"] == 17 and rows["tools"]["total_tokens"] == 10
