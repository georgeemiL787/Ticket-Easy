"""One AI model per job: selection, backup models, reasoning effort, and the free-plan rate limits (no real network)."""

import json
from typing import Any

import httpx
import pytest

from team_b.adapters.llm import OpenAICompatibleLLM
from team_b.adapters.llm_limits import RateBudgetExceeded
from team_b.adapters.llm_registry import LLMRegistry, RoleLLM, role_provider
from team_b.config import ROLES, Settings, SettingsError
from team_b.contracts.errors import UpstreamError
from team_b.observability import ai_events
from tests.fakes import FakeLLM

GROQ = {
    "TEAM_B_LLM": "openai",
    "TEAM_B_LLM_BASE_URL": "https://api.groq.com/openai/v1",
    "TEAM_B_LLM_API_KEY": "gsk-general",
    "TEAM_B_LLM_MODEL": "qwen/qwen3.8-27b",
}


def settings(**env: str) -> Settings:
    return Settings.from_env({**GROQ, **env})


def answer(content: str = '{"ok": true}', tokens: int | None = None) -> httpx.Response:
    body: dict[str, Any] = {"choices": [{"message": {"role": "assistant", "content": content}}]}
    if tokens is not None:
        body["usage"] = {"total_tokens": tokens}
    return httpx.Response(200, json=body)


