"""The failure table: what the agent does when a dependency fails. One case per row, with the stand-ins' switches.

Rows covered here: safety screen, rule checker, shop reads and writes, unpublished tools, rising frustration, out of
scope. (The policy search rows belong to Track B's matrix.) Every case also checks the global rules: no write the shop
did not apply on purpose, the trace of every turn is valid, and nothing is called done that the shop did not prove.
"""

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from team_b.container import Container, build_container, inject
from team_b.domain.trace import DecisionTrace
from tests.integration.scenario_runner import check_write_safety
from tests.support import make_settings

T = "shop_001"
RETURN = "I want to return order NS-20790, the size is wrong"
C101 = "01123456702"
REFUND = "I want a refund for order NS-20745"
C100 = "01012345601"


@dataclass
class Turn:
    say: str
    decision: str
    escalation: str | None = None
    awaiting: str | None = None
    contains: tuple[str, ...] = ()
    absent: tuple[str, ...] = ()


@dataclass
class Row:
    name: str
    turns: list[Turn]
    inject: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    changes: int = 0  # real changes in the shop at the end
    writes: int = 0  # write calls that reached the shop (changes, refusals, unclear ones)
    case_reason: str | None = None


def shop(tool: str, code: str, times: int = 1) -> tuple[str, dict[str, Any]]:
    return "shop", {"switch": "fail_next", "tool": tool, "code": code, "times": times}


UNHELPFUL = ("shipped", "delivered", "out for delivery", "on its way")

ROWS = [
    Row(
        "safety screen down: answers continue, actions are blocked",
        inject=[("safety_screen", {"switch": "fail_next", "times": 20})],
        turns=[
            Turn("hello", "answer"),
            Turn("Where is my order NS-20745? My phone is 01012345601", "answer", contains=("NS-20745",)),
            Turn(REFUND, "handoff", escalation="dependency_unavailable", awaiting="human"),
        ],
        case_reason="dependency_unavailable",
    ),
    Row(
        "rule checker down: nothing is written, handed off",
        inject=[("rule_checker", {"switch": "fail_next", "times": 2})],
        turns=[
            Turn(f"{RETURN}. My phone is {C101}", "handoff", escalation="dependency_unavailable", awaiting="human"),
        ],
        case_reason="dependency_unavailable",
    ),
    Row(
        "rule checker fails once: asked again, the customer is not bothered",
        inject=[("rule_checker", {"switch": "fail_next", "times": 1})],
        turns=[Turn(f"{RETURN}. My phone is {C101}", "confirm", awaiting="confirmation")],
    ),
    Row(
        "lookup fails once: retried in the same turn, success",
        inject=[shop("get_order", "BACKEND_UNAVAILABLE", 1)],
        turns=[Turn(f"Where is my order NS-20877? My phone is {C100}", "answer", contains=("NS-20877",))],
    ),
    Row(
        "lookup fails repeatedly: honest reply, then handoff repeated_tool_failure",
        inject=[shop("get_order", "BACKEND_UNAVAILABLE", 4)],
        turns=[
            Turn(f"Where is my order NS-20877? My phone is {C100}", "clarify", absent=UNHELPFUL),
            Turn("please try again", "handoff", escalation="repeated_tool_failure", awaiting="human", absent=UNHELPFUL),
        ],
        case_reason="repeated_tool_failure",
    ),
    Row(
        "write fails clearly: no retry, counted, handoff at the second failure",
        inject=[shop("create_return", "NOT_FOUND", 5)],
        turns=[
            Turn(f"{RETURN}. My phone is {C101}", "confirm"),
            Turn("yes", "refuse", contains=("Nothing was changed",), absent=("RET-",)),
            Turn(RETURN, "confirm"),
            Turn("yes", "handoff", escalation="repeated_tool_failure", awaiting="human"),
        ],
        writes=2,  # one attempt per proposal, never a retry
        case_reason="repeated_tool_failure",
    ),
    Row(
        "write cannot reach the shop: one attempt, a clear failure",
        inject=[shop("create_return", "BACKEND_UNAVAILABLE", 5)],
        turns=[Turn(f"{RETURN}. My phone is {C101}", "confirm"), Turn("yes", "refuse", absent=("RET-",))],
        writes=1,
    ),
    Row(
        "write outcome unknown: handoff unverified_result, never done or failed",
        inject=[("shop", {"switch": "uncertain", "tool": "create_refund", "applied": True})],
        turns=[
            Turn(f"{REFUND}. My phone is {C100}", "confirm"),
            Turn(
                "yes",
                "handoff",
                escalation="unverified_result",
                awaiting="human",
                absent=("has been refunded", "successfully", "failed", "REF-"),
            ),  # fmt: skip
        ],
        changes=1,  # it really happened once; the agent just may not say so
        writes=1,
        case_reason="unverified_result",
    ),
    Row(
        "write times out: unclear, handed to a person",
        inject=[shop("create_return", "TIMEOUT", 3)],
        turns=[
            Turn(f"{RETURN}. My phone is {C101}", "confirm"),
            Turn("yes", "handoff", escalation="unverified_result", awaiting="human"),
        ],
        writes=1,
        case_reason="unverified_result",
    ),
    Row(
        "write succeeds without an audit id: not claimed",
        inject=[("shop", {"switch": "no_audit", "tool": "create_refund"})],
        turns=[
            Turn(f"{REFUND}. My phone is {C100}", "confirm"),
            Turn("yes", "handoff", escalation="unverified_result", awaiting="human", absent=("REF-",)),
        ],
        changes=1,
        writes=1,
        case_reason="unverified_result",
    ),
    Row(
        "tool unpublished: capability_missing after identity, nothing written",
        inject=[("shop", {"switch": "unpublish", "tool": "create_return"})],
        turns=[
            Turn(RETURN, "verify_identity", awaiting="slot:phone"),
            Turn(C101, "handoff", escalation="capability_missing", awaiting="human"),
        ],
        case_reason="capability_missing",
    ),
    Row(
        "lookup tool unpublished: capability_missing, no order data",
        inject=[("shop", {"switch": "unpublish", "tool": "get_order"})],
        turns=[Turn(f"Where is my order NS-20877? My phone is {C100}", "handoff", escalation="capability_missing")],
        case_reason="capability_missing",
    ),
    Row(
        "high frustration on a repeat turn: handoff, the earlier checks still ran",
        turns=[
            Turn("Where is my order NS-20877? It is late", "verify_identity", awaiting="slot:phone"),
            Turn("I already asked, this is useless, why is it so slow?", "verify_identity", awaiting="slot:phone"),
            Turn(
                "This is ridiculous!!! Worst service ever, I am so angry, stop asking me questions!!!",
                "handoff",
                escalation="high_frustration",
                awaiting="human",
            ),  # fmt: skip
        ],
        case_reason="high_frustration",
    ),
    Row(
        "out of scope: refused politely with an offer of a person",
        turns=[Turn("Please book me a flight to Dubai", "refuse", contains=("person",))],
    ),
    Row(
        "wrong phone twice: handoff, no data",
        turns=[
            Turn("Where is my order NS-20877? My phone is 01099999999", "verify_identity", awaiting="slot:phone"),
            Turn("01088888888", "handoff", escalation="identity_failed", awaiting="human", absent=("Linen",)),
        ],
        case_reason="identity_failed",
    ),
    Row(
        "an order that is not theirs: handoff, no data",
        turns=[
            Turn(f"Where is my order NS-20877? My phone is {C100}", "answer"),
            Turn("and what about order NS-20790?", "handoff", escalation="ownership_mismatch", absent=("Polo",)),
        ],
        case_reason="ownership_mismatch",
    ),
]


