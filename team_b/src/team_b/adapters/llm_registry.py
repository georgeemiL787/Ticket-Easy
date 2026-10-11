"""One AI model per job, with a backup model and the provider's per-minute limits.

The jobs (config.ROLES): reader (understands the message), orchestrator, knowledge (policy search and answers), reviewer
(the policy agent), writer (words replies and small talk), writer_egyptian, summary (history and handoff summaries) and
judge (grades replies in the evaluation). Each reads its own TEAM_B_LLM_*_<ROLE> settings and, for anything it does not
set, the general TEAM_B_LLM_* ones: so with no per-job settings every job gets the same client as before.

llm_for(role) returns the client of a job, or None when the job has no AI model (the rules or templates are used):
- a job with a backup model (TEAM_B_LLM_FALLBACK_MODEL_<ROLE>) uses it when the main model is gone ("not found",
  "decommissioned") or keeps failing; a model that keeps failing is left alone for a minute before it is tried again;
- jobs on the same model share one RateLimiter, so together they stay inside the provider's requests and tokens per
  minute; a call that would wait longer than TEAM_B_LLM_MAX_WAIT_S raises RateBudgetExceeded at once, which every caller
  treats like any model error (rules or template), and the turn's trace says which job fell back and why.
"""

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeVar

import httpx

from team_b.adapters.llm import OpenAICompatibleLLM
from team_b.adapters.llm_limits import LimiterRegistry
from team_b.config import ROLES, Settings
from team_b.contracts.errors import UpstreamError
from team_b.observability import get_logger, record_ai_event
from team_b.ports import LLMClient

log = get_logger(__name__)
_T = TypeVar("_T")
OPENROUTER_URL = "https://openrouter.ai/api/v1"
OPENROUTER_DEFAULT_MODEL = "anthropic/claude-haiku-4.5"
FAILURES_BEFORE_BACKUP = 3  # in a row: then the backup is used first for a while
BACKUP_FIRST_FOR_S = 60.0
MODEL_GONE_FOR_S = 600.0  # a model the provider no longer has is not asked again for ten minutes


@dataclass(frozen=True)
class Provider:
    """Where a model is: the server, the model name, the key (None for none)."""

    base_url: str
    model: str
    api_key: str | None


def general_provider(settings: Settings) -> Provider | None:
    """The provider TEAM_B_LLM asks for, or None (no AI model)."""
    if settings.llm == "ollama":
        return Provider(settings.ollama_url.rstrip("/") + "/v1", settings.llm_model or settings.ollama_model, None)
    if settings.llm == "openrouter":
        key = settings.openrouter_api_key.get_secret_value() if settings.openrouter_api_key else None
        return Provider(OPENROUTER_URL, settings.llm_model or OPENROUTER_DEFAULT_MODEL, key)
    if settings.llm == "openai":
        assert settings.llm_base_url is not None and settings.llm_model is not None  # checked by Settings
        key = settings.llm_api_key.get_secret_value() if settings.llm_api_key else None
        return Provider(settings.llm_base_url, settings.llm_model, key)
    return None


def role_provider(settings: Settings, role: str) -> Provider | None:
    """The provider of one job: its own settings over the general ones. A job with its own server never borrows the
    general API key (a key belongs to the server it was issued for)."""
    own, general = settings.role(role), general_provider(settings)
    if own.base_url:
        assert own.model is not None  # checked by RoleSettings
        return Provider(own.base_url, own.model, own.api_key.get_secret_value() if own.api_key else None)
    if general is None:
        return None
    key = own.api_key.get_secret_value() if own.api_key else general.api_key
    return Provider(general.base_url, own.model or general.model, key)


def _text_of(client: LLMClient) -> Callable[..., Awaitable[str]]:
    text = getattr(client, "complete_text", None)
    if text is None:
        raise UpstreamError("llm", "BAD_REQUEST", "this AI model client cannot answer in plain text")
    return text  # type: ignore[no-any-return]


