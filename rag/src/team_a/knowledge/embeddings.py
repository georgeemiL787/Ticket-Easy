"""Local multilingual embeddings through Ollama (default model: bge-m3)."""

import threading
from collections import OrderedDict
from typing import Protocol

import httpx
import numpy as np

from team_a.config import settings


class EmbeddingUnavailable(RuntimeError):
    pass


class Embedder(Protocol):
    model: str

    def embed(self, texts: list[str]) -> np.ndarray: ...


class OllamaEmbedder:
    """Embeds through one persistent HTTP client (a new httpx client per call costs ~250 ms on Windows,
    mostly loading the TLS bundle) and keeps the model loaded for `keep_alive`. Vectors are cached per
    text in a bounded LRU, so a repeated query skips Ollama."""

    def __init__(self, model: str | None = None, url: str | None = None, batch_size: int = 16,
                 cache_size: int = 2048, keep_alive: str | None = None):
        self.model = model or settings.embed_model
        self.url = (url or settings.ollama_url).rstrip("/")
        self.batch_size = batch_size
        self.keep_alive = keep_alive or settings.ollama_keep_alive
        self.cache_size = cache_size
        self._cache: OrderedDict[str, np.ndarray] = OrderedDict()
        self._lock = threading.Lock()
        self._client = httpx.Client(timeout=120)

    def _fetch(self, texts: list[str]) -> np.ndarray:
        vectors = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start:start + self.batch_size]
            try:
                resp = self._client.post(
                    f"{self.url}/api/embed",
                    json={"model": self.model, "input": batch, "keep_alive": self.keep_alive},
                )
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                raise EmbeddingUnavailable(
                    f"Ollama embedding call failed ({exc}). Is Ollama running and is "
                    f"'{self.model}' pulled? Try: ollama pull {self.model}"
                ) from exc
            vectors.extend(resp.json()["embeddings"])
        return _unit(np.asarray(vectors, dtype=np.float32))

    def embed(self, texts: list[str]) -> np.ndarray:
        found: dict[str, np.ndarray] = {}
        with self._lock:
            for t in texts:
                if t in self._cache:
                    self._cache.move_to_end(t)
                    found[t] = self._cache[t]
        missing = list(dict.fromkeys(t for t in texts if t not in found))
        if missing:
            for t, v in zip(missing, self._fetch(missing)):
                found[t] = v
            with self._lock:
                for t in missing:
                    self._cache[t] = found[t]
                    self._cache.move_to_end(t)
                while len(self._cache) > self.cache_size:
                    self._cache.popitem(last=False)
        return np.stack([found[t] for t in texts])

    def warm_up(self) -> bool:
        """Load the model now so the first real query does not pay the load time. Never raises."""
        try:
            self._fetch(["warm-up"])
            return True
        except EmbeddingUnavailable:
            return False


def _unit(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms
