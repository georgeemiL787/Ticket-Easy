"""Service settings, read from environment variables. Shop-specific settings live in config/tenants/ instead."""

import os
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, model_validator

from agent_service.domain.agent import AgentMode

PROJECT_ROOT = Path(__file__).resolve().parents[2]  # the services/agent/ folder

Backends = Literal["simulated", "live"]
StoreKind = Literal["memory", "sqlite"]
LlmKind = Literal["none", "ollama", "openrouter", "openai"]

# Settings field -> environment variable.
ENV_VARS: dict[str, str] = {
    "backends": "AGENT_BACKENDS",
    "config_dir": "AGENT_CONFIG_DIR",
    "data_dir": "AGENT_DATA_DIR",
    "fixtures_dir": "AGENT_FIXTURES_DIR",
    "store": "AGENT_STORE",
    "db_path": "AGENT_DB_PATH",
    "llm": "AGENT_LLM",
    "ollama_url": "OLLAMA_URL",
    "ollama_model": "AGENT_OLLAMA_MODEL",
    "openrouter_api_key": "OPENROUTER_API_KEY",
    "knowledge_url": "AGENT_KNOWLEDGE_URL",
    "knowledge_timeout_s": "AGENT_KNOWLEDGE_TIMEOUT_S",
    "knowledge_key": "AGENT_KNOWLEDGE_KEY",
    "knowledge_admin_key": "AGENT_KNOWLEDGE_ADMIN_KEY",
    "tools_config": "AGENT_TOOLS_CONFIG",
    "llm_base_url": "AGENT_LLM_BASE_URL",
    "llm_api_key": "AGENT_LLM_API_KEY",
    "llm_model": "AGENT_LLM_MODEL",
    "llm_timeout_s": "AGENT_LLM_TIMEOUT_S",
    "fixed_today": "AGENT_FIXED_TODAY",
    "clock_ticks": "AGENT_CLOCK_TICKS",
    "capability_ttl_s": "AGENT_CAPABILITY_TTL_S",
    "queue_max_runs": "AGENT_QUEUE_MAX_RUNS",
    "alert_webhook": "AGENT_ALERT_WEBHOOK",
    "enable_test_admin": "AGENT_ENABLE_TEST_ADMIN",
    "auth_required": "AGENT_AUTH_REQUIRED",
    "secret_key": "AGENT_SECRET_KEY",
    "cookie_secure": "AGENT_COOKIE_SECURE",
    "session_hours": "AGENT_SESSION_HOURS",
    "chat_api_keys": "AGENT_CHAT_API_KEYS",
    "alert_interval_s": "AGENT_ALERT_INTERVAL_S",
    "log_json": "AGENT_LOG_JSON",
    "retention_days": "AGENT_RETENTION_DAYS",
    "llm_rewrite": "AGENT_LLM_REWRITE",
    "llm_policy": "AGENT_LLM_POLICY",
    "llm_knowledge": "AGENT_LLM_KNOWLEDGE",
    "llm_review": "AGENT_LLM_REVIEW",
    "rate_limit_per_minute": "AGENT_RATE_LIMIT_PER_MIN",
    "llm_max_wait_s": "AGENT_LLM_MAX_WAIT_S",
    "agent_mode": "AGENT_PLANNING_MODE",
    "agent_max_moves": "AGENT_PLANNING_MAX_MOVES",
    "agent_turn_timeout_s": "AGENT_PLANNING_TURN_TIMEOUT_S",
    "bundles": "AGENT_BUNDLES",
    "usage_log": "AGENT_USAGE_LOG",
    "usage_run_label": "AGENT_USAGE_RUN_LABEL",
}

# The jobs the AI model does. Each may use its own model (and provider, backup model, limits); see RoleSettings.
ROLES: tuple[str, ...] = (
    "reader", "planner", "knowledge", "reviewer", "writer", "writer_egyptian", "summary", "judge",
)  # fmt: skip
# RoleSettings field -> the variable's name; {ROLE} is the role in capitals (AGENT_LLM_MODEL_WRITER_EGYPTIAN).
ROLE_ENV_VARS: dict[str, str] = {
    "model": "AGENT_LLM_MODEL_{ROLE}",
    "base_url": "AGENT_LLM_BASE_URL_{ROLE}",
    "api_key": "AGENT_LLM_API_KEY_{ROLE}",
    "fallback_model": "AGENT_LLM_FALLBACK_MODEL_{ROLE}",
    "reasoning_effort": "AGENT_LLM_REASONING_EFFORT_{ROLE}",
    "rpm": "AGENT_LLM_RPM_{ROLE}",
    "tpm": "AGENT_LLM_TPM_{ROLE}",
}
ReasoningEffort = Literal["low", "medium", "high"]

