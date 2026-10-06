"""Trace completeness, privacy of stored traces, and the structured log events."""

import json
from typing import Any

import pytest

from team_b.brain.pipeline import log_turn_events
from team_b.brain.redaction import contains_sensitive, redact, redact_mapping, redact_value, sensitive_kinds
from team_b.brain.turn import PlannedIntent, Step, TurnContext
from team_b.container import Container
from team_b.domain.decision import Decision
from team_b.domain.trace import DecisionTrace, ToolCallRecord, TraceStep, record_problems
from team_b.observability import EVENTS, configure_logging
from tests.integration.scenario_runner import check_record_and_privacy
from tests.unit.brain.test_pipeline import C, T, last_trace, orch, say


def trace(**over: Any) -> DecisionTrace:
    base: dict[str, Any] = {
        "trace_id": "t1",
        "request_id": "r1",
        "tenant_id": T,
        "conversation_id": C,
        "turn_index": 0,
        "decision": Decision.ANSWER,
        "versions": {"schema": "1.0", "tenant_config_hash": "abc", "lexicon_hash": "def"},
        "steps": [TraceStep(stage="load", status="ok", duration_ms=0.4)],
    }
    return DecisionTrace.model_validate({**base, **over})


# ---- the record is complete: durations and versions ----


def test_a_complete_trace_has_no_problems_and_skipped_steps_need_no_duration() -> None:
    skipped = TraceStep(stage="risk_screen", status="skipped", duration_ms=0.0)
    assert record_problems(trace(steps=[TraceStep(stage="load", status="ok", duration_ms=0.4), skipped])) == []


def test_a_step_that_ran_needs_a_duration() -> None:
    problems = record_problems(trace(steps=[TraceStep(stage="understand", status="ok", duration_ms=0.0)]))
    assert problems == ["step understand ran but has no duration_ms"]


@pytest.mark.parametrize("missing", ["schema", "tenant_config_hash", "lexicon_hash"])
def test_versions_must_name_the_schema_the_tenant_config_and_the_lexicon(missing: str) -> None:
    versions = {"schema": "1.0", "tenant_config_hash": "abc", "lexicon_hash": "def"}
    del versions[missing]
    assert record_problems(trace(versions=versions)) == [f"versions lacks {missing}"]


def test_an_llm_reading_and_a_rewrite_must_name_their_prompt_ids() -> None:
    base = {"schema": "1.0", "tenant_config_hash": "a", "lexicon_hash": "b"}
    assert record_problems(trace(nlu_method="llm", versions=base)) == ["versions lacks prompt (the NLU prompt id)"]
    assert record_problems(trace(nlu_method="llm", versions={**base, "prompt": "nlu_v1"})) == []
    rewrite = [TraceStep(stage="rewrite", status="used", duration_ms=1.0)]
    assert record_problems(trace(steps=rewrite, versions=base)) == ["versions lacks rewrite_prompt"]


async def test_every_stored_trace_is_complete(c: Container) -> None:
    o = orch(c, evidence=c.evidence)
    await say(o, "How many days do I have to return an item?")
    await say(o, "I want to talk to a human")
    for stored in await c.traces.for_conversation(T, C):
        assert record_problems(stored) == []
        assert {"schema", "tenant_config_hash", "lexicon_hash"} <= set(stored.versions)
        assert all(s.duration_ms > 0 for s in stored.steps if s.status != "skipped")


# ---- redaction ----


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("deliver to 15 Tahrir Street, Dokki", "address"),
        ("عنواني شارع التحرير ١٥ الدقي", "address"),
        ("3anwany share3 el tahrir 15", "address"),
        ("building 7 apt 12", "address"),
        ("call 01012345601", "phone"),
        ("mail a@b.co", "email"),
        ("card 4111 1111 1111 1111", "card"),
        ("my otp is 482913", "otp"),
    ],
)
def test_every_kind_of_personal_value_is_found_and_hidden(text: str, kind: str) -> None:
    assert kind in sensitive_kinds(text) and contains_sensitive(text)
    assert not contains_sensitive(redact(text))  # hiding is complete and idempotent


@pytest.mark.parametrize(
    "text",
    [
        "I paid 1250 EGP for order NS-20877",
        "refunds within 14 days of delivery",
        "Cairo and Giza in 2-3 days",
        "street-wear",
    ],
)
def test_ordinary_text_is_left_alone(text: str) -> None:
    assert redact(text) == text


def test_values_under_a_sensitive_key_are_hidden_whatever_they_look_like() -> None:
    assert redact_value("new_address", "El Nasr, block 5") == "[address]"
    assert redact_value("phone", "0 1 0") == "[phone]"
    assert redact_value("amount", "450") == "450" and redact_value("amount", 450) == 450
    assert redact_mapping(
        {"order_id": "NS-1", "address": "x", "nested": {"email": "a@b.co"}, "list": ["01012345601"]}
    ) == {
        "order_id": "NS-1",
        "address": "[address]",
        "nested": {"email": "[email]"},
        "list": ["[phone]"],
    }


