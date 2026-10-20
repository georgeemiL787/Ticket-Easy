"""Replay: a past conversation said again with other settings, on a fresh simulated shop, never touching the real
one."""

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from agent_service.api.app import create_app
from agent_service.api.auth import least_role
from agent_service.config import Settings
from agent_service.container import Container, build_container
from agent_service.domain.agent import AskCustomer
from agent_service.evaluation import replay
from tests.fakes import FunctionPlanner, ScriptedPlanner
from tests.integration.agent_expert import expert
from tests.support import make_settings

T = "shop_001"
PHONE = "01012345601"
TODAY = date(2026, 9, 28)
SAID = [f"Where is my order NS-20877? My phone is {PHONE}", "I want a refund for order NS-20877"]


@pytest.fixture
async def live(tmp_path: Path) -> Container:
    """A service with one stored conversation in it (the "real" one a replay must leave alone)."""
    container = build_container(make_settings(tmp_path, fixed_today=TODAY))
    assert container.orchestrator is not None
    for text in SAID:
        await container.orchestrator.handle_turn(T, "past", text)
    return container


def settings_of(live: Container) -> Settings:
    return live.settings


def snapshot(container: Container) -> tuple[Any, ...]:
    assert container.shop is not None
    return (len(container.shop.audit_log(T)), container.shop.change_count(T))


# ---- the changes ----


def test_changes_are_read_as_key_value_pairs() -> None:
    assert replay.parse_changes(["TEAM_B_AGENT_MODE=off", " TEAM_B_LLM_REWRITE = 1 "]) == {
        "TEAM_B_AGENT_MODE": "off",
        "TEAM_B_LLM_REWRITE": "1",
    }
    with pytest.raises(ValueError, match="KEY=VALUE"):
        replay.parse_changes(["nonsense"])


def test_changes_are_put_on_top_of_the_settings_and_a_jobs_settings_field_by_field(tmp_path: Path) -> None:
    base = Settings.from_env(
        {
            "TEAM_B_LLM": "openai",
            "TEAM_B_LLM_BASE_URL": "http://x/v1",
            "TEAM_B_LLM_MODEL": "m",
            "TEAM_B_LLM_MODEL_WRITER": "w1",
            "TEAM_B_LLM_RPM_WRITER": "20",
        }  # fmt: skip
    )
    changed = replay.apply_changes(base, {"TEAM_B_AGENT_MODE": "off", "TEAM_B_LLM_MODEL_WRITER": "w2"})
    assert changed.agent_mode == "off" and base.agent_mode != "off"  # the original is untouched
    assert changed.roles["writer"].model == "w2" and changed.roles["writer"].rpm == 20  # only the model changed
    assert changed.llm_model == "m"
    assert replay.apply_changes(base, {}) is base


# ---- the replay ----


async def test_the_customers_own_words_are_replayed_not_the_hidden_ones(live: Container) -> None:
    said, source = await replay.customer_messages(live, T, "past")
    assert source == "session" and said == SAID and PHONE in said[0]
    traces = await live.traces.for_conversation(T, "past")
    assert PHONE not in traces[0].customer_message  # the stored trace has it hidden


async def test_a_replay_with_a_different_picker_differs_where_expected(live: Container) -> None:
    ask = ScriptedPlanner(
        AskCustomer(question_key="ask_rephrase", why="unclear"), AskCustomer(question_key="ask_rephrase", why="unclear")
    )
    result = await replay.replay(
        live, settings_of(live), T, "past", {}, pickers=replay.Pickers(before=FunctionPlanner(expert), after=ask)
    )
    assert result.messages_from == "session" and len(result.before.turns) == len(result.after.turns) == 2
    assert result.before.turns[0].decision == "answer" and "NS-20877" in result.before.turns[0].reply
    assert result.after.turns[0].decision == "clarify" and result.after.turns[0].moves[0].kind == "ask_customer"
    assert result.differing_turns == [1, 2]
    assert result.before.turns[0].agent_mode == "on" and result.before.turns[0].moves[-1].kind == "reply"


async def test_the_same_settings_give_the_same_replies_so_nothing_differs(live: Container) -> None:
    result = await replay.replay(live, settings_of(live), T, "past", {})
    assert result.differing_turns == [] and result.before.writes == result.after.writes == 0


async def test_a_replay_never_touches_the_real_shop_or_the_real_stores(live: Container) -> None:
    before = snapshot(live)
    sessions = await live.sessions.load(T, "past")
    traces = await live.traces.for_conversation(T, "past")
    await replay.replay(live, settings_of(live), T, "past", {"TEAM_B_AGENT_MODE": "off"})
    assert snapshot(live) == before
    assert len(await live.traces.for_conversation(T, "past")) == len(traces)
    assert (await live.sessions.load(T, "past")) == sessions
    for chat in ("replay-before", "replay-after"):
        assert await live.traces.for_conversation(T, chat) == [] and await live.sessions.load(T, chat) is None


async def test_a_conversation_without_messages_is_not_found(live: Container) -> None:
    with pytest.raises(LookupError):
        await replay.replay(live, settings_of(live), T, "never-happened", {})


async def test_when_the_session_is_gone_the_stored_traces_are_used(live: Container) -> None:
    await live.sessions.purge_older_than(datetime(2100, 1, 1, tzinfo=UTC))  # retention removed it
    said, source = await replay.customer_messages(live, T, "past")
    assert source == "traces" and len(said) == 2 and PHONE not in said[0]


def test_the_report_puts_both_runs_side_by_side(tmp_path: Path) -> None:
    turn = replay.ReplayTurn(
        said="hello | there", reply="Hi!", decision="answer", escalation=None, awaiting=None, agent_mode="on",
        fallback_reason=None, moves=[replay.ReplayMove(kind="reply", summary="ok", refused=False)],
    )  # fmt: skip
    other = turn.model_copy(update={"reply": "Hello!", "decision": "clarify"})
    result = replay.ReplayResult(
        conversation_id="c1", tenant_id=T, messages_from="session", changes={"TEAM_B_AGENT_MODE": "off"},
        before=replay.ReplayRun(label="before", turns=[turn], writes=0),
        after=replay.ReplayRun(label="after", turns=[other], writes=0), differing_turns=[1],
    )  # fmt: skip
    path = replay.write_report(result, tmp_path, TODAY)
    text = path.read_text(encoding="utf-8")
    assert path.name == "replay_c1_2026-09-28.md" and "TEAM_B_AGENT_MODE=off" in text
    assert "| 1 | hello \\| there |" in text and "**answer**" in text and "**clarify**" in text and "differs" in text


# ---- the endpoint ----


async def test_the_replay_endpoint_returns_both_runs_and_needs_the_admin_role(live: Container) -> None:
    assert least_role("POST", "/v1/dashboard/conversations/past/replay") == "admin"
    app = create_app(container=live)
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test")
    before = snapshot(live)
    async with app.router.lifespan_context(app), http:
        ok = await http.post("/v1/dashboard/conversations/past/replay", json={"tenant_id": T, "changes": {}})
        missing = await http.post("/v1/dashboard/conversations/nope/replay", json={"tenant_id": T, "changes": {}})
        bad = await http.post(
            "/v1/dashboard/conversations/past/replay",
            json={"tenant_id": T, "changes": {"TEAM_B_AGENT_MODE": "sideways"}},
        )
    assert ok.status_code == 200
    body = ok.json()
    assert body["messages_from"] == "session" and len(body["before"]["turns"]) == 2 and body["differing_turns"] == []
    assert missing.status_code == 404 and bad.status_code == 422
    assert snapshot(live) == before