@pytest.fixture
def container(tmp_path: Path) -> Container:
    return build_container(make_settings(tmp_path, fixed_today=date(2026, 9, 28)))


async def traces_of(container: Container, conversation: str) -> list[DecisionTrace]:
    return await container.traces.for_conversation(T, conversation)


@pytest.mark.parametrize("row", ROWS, ids=[r.name for r in ROWS])
async def test_failure_row(container: Container, row: Row) -> None:
    assert container.orchestrator is not None and container.shop is not None
    for plug, spec in row.inject:
        inject(container, plug, spec)
    conversation = "matrix"
    for number, turn in enumerate(row.turns, start=1):
        reply = await container.orchestrator.handle_turn(T, conversation, turn.say)
        trace = (await traces_of(container, conversation))[-1]
        where = f"turn {number} ({turn.say!r}): {reply.text[:100]!r}"
        assert reply.decision.value == turn.decision, where
        assert (trace.escalation_reason.value if trace.escalation_reason else None) == turn.escalation, where
        if turn.awaiting is not None:
            assert reply.awaiting == turn.awaiting, where
        for text in turn.contains:
            assert text.lower() in reply.text.lower(), where
        for text in turn.absent:
            assert text.lower() not in reply.text.lower(), where

    # the shop, not the agent's words, is the witness
    audit = container.shop.audit_log(T)
    kinds = {t.name: t.operation_kind for t in await container.capabilities.list_tools(T)}
    written = [e for e in audit if kinds.get(e.tool, "write") != "read"]
    assert container.shop.change_count(T) == row.changes, [(e.tool, e.status, e.applied) for e in audit]
    assert len(written) == row.writes
    assert len({e.idempotency_key for e in written if e.applied}) == sum(e.applied for e in written)  # none twice
    assert await check_write_safety(container, T, conversation, kinds) == []

    traces = await traces_of(container, conversation)
    last = traces[-1]
    assert (last.escalation_reason.value if last.escalation_reason else None) == (
        row.turns[-1].escalation if row.turns[-1].decision == "handoff" else None
    )
    session = await container.sessions.load(T, conversation)
    assert session is not None
    case = await container.cases.get(T, session.handoff_case_id) if session.handoff_case_id else None
    assert (case.package.reason.value if case else None) == row.case_reason


async def test_frustration_handoff_still_ran_every_earlier_check(container: Container) -> None:
    assert container.orchestrator is not None
    row = next(r for r in ROWS if r.name.startswith("high frustration"))
    for turn in row.turns:
        await container.orchestrator.handle_turn(T, "f", turn.say)
    stages = {s.stage: s.status for s in (await traces_of(container, "f"))[-1].steps}
    assert stages["understand"] == stages["risk_screen"] == stages["human_request"] == "ok"
    assert stages["frustration"] == "ok" and stages["handler"] == "skipped"  # decided before any work was started


async def test_every_row_has_a_distinct_name() -> None:
    assert len({r.name for r in ROWS}) == len(ROWS) >= 15
