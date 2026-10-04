"""Local multilingual embeddings through Ollama (default model: bge-m3)."""

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
    def __init__(self, model: str | None = None, url: str | None = None, batch_size: int = 16):
        self.model = model or settings.embed_model
        self.url = (url or settings.ollama_url).rstrip("/")
        self.batch_size = batch_size

    def embed(self, texts: list[str]) -> np.ndarray:
        vectors = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start:start + self.batch_size]
            try:
                resp = httpx.post(
                    f"{self.url}/api/embed",
                    json={"model": self.model, "input": batch},
                    timeout=120,
                )
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                raise EmbeddingUnavailable(
                    f"Ollama embedding call failed ({exc}). Is Ollama running and is "
                    f"'{self.model}' pulled? Try: ollama pull {self.model}"
                ) from exc
            vectors.extend(resp.json()["embeddings"])
        return _unit(np.asarray(vectors, dtype=np.float32))


def _unit(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms
