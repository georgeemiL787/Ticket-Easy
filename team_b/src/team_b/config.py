"""Service settings, read from environment variables. Shop-specific settings live in config/tenants/ instead."""

import os
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, model_validator

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
    "llm_base_url": "TEAM_B_LLM_BASE_URL",
    "llm_api_key": "TEAM_B_LLM_API_KEY",
    "llm_model": "TEAM_B_LLM_MODEL",
    "llm_timeout_s": "TEAM_B_LLM_TIMEOUT_S",
    "fixed_today": "TEAM_B_FIXED_TODAY",
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
    "rate_limit_per_minute": "TEAM_B_RATE_LIMIT_PER_MIN",
}


class SettingsError(ValueError):
    """An environment variable has a value we cannot use. The message names the variable."""


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: Mode = "standin"  # standin: Team B own fake services. live: the real Team A / Team C (Phase 6)
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
    fixed_today: date | None = None  # pins the date for demos and tests, e.g. 2026-09-28
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
    rate_limit_per_minute: int = Field(default=20, ge=1)  # messages per conversation per minute (chat API)
    retention_days: int = Field(default=90, ge=1)  # older conversations, traces and finished cases are deleted

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
        try:
            return cls.model_validate(raw)
        except ValidationError as exc:
            problems = []
            for error in exc.errors():
                field = str(error["loc"][0]) if error["loc"] else ""
                name = ENV_VARS.get(field, field or "settings")
                problems.append(f"{name}: {error['msg']}")
            raise SettingsError("; ".join(problems)) from None
