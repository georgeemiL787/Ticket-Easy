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
LlmKind = Literal["none", "ollama", "openrouter"]

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
    "llm_model": "TEAM_B_LLM_MODEL",
    "llm_timeout_s": "TEAM_B_LLM_TIMEOUT_S",
    "fixed_today": "TEAM_B_FIXED_TODAY",
    "capability_ttl_s": "TEAM_B_CAPABILITY_TTL_S",
    "log_json": "TEAM_B_LOG_JSON",
    "retention_days": "TEAM_B_RETENTION_DAYS",
    "llm_rewrite": "TEAM_B_LLM_REWRITE",
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
    llm_model: str | None = None  # overrides the default model of the chosen provider
    llm_timeout_s: float = Field(default=20.0, gt=0)
    fixed_today: date | None = None  # pins the date for demos and tests, e.g. 2026-09-28
    capability_ttl_s: float = Field(default=60.0, ge=0)  # how long the list of shop tools is cached
    log_json: bool = True
    llm_rewrite: bool = False  # let the AI model reword replies (checked: it may not add any fact); needs TEAM_B_LLM
    retention_days: int = Field(default=90, ge=1)  # older conversations, traces and finished cases are deleted

    @model_validator(mode="after")
    def _openrouter_needs_a_key(self) -> Self:
        if self.llm == "openrouter" and self.openrouter_api_key is None:
            raise ValueError("TEAM_B_LLM=openrouter needs OPENROUTER_API_KEY")
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
