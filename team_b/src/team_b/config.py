"""Service settings, read from environment variables. Shop-specific settings live in config/tenants/ instead."""

import os
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, model_validator

from team_b.domain.agent import AgentMode

PROJECT_ROOT = Path(__file__).resolve().parents[2]  # the team_b/ folder

Mode = Literal["standin", "live"]
StoreKind = Literal["memory", "sqlite"]
LlmKind = Literal["none", "ollama", "openrouter", "openai"]

# Settings field -> environment variable.
ENV_VARS: dict[str, str] = {
    "mode": "TEAM_B_MODE",
    "config_dir": "TEAM_B_CONFIG_DIR",
    "data_dir": "TEAM_B_DATA_DIR",
    "fixtures_dir": "TEAM_B_FIXTURES_DIR",
    "store": "TEAM_B_STORE",
    "db_path": "TEAM_B_DB_PATH",
    "llm": "TEAM_B_LLM",
    "ollama_url": "OLLAMA_URL",
    "ollama_model": "TEAM_B_OLLAMA_MODEL",
    "openrouter_api_key": "OPENROUTER_API_KEY",
    "team_a_url": "TEAM_B_TEAM_A_URL",
    "team_a_timeout_s": "TEAM_B_TEAM_A_TIMEOUT_S",
    "team_a_key": "TEAM_B_TEAM_A_KEY",
    "team_a_admin_key": "TEAM_B_TEAM_A_ADMIN_KEY",
    "team_c_config": "TEAM_B_TEAM_C_CONFIG",
    "llm_base_url": "TEAM_B_LLM_BASE_URL",
    "llm_api_key": "TEAM_B_LLM_API_KEY",
    "llm_model": "TEAM_B_LLM_MODEL",
    "llm_timeout_s": "TEAM_B_LLM_TIMEOUT_S",
    "fixed_today": "TEAM_B_FIXED_TODAY",
    "clock_ticks": "TEAM_B_CLOCK_TICKS",
    "capability_ttl_s": "TEAM_B_CAPABILITY_TTL_S",
    "queue_max_runs": "TEAM_B_QUEUE_MAX_RUNS",
    "alert_webhook": "TEAM_B_ALERT_WEBHOOK",
    "enable_test_admin": "TEAM_B_ENABLE_TEST_ADMIN",
    "auth_required": "TEAM_B_AUTH_REQUIRED",
    "secret_key": "TEAM_B_SECRET_KEY",
    "cookie_secure": "TEAM_B_COOKIE_SECURE",
    "session_hours": "TEAM_B_SESSION_HOURS",
    "chat_api_keys": "TEAM_B_CHAT_API_KEYS",
    "alert_interval_s": "TEAM_B_ALERT_INTERVAL_S",
    "log_json": "TEAM_B_LOG_JSON",
    "retention_days": "TEAM_B_RETENTION_DAYS",
    "llm_rewrite": "TEAM_B_LLM_REWRITE",
    "llm_policy": "TEAM_B_LLM_POLICY",
    "llm_knowledge": "TEAM_B_LLM_KNOWLEDGE",
    "llm_review": "TEAM_B_LLM_REVIEW",
    "rate_limit_per_minute": "TEAM_B_RATE_LIMIT_PER_MIN",
    "llm_max_wait_s": "TEAM_B_LLM_MAX_WAIT_S",
    "agent_mode": "TEAM_B_AGENT_MODE",
    "agent_max_moves": "TEAM_B_AGENT_MAX_MOVES",
    "agent_turn_timeout_s": "TEAM_B_AGENT_TURN_TIMEOUT_S",
    "bundles": "TEAM_B_BUNDLES",
    "usage_log": "TEAM_B_USAGE_LOG",
    "usage_run_label": "TEAM_B_USAGE_RUN_LABEL",
}