PREFIX = "AGENT_"  # every setting of this service starts with it, except the shared Ollama and OpenRouter ones
SIBLING_PREFIXES = ("AGENT_", "KNOWLEDGE_", "TOOLS_")
SHARED_SETTINGS = frozenset({"OLLAMA_URL", "OPENROUTER_API_KEY"})


def own_setting_names() -> frozenset[str]:
    """Every variable this service reads under its own prefix, including the per-job ones."""
    names = {name for name in ENV_VARS.values() if name.startswith(PREFIX)}
    names |= {pattern.format(ROLE=role.upper()) for role in ROLES for pattern in ROLE_ENV_VARS.values()}
    return frozenset(names)


def unknown_setting_warnings(env: Mapping[str, str] | None = None) -> list[str]:
    """A variable that ends like one of our settings but starts differently is probably an old or mistyped name."""
    own = own_setting_names()
    warnings = []
    for name in sorted(os.environ if env is None else env):
        upper = name.upper()
        if upper in own or upper in SHARED_SETTINGS or upper.startswith(SIBLING_PREFIXES):
            continue
        for setting in sorted(own, key=len, reverse=True):
            tail = setting[len(PREFIX) :]
            if upper == tail or (upper.endswith("_" + tail) and 1 <= len(upper[: -len(tail) - 1].split("_")) <= 2):
                warnings.append(f"Setting {name} is not used. Did you mean {setting}?")
                break
    return warnings


class SettingsError(ValueError):
    """An environment variable has a value we cannot use. The message names the variable."""


