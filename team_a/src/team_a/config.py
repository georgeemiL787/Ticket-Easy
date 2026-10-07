import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    data_dir: Path = ROOT / "data"
    index_dir: Path = ROOT / "var" / "index"
    ollama_url: str = os.getenv("OLLAMA_URL", "http://localhost:11434")
    embed_model: str = os.getenv("EMBED_MODEL", "bge-m3")
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

    def corpus_dir(self, tenant_id: str) -> Path:
        return self.data_dir / "corpus" / tenant_id

    def rules_file(self, tenant_id: str) -> Path:
        return self.data_dir / "rules" / f"{tenant_id}.json"

    def tickets_file(self, tenant_id: str) -> Path:
        return self.data_dir / "tickets" / f"{tenant_id}.jsonl"

    def tenant_index_dir(self, tenant_id: str) -> Path:
        return self.index_dir / tenant_id


settings = Settings()