# The jobs the AI model does. Each may use its own model (and provider, backup model, limits); see RoleSettings.
ROLES: tuple[str, ...] = (
    "reader", "orchestrator", "knowledge", "reviewer", "writer", "writer_egyptian", "summary", "judge",
)  # fmt: skip
# RoleSettings field -> the variable's name; {ROLE} is the role in capitals (TEAM_B_LLM_MODEL_WRITER_EGYPTIAN).
ROLE_ENV_VARS: dict[str, str] = {
    "model": "TEAM_B_LLM_MODEL_{ROLE}",
    "base_url": "TEAM_B_LLM_BASE_URL_{ROLE}",
    "api_key": "TEAM_B_LLM_API_KEY_{ROLE}",
    "fallback_model": "TEAM_B_LLM_FALLBACK_MODEL_{ROLE}",
    "reasoning_effort": "TEAM_B_LLM_REASONING_EFFORT_{ROLE}",
    "rpm": "TEAM_B_LLM_RPM_{ROLE}",
    "tpm": "TEAM_B_LLM_TPM_{ROLE}",
}
ReasoningEffort = Literal["low", "medium", "high"]


class SettingsError(ValueError):
    """An environment variable has a value we cannot use. The message names the variable."""


class RoleSettings(BaseModel):
    """How one job of the AI model differs from the general TEAM_B_LLM_* settings. Unset: the general one is used."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str | None = None  # TEAM_B_LLM_MODEL_<ROLE>
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

    mode: Mode = "standin"  # standin: Team B own fake services. live: the real Team A (policy search, safety, rules)
    team_a_url: str = "http://127.0.0.1:8001"  # TEAM_B_MODE=live: where Team A's service listens
    team_a_timeout_s: float = Field(default=10.0, gt=0)
    team_a_key: SecretStr | None = (
        None  # sent as X-Service-Key on every call to Team A (needed when Team A has service keys)
    )
    team_a_admin_key: SecretStr | None = (
        None  # X-Admin-Key for documents and rules (the knowledge pages); unset: those pages are off
    )
    # Team C's sandbox tools over MCP: the operator's JSON file (see config/team_c.example.json). Unset: the
    # stand-in shop serves every tool. Set: the tools it binds are served by Team C, the rest by the stand-in.
    team_c_config: Path | None = None
    config_dir: Path = PROJECT_ROOT / "config" / "tenants"
    data_dir: Path = PROJECT_ROOT / "data"
    fixtures_dir: Path = PROJECT_ROOT / "fixtures"
    store: StoreKind = "memory"
    db_path: Path = PROJECT_ROOT / "var" / "team_b.sqlite3"
    llm: LlmKind = "none"
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3:8b"
    openrouter_api_key: SecretStr | None = None
    llm_base_url: str | None = None  # TEAM_B_LLM=openai: any server speaking the OpenAI chat format (Groq, Gemini, ...)
    llm_api_key: SecretStr | None = None  # its key (never put in a file that is committed)
    llm_model: str | None = None  # overrides the default model of the chosen provider
    llm_timeout_s: float = Field(default=20.0, gt=0)
    llm_max_wait_s: float = Field(
        default=4.0, ge=0
    )  # a call waits at most this long for its rate-limit slot, else it falls back
    agent_mode: AgentMode = (
        "on"  # the default for businesses that do not set agent_mode; needs a model for the orchestrator job
    )
    agent_max_moves: int = Field(default=5, ge=1)  # asks of the orchestrator per message, then the usual stages run
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
    llm_rewrite: bool = False  # let the AI model reword replies (checked: it may not add any fact); needs TEAM_B_LLM
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
            raise ValueError("TEAM_B_LLM=openrouter needs OPENROUTER_API_KEY")
        if self.llm == "openai" and (not self.llm_base_url or not self.llm_model):
            raise ValueError(
                "TEAM_B_LLM=openai needs TEAM_B_LLM_BASE_URL and TEAM_B_LLM_MODEL (and usually TEAM_B_LLM_API_KEY)"
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
            raw["llm"] = "openai"  # a configured AI endpoint is used unless TEAM_B_LLM=none says otherwise
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
                    name = ROLE_ENV_VARS.get(sub, "TEAM_B_LLM_*_{ROLE}").format(ROLE=role.upper())
                else:
                    name = ENV_VARS.get(field, field or "settings")
                problems.append(f"{name}: {error['msg']}")
            raise SettingsError("; ".join(problems)) from None

    def role(self, name: str) -> RoleSettings:
        """The settings of one job (all unset when it has none of its own)."""
        return self.roles.get(name, RoleSettings())