async def test_a_conversation_full_of_personal_values_leaves_none_in_the_stored_traces(c: Container) -> None:
    o = orch(c, evidence=c.evidence)
    await say(o, "Where is order NS-20877? my phone is 01012345601, mail mona@example.com")
    await say(o, "my card is 4111 1111 1111 1111 and the code is 482913, deliver to 15 Tahrir Street, Dokki")
    for stored in await c.traces.for_conversation(T, C):
        assert contains_sensitive(stored.customer_message) is False
    stored = (await c.traces.for_conversation(T, C))[0]
    assert "[phone]" in stored.customer_message and "[email]" in stored.customer_message
    assert await check_record_and_privacy(c, T, C) == []


async def test_the_reply_text_and_the_tool_arguments_are_hidden_in_the_trace_only(c: Container) -> None:
    async def handler(ctx: TurnContext, planned: PlannedIntent) -> Step:
        ctx.tool_calls.append(
            ToolCallRecord(
                request_id="x",
                tool="verify_customer",
                operation_kind="read",
                status="success",
                arguments={"order_id": "NS-20877", "phone": "01012345601", "new_address": "15 Tahrir Street"},
            )  # fmt: skip
        )
        return Step(
            Decision.ANSWER, reason="echo 01012345601", reply_key="ask_generic", values={"slot": "15 Tahrir Street"}
        )

    reply = await say(orch(c, handlers={"knowledge": handler}), "What is your return policy?")
    assert "15 Tahrir Street" in reply.text  # the customer is not shown a hidden value
    stored = await last_trace(c)
    assert "[address]" in stored.response_text and "Tahrir" not in stored.response_text
    assert stored.decision_reason == "echo [phone]"
    assert stored.tool_calls[0].arguments == {"order_id": "NS-20877", "phone": "[phone]", "new_address": "[address]"}


async def test_the_privacy_scan_catches_a_leak(c: Container) -> None:
    await say(orch(c), "hello")
    leaky = (await c.traces.for_conversation(T, C))[0].model_copy(update={"response_text": "call 01012345601"})
    await c.traces.add(leaky.model_copy(update={"trace_id": "leak"}))
    assert any("PRIVACY" in p and "phone" in p for p in await check_record_and_privacy(c, T, C))


# ---- logs ----


def lines(capsys: pytest.CaptureFixture[str]) -> list[dict[str, Any]]:
    out = capsys.readouterr().out
    return [json.loads(line) for line in out.splitlines() if line.startswith("{")]


async def test_every_log_line_of_a_turn_carries_the_four_ids(c: Container, capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(json_logs=True)
    assert c.policy_search is not None
    c.policy_search.fail_next("search_knowledge", 2)
    reply = await say(orch(c, evidence=c.evidence), "What is your return policy?")
    written = lines(capsys)
    assert written
    for line in written:
        assert (line["tenant_id"], line["conversation_id"], line["request_id"], line["trace_id"]) == (
            T, C, reply.request_id, reply.trace_id,
        )  # fmt: skip
    events = [line["event"] for line in written]
    assert events[0] == "turn_start" and events[-1] == "turn_complete"
    assert {"dependency_error", "handoff_created"} <= set(events)
    done = written[-1]
    assert (done["decision"], done["escalation_reason"], done["handoff_case_id"]) == (
        "handoff", "dependency_unavailable", reply.handoff_case_id,
    )  # fmt: skip
    assert "return policy" not in json.dumps(written).lower()  # ids and codes only, never message text


async def test_the_ids_do_not_leak_into_the_next_turn(c: Container, capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(json_logs=True)
    o = orch(c)
    first, second = await say(o, "hello"), await say(o, "thanks")
    ids = {line["trace_id"] for line in lines(capsys)}
    assert ids == {first.trace_id, second.trace_id}


def test_policy_check_and_tool_call_lines_are_written_from_the_trace(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(json_logs=True)
    recorded = trace(
        policy=[{"request_id": "p1", "action": "create_refund", "decision": "deny", "reason_code": "WINDOW"}],
        tool_calls=[
            ToolCallRecord(
                request_id="c1", tool="get_order", operation_kind="read", status="error", error_code="TIMEOUT"
            )
        ],
    )
    log_turn_events(None, recorded)  # type: ignore[arg-type]
    written = lines(capsys)
    assert [line["event"] for line in written] == ["policy_check", "tool_call", "turn_complete"]
    assert written[0]["decision"] == "deny" and written[1]["error_code"] == "TIMEOUT"
    assert set(EVENTS) == {
        "turn_start",
        "turn_complete",
        "policy_check",
        "tool_call",
        "handoff_created",
        "dependency_error",
    }