class RoleLLM:
    """The AI model of one job: the main model, and a backup for when the main one is gone or keeps failing."""

    def __init__(
        self,
        role: str,
        main: LLMClient,
        backup: LLMClient | None,
        backup_name: str,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._role, self._main, self._backup, self._backup_name = role, main, backup, backup_name
        self._clock = clock
        self._failures = 0
        self._skip_main_until = 0.0

    async def complete_json(
        self, *, system: str, user: str, schema_hint: dict[str, Any], temperature: float = 0.0
    ) -> dict[str, Any]:
        return await self._run(
            lambda c: c.complete_json(system=system, user=user, schema_hint=schema_hint, temperature=temperature)
        )

    async def complete_text(self, *, system: str, user: str, temperature: float = 0.0) -> str:
        """The model's answer as plain text (for a small model that cannot keep a JSON format)."""
        return await self._run(lambda c: _text_of(c)(system=system, user=user, temperature=temperature))

    async def _run(self, call: Callable[[LLMClient], Awaitable[_T]]) -> _T:
        if self._backup is not None and self._clock() < self._skip_main_until:
            return await self._ask_backup(call, "the main model is being skipped")
        try:
            answer = await call(self._main)
        except UpstreamError as exc:
            self._failures += 1
            if exc.code == "MODEL_NOT_FOUND":
                self._skip_main_until = self._clock() + MODEL_GONE_FOR_S
            elif self._failures >= FAILURES_BEFORE_BACKUP:
                self._skip_main_until = self._clock() + BACKUP_FIRST_FOR_S
            if self._backup is None or exc.code == "UNAUTHORIZED":
                record_ai_event(self._role, "failed", exc.code)
                raise
            return await self._ask_backup(call, exc.code)
        except Exception:
            record_ai_event(self._role, "failed", "INVALID_OUTPUT")  # nonsense: the caller uses its own fallback
            raise
        self._failures = 0
        return answer

    async def _ask_backup(self, call: Callable[[LLMClient], Awaitable[_T]], why: str) -> _T:
        assert self._backup is not None
        try:
            answer = await call(self._backup)
        except UpstreamError as exc:
            record_ai_event(self._role, "failed", exc.code)
            raise
        record_ai_event(self._role, "backup_model", f"{self._backup_name} ({why})")
        return answer


class LLMRegistry:
    """llm_for(role) -> the client of that job, or None. Built once from the settings."""

    def __init__(
        self,
        settings: Settings,
        *,
        wrapper: Callable[[LLMClient], LLMClient] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._wrapper = wrapper
        self._transport = transport
        self._limiters = LimiterRegistry(settings.llm_max_wait_s)
        self._clients: dict[tuple[str, str, str | None, str | None], LLMClient] = {}
        self._roles: dict[str, LLMClient | None] = {}
        for role in ROLES:  # first every job's numbers, so jobs on one model share the tightest budget
            provider = role_provider(settings, role)
            if provider is not None:
                own = settings.role(role)
                # the numbers are per key: with several keys (rotated one request after the other) the budget is the sum
                keys = max(1, len([k for k in (provider.api_key or "").split(",") if k.strip()]))
                rpm = own.rpm * keys if own.rpm is not None else None
                tpm = own.tpm * keys if own.tpm is not None else None
                self._limiters.add(provider.base_url, provider.model, rpm, tpm)
                if own.fallback_model:  # the backup model has its own budget, with the same numbers
                    self._limiters.add(provider.base_url, own.fallback_model, rpm, tpm)
        for role in ROLES:
            self._roles[role] = self._build(role)

    def llm_for(self, role: str) -> LLMClient | None:
        if role not in self._roles:
            raise KeyError(f"unknown AI job {role!r}: one of {', '.join(ROLES)}")
        return self._roles[role]

    def default(self) -> LLMClient | None:
        """The general AI model (TEAM_B_LLM_*), the one a job without its own settings uses."""
        provider = general_provider(self._settings)
        return self._client(provider, None) if provider is not None else None

    def any_model(self) -> bool:
        return any(client is not None for client in self._roles.values())

    # ---- building ----

    def _client(self, provider: Provider, effort: str | None) -> LLMClient:
        key = (provider.base_url, provider.model, provider.api_key, effort)
        if key not in self._clients:
            client: LLMClient = OpenAICompatibleLLM(
                base_url=provider.base_url,
                model=provider.model,
                api_key=provider.api_key,
                timeout_s=self._settings.llm_timeout_s,
                transport=self._transport,
                reasoning_effort=effort,
                limiter=self._limiters.get(provider.base_url, provider.model),
            )
            self._clients[key] = self._wrapper(client) if self._wrapper is not None else client
        return self._clients[key]

    def _build(self, role: str) -> LLMClient | None:
        provider = role_provider(self._settings, role)
        if provider is None:
            return None
        own = self._settings.role(role)
        main = self._client(provider, own.reasoning_effort)
        if not own.fallback_model:
            return main
        backup = Provider(provider.base_url, own.fallback_model, provider.api_key)
        return RoleLLM(role, main, self._client(backup, None), own.fallback_model)
