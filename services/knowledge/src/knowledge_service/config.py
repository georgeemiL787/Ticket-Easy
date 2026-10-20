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
    db_path: Path = ROOT / "var" / "db" / "knowledge.sqlite"
    ollama_url: str = os.getenv("OLLAMA_URL", "http://localhost:11434")
    embed_model: str = os.getenv("KNOWLEDGE_EMBED_MODEL", "bge-m3")
    ollama_keep_alive: str = os.getenv("OLLAMA_KEEP_ALIVE", "30m")  # how long Ollama keeps the model loaded
    openrouter_api_key: str = os.getenv("OPENROUTER_API_KEY", "")
    openrouter_models: list[str] = field(
        default_factory=lambda: [
            m.strip()
            for m in os.getenv("OPENROUTER_MODELS", "google/gemma-4-31b-it:free").split(",")
            if m.strip()
        ]
    )
    min_cosine: float = float(os.getenv("KNOWLEDGE_MIN_COSINE", "0.58"))
    min_bm25: float = float(os.getenv("KNOWLEDGE_MIN_BM25", "2.5"))
    admin_api_key: str = os.getenv("KNOWLEDGE_ADMIN_API_KEY", "")
    # Who may call the public endpoints. Unset: anybody (local development). KNOWLEDGE_SERVICE_API_KEY: one key for every tenant;
    # KNOWLEDGE_SERVICE_API_KEYS: "key=tenantA+tenantB,otherkey=*" binds each key to the tenants it may read.
    service_api_key: str = os.getenv("KNOWLEDGE_SERVICE_API_KEY", "")
    service_api_keys: str = os.getenv("KNOWLEDGE_SERVICE_API_KEYS", "")
    onboarded_dir: Path = ROOT / "var" / "tenants"  # businesses added through the admin API (documents, rules)
    max_upload_bytes: int = int(os.getenv("KNOWLEDGE_MAX_UPLOAD_BYTES", str(10 * 1024 * 1024)))
    usage_log: str = os.getenv("KNOWLEDGE_USAGE_LOG", "")  # a file path: one JSON line per AI request (tokens only). Empty = off
    usage_run_label: str = os.getenv("KNOWLEDGE_USAGE_RUN_LABEL", "")  # free text written into every usage line

    @property
    def shared_dir(self) -> Path:
        return self.data_dir / "shared"

    def category(self, tenant_id: str) -> str | None:
        for category in CATEGORIES:
            if (self.data_dir / category / tenant_id).is_dir():
                return category
        return None

    def tenant_dir(self, tenant_id: str) -> Path:
        """data/<category>/<tenant_id>; var/tenants/<tenant_id> for a business added through the admin API;
        for an unknown tenant a path that does not exist."""
        category = self.category(tenant_id)
        if category:
            return self.data_dir / category / tenant_id
        if (self.onboarded_dir / tenant_id).is_dir():
            return self.onboarded_dir / tenant_id
        return self.data_dir / "_unknown" / tenant_id

    def is_onboarded(self, tenant_id: str) -> bool:
        """Added through the admin API (its files live in var/tenants), not shipped in data/."""
        return self.category(tenant_id) is None and (self.onboarded_dir / tenant_id).is_dir()

    def tenants(self) -> list[str]:
        shipped = [p.parent.parent.name for c in CATEGORIES for p in (self.data_dir / c).glob("*/corpus/manifest.json")]
        added = [p.parent.parent.name for p in self.onboarded_dir.glob("*/corpus/manifest.json")]
        return sorted({*shipped, *added})

    def allowed_tenants(self, key: str) -> frozenset[str] | None:
        """The tenants a service key may read: None means every tenant; an unknown key is an empty set (nothing)."""
        import hmac

        keys: dict[str, frozenset[str] | None] = {}
        if self.service_api_key:
            keys[self.service_api_key] = None
        for item in self.service_api_keys.split(","):
            name, _, tenants = item.strip().partition("=")
            if name and tenants:
                keys[name.strip()] = None if tenants.strip() == "*" else frozenset(t for t in tenants.strip().split("+") if t)
        for known, tenants in keys.items():
            if key and hmac.compare_digest(known, key):
                return tenants
        return frozenset()

    @property
    def service_auth(self) -> bool:
        return bool(self.service_api_key or self.service_api_keys)

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

PREFIX = "KNOWLEDGE_"  # every setting of this service starts with it, except the shared Ollama and OpenRouter ones
SIBLING_PREFIXES = ("AGENT_", "KNOWLEDGE_", "TOOLS_")
OWN_SETTINGS = (
    "KNOWLEDGE_EMBED_MODEL", "KNOWLEDGE_MIN_COSINE", "KNOWLEDGE_MIN_BM25", "KNOWLEDGE_ADMIN_API_KEY",
    "KNOWLEDGE_SERVICE_API_KEY", "KNOWLEDGE_SERVICE_API_KEYS", "KNOWLEDGE_MAX_UPLOAD_BYTES", "KNOWLEDGE_USAGE_LOG",
    "KNOWLEDGE_USAGE_RUN_LABEL",
)  # fmt: skip
SHARED_SETTINGS = ("OLLAMA_URL", "OLLAMA_KEEP_ALIVE", "OPENROUTER_API_KEY", "OPENROUTER_MODELS")


def unknown_setting_warnings(environ: "dict[str, str] | None" = None) -> list[str]:
    """A variable that ends like one of our settings but starts differently is probably an old or mistyped name."""
    warnings = []
    for name in sorted(os.environ if environ is None else environ):
        upper = name.upper()
        if upper in OWN_SETTINGS or upper in SHARED_SETTINGS or upper.startswith(SIBLING_PREFIXES):
            continue
        for setting in sorted(OWN_SETTINGS, key=len, reverse=True):
            tail = setting[len(PREFIX):]
            if upper == tail or (upper.endswith("_" + tail) and 1 <= len(upper[: -len(tail) - 1].split("_")) <= 2):
                warnings.append(f"Setting {name} is not used. Did you mean {setting}?")
                break
    return warnings
