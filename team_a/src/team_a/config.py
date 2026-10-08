import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

# Data is grouped by business category: data/<category>/<tenant_id>/ holds one tenant's corpus, mock backend
# data, tickets, rules, precedents and benchmarks. data/shared/ holds what all tenants use.
CATEGORIES = ("e-commerce", "telecom")


@dataclass(frozen=True)
class Settings:
    data_dir: Path = ROOT / "data"
    index_dir: Path = ROOT / "var" / "index"
    resolutions_dir: Path | None = None  # tests redirect precedent writes here; default is the tenant folder
    db_path: Path = ROOT / "var" / "db" / "team_a.sqlite"
    ollama_url: str = os.getenv("OLLAMA_URL", "http://localhost:11434")
    embed_model: str = os.getenv("EMBED_MODEL", "bge-m3")
    ollama_keep_alive: str = os.getenv("OLLAMA_KEEP_ALIVE", "30m")  # how long Ollama keeps the model loaded
    openrouter_api_key: str = os.getenv("OPENROUTER_API_KEY", "")
    openrouter_models: list[str] = field(
        default_factory=lambda: [
            m.strip()
            for m in os.getenv("OPENROUTER_MODELS", "google/gemma-4-31b-it:free").split(",")
            if m.strip()
        ]
    )
    min_cosine: float = float(os.getenv("MIN_COSINE", "0.58"))
    min_bm25: float = float(os.getenv("MIN_BM25", "2.5"))
    admin_api_key: str = os.getenv("ADMIN_API_KEY", "")

    @property
    def shared_dir(self) -> Path:
        return self.data_dir / "shared"

    def category(self, tenant_id: str) -> str | None:
        for category in CATEGORIES:
            if (self.data_dir / category / tenant_id).is_dir():
                return category
        return None

    def tenant_dir(self, tenant_id: str) -> Path:
        """data/<category>/<tenant_id>; for an unknown tenant a path that does not exist."""
        category = self.category(tenant_id)
        return self.data_dir / category / tenant_id if category else self.data_dir / "_unknown" / tenant_id

    def tenants(self) -> list[str]:
        return sorted(p.parent.parent.name for c in CATEGORIES
                      for p in (self.data_dir / c).glob("*/corpus/manifest.json"))

    def corpus_dir(self, tenant_id: str) -> Path:
        return self.tenant_dir(tenant_id) / "corpus"

    def mock_dir(self, tenant_id: str) -> Path:
        return self.tenant_dir(tenant_id) / "mock"

    def rules_file(self, tenant_id: str) -> Path:
        return self.tenant_dir(tenant_id) / "rules.json"

    def tickets_file(self, tenant_id: str) -> Path:
        return self.tenant_dir(tenant_id) / "tickets.jsonl"

    def resolutions_file(self, tenant_id: str) -> Path:
        if self.resolutions_dir is not None:
            return self.resolutions_dir / f"{tenant_id}.jsonl"
        return self.tenant_dir(tenant_id) / "resolutions.jsonl"

    def synonyms_file(self, tenant_id: str) -> Path:
        return self.tenant_dir(tenant_id) / "synonyms.json"

    def benchmark_file(self, tenant_id: str, name: str) -> Path:
        """name: "retrieval.dev", "retrieval.test" or "guardrails"."""
        return self.tenant_dir(tenant_id) / "benchmark" / f"{name}.jsonl"

    def tenant_index_dir(self, tenant_id: str) -> Path:
        return self.index_dir / tenant_id


settings = Settings()