class Server:
    """A fake provider: records every request, answers by model name."""

    def __init__(self, replies: dict[str, list[httpx.Response]] | None = None) -> None:
        self.requests: list[dict[str, Any]] = []
        self.keys: list[str] = []
        self.urls: list[str] = []
        self.replies = replies or {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        self.keys.append(request.headers.get("authorization", ""))
        self.urls.append(str(request.url))
        queue = self.replies.get(body["model"])
        return queue.pop(0) if queue else answer()

    def models(self) -> list[str]:
        return [r["model"] for r in self.requests]


def registry(server: Server, **env: str) -> LLMRegistry:
    return LLMRegistry(settings(**env), transport=httpx.MockTransport(server))


async def ask(llm: Any) -> dict[str, Any]:
    result: dict[str, Any] = await llm.complete_json(system="s", user="u", schema_hint={})
    return result


# ---- which model does which job ----


def test_with_no_settings_every_job_gets_the_general_client_as_before() -> None:
    reg = LLMRegistry(settings())
    default = reg.default()
    assert isinstance(default, OpenAICompatibleLLM)
    assert all(reg.llm_for(role) is default for role in ROLES)  # one and the same client: nothing changed


def test_without_an_ai_model_no_job_has_one() -> None:
    reg = LLMRegistry(Settings())
    assert reg.default() is None and reg.any_model() is False
    assert all(reg.llm_for(role) is None for role in ROLES)
    with pytest.raises(KeyError, match="unknown AI job"):
        reg.llm_for("poet")


async def test_each_job_can_use_its_own_model() -> None:
    server = Server()
    reg = registry(server, TEAM_B_LLM_MODEL_ORCHESTRATOR="openai/gpt-oss-120b", TEAM_B_LLM_MODEL_REVIEWER="m-review")
    for role in ("orchestrator", "reviewer", "reader", "writer"):
        await ask(reg.llm_for(role))
    assert server.models() == ["openai/gpt-oss-120b", "m-review", "qwen/qwen3.8-27b", "qwen/qwen3.8-27b"]


async def test_a_job_with_its_own_server_does_not_borrow_the_general_key() -> None:
    server = Server()
    reg = registry(
        server,
        TEAM_B_LLM_BASE_URL_WRITER_EGYPTIAN="http://127.0.0.1:11434/v1",
        TEAM_B_LLM_MODEL_WRITER_EGYPTIAN="nile-chat",
    )
    await ask(reg.llm_for("writer_egyptian"))
    await ask(reg.llm_for("writer"))
    assert server.urls == [
        "http://127.0.0.1:11434/v1/chat/completions",
        "https://api.groq.com/openai/v1/chat/completions",
    ]
    assert server.keys == ["", "Bearer gsk-general"]  # the Ollama call carries no key at all


async def test_a_job_can_have_its_own_key_on_the_general_server() -> None:
    server = Server()
    reg = registry(server, TEAM_B_LLM_API_KEY_JUDGE="gsk-judge")
    await ask(reg.llm_for("judge"))
    assert server.keys == ["Bearer gsk-judge"]


def test_a_job_can_have_a_model_when_there_is_no_general_one_only_with_its_own_server() -> None:
    only_model = Settings.from_env({"TEAM_B_LLM_MODEL_WRITER": "m"})
    assert LLMRegistry(only_model).llm_for("writer") is None  # a model name alone has no server to go to
    own = Settings.from_env({"TEAM_B_LLM_BASE_URL_WRITER": "http://x/v1", "TEAM_B_LLM_MODEL_WRITER": "m"})
    reg = LLMRegistry(own)
    assert reg.llm_for("writer") is not None and reg.llm_for("reader") is None and reg.any_model()
    assert role_provider(own, "writer") is not None and role_provider(own, "judge") is None


def test_settings_for_jobs_are_read_and_checked() -> None:
    s = settings(
        TEAM_B_LLM_FALLBACK_MODEL_READER="openai/gpt-oss-20b",
        TEAM_B_LLM_REASONING_EFFORT_ORCHESTRATOR="low",
        TEAM_B_LLM_RPM_WRITER="25",
        TEAM_B_LLM_TPM_WRITER="6000",
        TEAM_B_LLM_MAX_WAIT_S="2.5",
    )
    assert s.role("reader").fallback_model == "openai/gpt-oss-20b" and s.role("orchestrator").reasoning_effort == "low"
    assert (s.role("writer").rpm, s.role("writer").tpm, s.llm_max_wait_s) == (25, 6000, 2.5)
    assert s.role("judge").model is None  # nothing set: the general one will be used
    assert Settings().llm_max_wait_s == 4.0
    for bad, fragment in (
        ({"TEAM_B_LLM_RPM_READER": "0"}, "TEAM_B_LLM_RPM_READER"),
        ({"TEAM_B_LLM_REASONING_EFFORT_READER": "extreme"}, "TEAM_B_LLM_REASONING_EFFORT_READER"),
        ({"TEAM_B_LLM_BASE_URL_JUDGE": "http://x"}, "JUDGE.*needs its own model"),
    ):
        with pytest.raises(SettingsError, match=fragment):
            Settings.from_env(bad)


def test_the_api_keys_never_show_in_the_settings_text() -> None:
    s = settings(TEAM_B_LLM_API_KEY_JUDGE="gsk-secret-judge")
    assert "gsk-secret-judge" not in repr(s) and "gsk-general" not in repr(s)


# ---- reasoning effort ----


async def test_reasoning_effort_is_sent_only_when_set() -> None:
    server = Server()
    reg = registry(server, TEAM_B_LLM_REASONING_EFFORT_ORCHESTRATOR="low")
    await ask(reg.llm_for("orchestrator"))
    await ask(reg.llm_for("reader"))
    assert server.requests[0]["reasoning_effort"] == "low" and "reasoning_effort" not in server.requests[1]


async def test_a_model_that_rejects_reasoning_effort_is_asked_without_it_from_then_on() -> None:
    server = Server(
        {"m": [httpx.Response(400, text='{"error": "unknown parameter reasoning_effort"}'), answer(), answer()]}
    )
    reg = registry(server, TEAM_B_LLM_MODEL_ORCHESTRATOR="m", TEAM_B_LLM_REASONING_EFFORT_ORCHESTRATOR="low")
    llm = reg.llm_for("orchestrator")
    assert await ask(llm) == {"ok": True} and await ask(llm) == {"ok": True}
    assert ["reasoning_effort" in r for r in server.requests] == [True, False, False]


# ---- the backup model ----


async def test_a_model_that_is_gone_is_replaced_by_the_backup_and_then_left_alone() -> None:
    gone = httpx.Response(404, json={"error": {"message": "The model `qwen/preview` has been decommissioned"}})
    server = Server({"qwen/preview": [gone]})
    reg = registry(
        server, TEAM_B_LLM_MODEL_READER="qwen/preview", TEAM_B_LLM_FALLBACK_MODEL_READER="openai/gpt-oss-20b"
    )
    llm = reg.llm_for("reader")
    assert isinstance(llm, RoleLLM)
    with ai_events() as events:
        assert await ask(llm) == {"ok": True}
        assert await ask(llm) == {"ok": True}
    assert server.models() == [
        "qwen/preview",
        "openai/gpt-oss-20b",
        "openai/gpt-oss-20b",
    ]  # the dead model is not retried
    assert events[0][:2] == ("reader", "backup_model") and "openai/gpt-oss-20b" in events[0][2]
    assert "MODEL_NOT_FOUND" in events[0][2]


async def test_a_model_that_keeps_failing_is_skipped_for_a_while() -> None:
    down = httpx.Response(503, text="busy")
    server = Server({"main": [down, down, down, down, down, down, down, down, down]})
    now = [0.0]
    main = OpenAICompatibleLLM(base_url="http://x/v1", model="main", transport=httpx.MockTransport(server))
    backup = OpenAICompatibleLLM(base_url="http://x/v1", model="backup", transport=httpx.MockTransport(server))
    llm = RoleLLM("writer", main, backup, "backup", clock=lambda: now[0])
    for _ in range(3):
        await ask(llm)  # three failures in a row: each is answered by the backup
    before = len(server.models())
    await ask(llm)  # now the main model is not even tried
    assert server.models()[before:] == ["backup"]
    now[0] += 61  # a minute later the main model is tried again
    server.replies["main"] = [answer()]
    await ask(llm)
    assert server.models()[-1] == "main"


async def test_without_a_backup_the_error_goes_to_the_caller_and_the_trace_notes_it() -> None:
    llm = RoleLLM("reader", FakeLLM(UpstreamError("llm", "TIMEOUT", "slow", retryable=True)), None, "")
    with ai_events() as events, pytest.raises(UpstreamError):
        await ask(llm)
    assert events == [("reader", "failed", "TIMEOUT")]


async def test_a_bad_key_is_never_hidden_behind_the_backup() -> None:
    llm = RoleLLM("reader", FakeLLM(UpstreamError("llm", "UNAUTHORIZED", "no")), FakeLLM({"ok": 1}), "b")
    with ai_events() as events, pytest.raises(UpstreamError, match="UNAUTHORIZED"):
        await ask(llm)
    assert events == [("reader", "failed", "UNAUTHORIZED")]


async def test_when_the_backup_fails_too_the_job_is_marked_failed() -> None:
    llm = RoleLLM(
        "reader", FakeLLM(UpstreamError("llm", "TIMEOUT", "x")), FakeLLM(UpstreamError("llm", "TIMEOUT", "y")), "b"
    )
    with ai_events() as events, pytest.raises(UpstreamError):
        await ask(llm)
    assert events == [("reader", "failed", "TIMEOUT")]


# ---- the free-plan rate limits ----


async def test_jobs_on_the_same_model_share_one_budget_and_the_extra_call_is_refused_at_once() -> None:
    server = Server()
    reg = registry(server, TEAM_B_LLM_RPM_READER="2", TEAM_B_LLM_MAX_WAIT_S="0")
    await ask(reg.llm_for("reader"))
    await ask(reg.llm_for("knowledge"))  # a different job on the same model: the same budget
    with pytest.raises(RateBudgetExceeded):
        await ask(reg.llm_for("writer"))
    assert len(server.requests) == 2  # the third call never left the building


async def test_a_job_on_another_model_has_its_own_budget() -> None:
    server = Server()
    reg = registry(server, TEAM_B_LLM_RPM_READER="1", TEAM_B_LLM_MAX_WAIT_S="0", TEAM_B_LLM_MODEL_REVIEWER="m-review")
    await ask(reg.llm_for("reader"))
    await ask(reg.llm_for("reviewer"))
    await ask(reg.llm_for("reviewer"))  # unlimited
    with pytest.raises(RateBudgetExceeded):
        await ask(reg.llm_for("reader"))


async def test_the_token_count_the_server_returns_keeps_the_token_budget_honest() -> None:
    server = Server({"qwen/qwen3.8-27b": [answer(tokens=100), answer(tokens=100)]})
    reg = registry(server, TEAM_B_LLM_TPM_READER="1000", TEAM_B_LLM_MAX_WAIT_S="0")
    llm = reg.llm_for("reader")
    for _ in range(2):  # each call is estimated at about 400 tokens, so only two fit until the real counts replace them
        await ask(llm)
    await ask(llm)  # the real counts were 100 + 100: the third still fits


async def test_a_rate_limited_job_falls_back_to_its_backup_model_when_it_has_one() -> None:
    server = Server()
    reg = registry(
        server,
        TEAM_B_LLM_RPM_WRITER="1",
        TEAM_B_LLM_MAX_WAIT_S="0",
        TEAM_B_LLM_FALLBACK_MODEL_WRITER="openai/gpt-oss-20b",
    )
    llm = reg.llm_for("writer")
    with ai_events() as events:
        await ask(llm)
        await ask(llm)  # the main budget is used up: the backup answers (it has its own budget)
    assert server.models() == ["qwen/qwen3.8-27b", "openai/gpt-oss-20b"]
    assert events[0][:2] == ("writer", "backup_model") and "RATE_BUDGET" in events[0][2]


async def test_the_api_key_never_appears_in_an_error() -> None:
    server = Server({"qwen/qwen3.8-27b": [httpx.Response(401, text="bad key gsk-general")]})
    with pytest.raises(UpstreamError) as raised:
        await ask(registry(server).llm_for("reader"))
    assert "gsk-general" not in str(raised.value)


def test_several_keys_add_up_their_per_minute_budgets() -> None:
    env = {"TEAM_B_LLM_RPM_WRITER": "30", "TEAM_B_LLM_TPM_WRITER": "8000"}
    one = LLMRegistry(settings(**env))
    three = LLMRegistry(settings(TEAM_B_LLM_API_KEY="gsk-a,gsk-b, gsk-c", **env))
    url, model = GROQ["TEAM_B_LLM_BASE_URL"], GROQ["TEAM_B_LLM_MODEL"]
    first, sum_of_three = one._limiters.get(url, model), three._limiters.get(url, model)
    assert first is not None and sum_of_three is not None
    assert (first.rpm, first.tpm) == (30, 8000)
    assert (sum_of_three.rpm, sum_of_three.tpm) == (90, 24000)