class RoleSettings(BaseModel):
    """How one job of the AI model differs from the general AGENT_LLM_* settings. Unset: the general one is used."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str | None = None  # AGENT_LLM_MODEL_<ROLE>
    base_url: str | None = None  # another provider for this job (e.g. Ollama); then api_key is its own, never shared
    api_key: SecretStr | None = None  # several keys separated by commas are rotated on a rate limit
    fallback_model: str | None = None  # used when the model is gone ("not found", "decommissioned") or keeps failing
    reasoning_effort: ReasoningEffort | None = None  # sent as reasoning_effort only when set (GPT-OSS on Groq)
    rpm: int | None = Field(default=None, ge=1)  # requests per minute this job's model may be sent (free plan limits)
    tpm: int | None = Field(default=None, ge=1)  # tokens per minute

    @model_validator(mode="after")
    def _own_provider_needs_a_model(self) -> Self:
        if self.base_url and not self.model:
            raise ValueError("a role with its own base_url needs its own model too")
        return self


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    # simulated: the agent's own fake services. live: the real knowledge service (policy search, safety, rules)
    backends: Backends = "simulated"
    knowledge_url: str = "http://127.0.0.1:8001"  # AGENT_BACKENDS=live: where the knowledge service listens
    knowledge_timeout_s: float = Field(default=10.0, gt=0)
    knowledge_key: SecretStr | None = (
        # sent as X-Service-Key on every call to the knowledge service (needed when the knowledge service has service
        # keys)
        None
    )
    knowledge_admin_key: SecretStr | None = (
        None  # X-Admin-Key for documents and rules (the knowledge pages); unset: those pages are off
    )
    # The tools service's sandbox tools over MCP: the operator's JSON file (see config/team_c.example.json). Unset: the
    # simulated shop serves every tool. Set: the tools it binds are served by the tools service, the rest by the
    # simulated service.
    tools_config: Path | None = None
    config_dir: Path = PROJECT_ROOT / "config" / "tenants"
    data_dir: Path = PROJECT_ROOT / "data"
    fixtures_dir: Path = PROJECT_ROOT / "fixtures"
    store: StoreKind = "memory"
    db_path: Path = PROJECT_ROOT / "var" / "agent.sqlite3"
    llm: LlmKind = "none"
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3:8b"
    openrouter_api_key: SecretStr | None = None
    llm_base_url: str | None = None  # AGENT_LLM=openai: any server speaking the OpenAI chat format (Groq, Gemini, ...)
    llm_api_key: SecretStr | None = None  # its key (never put in a file that is committed)
    llm_model: str | None = None  # overrides the default model of the chosen provider
    llm_timeout_s: float = Field(default=20.0, gt=0)
    llm_max_wait_s: float = Field(
        default=4.0, ge=0
    )  # a call waits at most this long for its rate-limit slot, else it falls back
    agent_mode: AgentMode = (
        "on"  # the default for businesses that do not set agent_mode; needs a model for the planner job
    )
    agent_max_moves: int = Field(default=5, ge=1)  # asks of the planner per message, then the usual stages run
    agent_turn_timeout_s: float = Field(default=20.0, gt=0)  # seconds the agent loop may take for one message
    roles: dict[str, RoleSettings] = Field(default_factory=dict)  # only the roles that have settings of their own
    fixed_today: date | None = None  # pins the date for demos and tests, e.g. 2026-09-28
    clock_ticks: bool = False  # with fixed_today: start on that day at noon, then keep running
    capability_ttl_s: float = Field(default=60.0, ge=0)  # how long the list of shop tools is cached
    auth_required: bool = True  # staff must sign in to the inbox, dashboard and traces. 0 only for local experiments.
    secret_key: SecretStr | None = None  # signs the sign-in cookie; unset: a random key per start (all sign in again)
    cookie_secure: bool = False  # 1 when served over https: the cookie is then never sent over plain http
    session_hours: float = Field(default=12.0, gt=0, le=24 * 30)
    chat_api_keys: str = ""  # "shop_001=abc,shop_002=def": the chat of those businesses needs the header X-Api-Key
    enable_test_admin: bool = False  # 1: /v1/_test/* (chaos switches, audit report) exists. Never in production.
    alert_webhook: str | None = (
        None  # optional URL that receives every opened and resolved alert (JSON, no message text)
    )
    alert_interval_s: float = Field(default=60.0, gt=0)  # how often the alert engine looks
    queue_max_runs: int = Field(default=3, ge=0)  # queued requests answered in one turn (chained in one reply)
    log_json: bool = True
    llm_rewrite: bool = False  # let the AI model reword replies (checked: it may not add any fact); needs AGENT_LLM
    llm_policy: bool = False  # an AI agent reads the policy and may tighten (never loosen) the rule checker
    llm_knowledge: bool = False  # the AI model writes the search query and words the policy answer
    llm_review: bool = False  # the reviewer checks every written reply and every write (stricter only); needs a model
    rate_limit_per_minute: int = Field(default=20, ge=1)  # messages per conversation per minute (chat API)
    bundles: bool = True  # 0: tenant bundles are ignored and only config/tenants/*.json is used (always-safe fallback)
    retention_days: int = Field(default=90, ge=1)  # older conversations, traces and finished cases are deleted
    usage_log: str = ""  # a file path: one JSON line per AI request with the provider's token counts. Empty = off
    usage_run_label: str = ""  # free text written into every usage line (which run it belongs to)

    @model_validator(mode="after")
    def _openrouter_needs_a_key(self) -> Self:
        if self.llm == "openrouter" and self.openrouter_api_key is None:
            raise ValueError("AGENT_LLM=openrouter needs OPENROUTER_API_KEY")
        if self.llm == "openai" and (not self.llm_base_url or not self.llm_model):
            raise ValueError(
                "AGENT_LLM=openai needs AGENT_LLM_BASE_URL and AGENT_LLM_MODEL (and usually AGENT_LLM_API_KEY)"
            )
        return self

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Self:
        """Read settings from env (default: the process environment). Empty values count as not set."""
        source = os.environ if env is None else env
        raw: dict[str, Any] = {
            field: source[name].strip() for field, name in ENV_VARS.items() if source.get(name, "").strip()
        }
        if "llm" not in raw and raw.get("llm_base_url") and raw.get("llm_model"):
            raw["llm"] = "openai"  # a configured AI endpoint is used unless AGENT_LLM=none says otherwise
        roles: dict[str, dict[str, str]] = {}
        for role in ROLES:
            found = {
                field: source[name.format(ROLE=role.upper())].strip()
                for field, name in ROLE_ENV_VARS.items()
                if source.get(name.format(ROLE=role.upper()), "").strip()
            }
            if found:
                roles[role] = found
        if roles:
            raw["roles"] = roles
        try:
            return cls.model_validate(raw)
        except ValidationError as exc:
            problems = []
            for error in exc.errors():
                field = str(error["loc"][0]) if error["loc"] else ""
                if field == "roles" and len(error["loc"]) > 1:
                    role = str(error["loc"][1])
                    sub = str(error["loc"][2]) if len(error["loc"]) > 2 else ""
                    name = ROLE_ENV_VARS.get(sub, "AGENT_LLM_*_{ROLE}").format(ROLE=role.upper())
                else:
                    name = ENV_VARS.get(field, field or "settings")
                problems.append(f"{name}: {error['msg']}")
            raise SettingsError("; ".join(problems)) from None

    def role(self, name: str) -> RoleSettings:
        """The settings of one job (all unset when it has none of its own)."""
        return self.roles.get(name, RoleSettings())
