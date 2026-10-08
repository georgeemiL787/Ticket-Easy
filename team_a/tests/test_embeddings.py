"""OllamaEmbedder latency features: one persistent client, keep_alive, per-text LRU cache. No Ollama needed."""

import json

import httpx
import numpy as np
import pytest

from team_a.knowledge.embeddings import EmbeddingUnavailable, OllamaEmbedder


def fake_ollama(calls: list):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        vectors = [[float(len(t)), 1.0, 0.0] for t in body["input"]]
        return httpx.Response(200, json={"embeddings": vectors})
    return handler


@pytest.fixture
def embedder():
    calls: list = []
    e = OllamaEmbedder(model="bge-m3", url="http://ollama.test", cache_size=3, keep_alive="30m")
    e._client = httpx.Client(transport=httpx.MockTransport(fake_ollama(calls)))
    return e, calls


def test_requests_keep_the_model_loaded(embedder):
    e, calls = embedder
    e.embed(["hello"])
    assert calls == [{"model": "bge-m3", "input": ["hello"], "keep_alive": "30m"}]


def test_repeated_text_is_served_from_the_cache(embedder):
    e, calls = embedder
    first = e.embed(["where is my refund"])
    again = e.embed(["where is my refund"])
    assert len(calls) == 1 and np.array_equal(first, again)
    assert abs(np.linalg.norm(first[0]) - 1) < 1e-6


def test_only_missing_texts_are_fetched_and_order_is_kept(embedder):
    e, calls = embedder
    e.embed(["a"])
    out = e.embed(["bbb", "a", "bbb"])
    assert calls[-1]["input"] == ["bbb"]  # "a" cached, duplicate "bbb" fetched once
    assert np.array_equal(out[0], out[2]) and not np.array_equal(out[0], out[1])


def test_cache_is_bounded_lru(embedder):
    e, calls = embedder
    for t in ["a", "bb", "ccc", "a", "dddd"]:  # "bb" is the least recently used when "dddd" arrives
        e.embed([t])
    assert list(e._cache) == ["ccc", "a", "dddd"]


def test_outage_raises_embedding_unavailable_and_warm_up_does_not():
    def down(_request):
        raise httpx.ConnectError("refused")

    e = OllamaEmbedder(url="http://ollama.test")
    e._client = httpx.Client(transport=httpx.MockTransport(down))
    with pytest.raises(EmbeddingUnavailable):
        e.embed(["x"])
    assert e.warm_up() is False
    assert e._cache == {}  # failures are never cached


def test_index_cache_hit_does_not_open_the_database(built_db, monkeypatch):
    from team_a.db import repository

    first = repository.load_index("noon_eg", None, built_db)
    monkeypatch.setattr(repository, "_open", lambda *a: (_ for _ in ()).throw(AssertionError("opened")))
    assert repository.load_index("noon_eg", None, built_db) is first
