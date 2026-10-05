"""Runs one scripted conversation against a fresh container and checks every expectation.

Failures are collected as plain sentences ("S07 turn 2 expect.decision: expected 'confirm', got 'answer'") so a red
scenario says what went wrong without a debugger. The write-safety check at the end runs for every active scenario.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from team_b.config import Settings
from team_b.container import Container, build_container, inject
from team_b.domain.handoff import HandoffCase
from team_b.domain.reply import AgentReply
from team_b.domain.trace import DecisionTrace
from tests.integration.scenario_format import Expect, Final, Inject, Scenario

HUMAN_ACTOR = "scenario-agent"
Customize = Callable[[Container], Container]


class OrchestratorLike(Protocol):
    """The interface the runner expects from container.orchestrator (human methods: see brain/orchestrator.py)."""

    async def handle_turn(self, tenant_id: str, conversation_id: str, text: str) -> AgentReply: ...


@dataclass
class ScenarioResult:
    scenario_id: str
    title: str
    status: str  # active | pending
    outcome: str  # passed | failed | skipped
    failures: list[str] = field(default_factory=list)
    final_decision: str | None = None
    escalation_reason: str | None = None
    skip_reason: str | None = None


@dataclass
class TurnOutcome:
    decision: str | None
    escalation: str | None
    awaiting: str | None
    locale: str | None
    citations: tuple[str, ...]
    text: str


def conversation_id_of(scenario: Scenario) -> str:
    return f"scn-{scenario.id}"


# ---- injecting failures ----


def inject_spec(item: Inject) -> dict[str, Any]:
    """Translate a scenario inject entry to the switch format of container.inject()."""
    if item.mode in ("fail", "timeout"):
        code = "TIMEOUT" if item.mode == "timeout" else (item.code or "BACKEND_UNAVAILABLE")
        key = "tool" if item.plug == "shop" else "operation"
        return {"switch": "fail_next", key: item.operation, "code": code, "times": item.times}
    if item.plug != "shop":
        raise ValueError(f"mode {item.mode!r} only exists for the shop plug")
    return {"switch": item.mode, "tool": item.operation}


# ---- checks ----


def check_expect(label: str, expect: Expect, outcome: TurnOutcome) -> list[str]:
    problems: list[str] = []
    given = expect.model_fields_set
    text = outcome.text.casefold()

    def compare(name: str, wanted: object, actual: object) -> None:
        if wanted != actual:
            problems.append(f"{label} expect.{name}: expected {wanted!r}, got {actual!r}")

    if "decision" in given:
        compare("decision", expect.decision, outcome.decision)
    if "escalation" in given:
        compare("escalation", expect.escalation, outcome.escalation)
    if "awaiting" in given:
        compare("awaiting", expect.awaiting, outcome.awaiting)
    if "locale" in given:
        compare("locale", expect.locale, outcome.locale)
    cited = list(outcome.citations)
    problems += [
        f"{label} expect.citations_include: {c!r} is missing, got {cited}"
        for c in expect.citations_include
        if c not in outcome.citations
    ]
    problems += [
        f"{label} expect.citations_exclude: {c!r} must not be cited, got {cited}"
        for c in expect.citations_exclude
        if c in outcome.citations
    ]
    problems += [
        f"{label} expect.text_contains: {p!r} not found in reply {outcome.text!r}"
        for p in expect.text_contains
        if p.casefold() not in text
    ]
    if expect.text_contains_any and not any(p.casefold() in text for p in expect.text_contains_any):
        problems.append(
            f"{label} expect.text_contains_any: none of {expect.text_contains_any} found in reply {outcome.text!r}"
        )
    problems += [
        f"{label} expect.text_not_contains: {p!r} must not appear, but the reply is {outcome.text!r}"
        for p in expect.text_not_contains
        if p.casefold() in text
    ]
    return problems


def write_audit(container: Container, tenant_id: str, tool_kinds: dict[str, str]) -> list[Any]:
    """Audit entries of calls that are not reads (a tool nobody knows counts as a write)."""
    assert container.shop is not None
    return [e for e in container.shop.audit_log(tenant_id) if tool_kinds.get(e.tool, "write") != "read"]


async def check_write_safety(
    container: Container, tenant_id: str, conversation_id: str, tool_kinds: dict[str, str]
) -> list[str]:
    """The global safety check, never optional: every write the shop saw is backed by a policy entry in this
    conversation's traces: allow, or require_human together with a human approval id."""
    traces = await container.traces.for_conversation(tenant_id, conversation_id)
    policies = {p.request_id: p for t in traces for p in t.policy}
    problems = []
    for entry in write_audit(container, tenant_id, tool_kinds):
        name = f"SAFETY: write #{entry.seq} {entry.tool} (request {entry.request_id})"
        policy = policies.get(entry.policy_request_id) if entry.policy_request_id else None
        if policy is None:
            problems.append(
                f"{name} has no matching policy entry in the conversation traces "
                f"(policy id {entry.policy_request_id!r})"
            )
        elif policy.decision == "deny":
            problems.append(f"{name} went ahead although the policy said deny")
        elif policy.decision == "require_human" and not entry.approval_id:
            problems.append(f"{name} went ahead on require_human without a human approval id")
    return problems


async def check_trace_invariants(
    container: Container, tenant_id: str, conversation_id: str, replies: list[AgentReply]
) -> list[str]:
    problems = []
    traces = await container.traces.for_conversation(tenant_id, conversation_id)
    for trace in traces:
        try:
            DecisionTrace.model_validate(trace.model_dump())
        except ValueError as exc:
            problems.append(f"trace {trace.trace_id} breaks an invariant: {exc}")
    stored = {t.trace_id: t for t in traces}
    for reply in replies:
        trace = stored.get(reply.trace_id)
        if trace is None:
            problems.append(f"reply {reply.request_id} points to trace {reply.trace_id}, which is not stored")
        elif trace.decision != reply.decision:
            problems.append(
                f"reply says {reply.decision.value} but its trace {trace.trace_id} says {trace.decision.value}"
            )
    return problems


async def case_of(container: Container, tenant_id: str, conversation_id: str) -> HandoffCase | None:
    cases = [c for c in await container.cases.list(tenant_id) if c.conversation_id == conversation_id]
    return cases[-1] if cases else None


async def check_final(
    final: Final,
    container: Container,
    tenant_id: str,
    conversation_id: str,
    tool_kinds: dict[str, str],
    replies: list[AgentReply],
    sid: str,
) -> list[str]:
    problems: list[str] = []
    given = final.model_fields_set
    assert container.shop is not None
    writes = write_audit(container, tenant_id, tool_kinds)

    def compare(name: str, wanted: object, actual: object) -> None:
        if wanted != actual:
            problems.append(f"{sid} final.{name}: expected {wanted!r}, got {actual!r}")

    if final.executed_tools is not None:
        done = [e.tool for e in writes if e.status == "success" and not e.replayed]
        compare("executed_tools", final.executed_tools, done)
    if final.audit_count is not None:
        compare("audit_count", final.audit_count, len(writes))
    if final.no_writes is not None:
        compare("no_writes", final.no_writes, not writes)
    case = await case_of(container, tenant_id, conversation_id)
    if "case_reason" in given:
        compare("case_reason", final.case_reason, case.package.reason.value if case else None)
    if "case_priority" in given:
        compare("case_priority", final.case_priority, case.package.priority if case else None)
    if final.case_has_pending_approval is not None:
        compare("case_has_pending_approval", final.case_has_pending_approval, bool(case and case.pending_approval))
    if final.trace_invariants:
        found = await check_trace_invariants(container, tenant_id, conversation_id, replies)
        problems += [f"{sid} final.trace_invariants: {p}" for p in found]
    return problems


# ---- the run ----


async def run_scenario(scenario: Scenario, tenant_id: str, *, customize: Customize | None = None) -> ScenarioResult:
    """Run an active scenario on a fresh container. A pending scenario is reported as skipped, not run.

    customize may wrap the container (tests use it to swap the orchestrator for a misbehaving one)."""
    sid = scenario.id
    result = ScenarioResult(sid, scenario.title, scenario.status, "passed")
    if scenario.status == "pending":
        result.outcome, result.skip_reason = "skipped", scenario.pending_reason
        return result

    container = build_container(Settings(fixed_today=scenario.setup.today))
    if customize is not None:
        container = customize(container)
    conversation_id = conversation_id_of(scenario)
    tool_kinds = {t.name: t.operation_kind for t in await container.capabilities.list_tools(tenant_id)}
    problems = result.failures
    replies: list[AgentReply] = []

    for number, item in enumerate(scenario.inject, start=1):
        try:
            inject(container, item.plug, inject_spec(item))
        except (ValueError, KeyError) as exc:
            problems.append(f"{sid} inject {number} ({item.plug}/{item.operation}/{item.mode}): {exc}")

    if not problems:
        await _play(scenario, container, tenant_id, conversation_id, replies, result)
        problems += await check_final(scenario.final, container, tenant_id, conversation_id, tool_kinds, replies, sid)
        problems += await check_write_safety(container, tenant_id, conversation_id, tool_kinds)

    last = await _latest_trace(container, tenant_id, conversation_id)
    if last is not None:
        result.final_decision = last.decision.value
        result.escalation_reason = last.escalation_reason.value if last.escalation_reason else None
    result.outcome = "failed" if problems else "passed"
    return result


async def _latest_trace(container: Container, tenant_id: str, conversation_id: str) -> DecisionTrace | None:
    traces = await container.traces.for_conversation(tenant_id, conversation_id)
    return traces[-1] if traces else None


async def _play(
    scenario: Scenario,
    container: Container,
    tenant_id: str,
    conversation_id: str,
    replies: list[AgentReply],
    result: ScenarioResult,
) -> None:
    orchestrator = container.orchestrator
    if orchestrator is None:
        result.failures.append(f"{scenario.id}: the container has no orchestrator")
        return
    clock: Any = container.clock
    for number, turn in enumerate(scenario.turns, start=1):
        label = f"{scenario.id} turn {number}"
        try:
            if turn.advance_days:
                clock.advance(turn.advance_days)
            if turn.human is not None:
                session = await container.sessions.load(tenant_id, conversation_id)
                case_id = session.handoff_case_id if session else None
                if case_id is None:
                    result.failures.append(f"{label} human.{turn.human.action}: the conversation has no handoff case")
                    return
                action = getattr(orchestrator, turn.human.action)
                await action(tenant_id, case_id, actor=HUMAN_ACTOR, text=turn.human.text)
            reply = None
            if turn.say is not None:
                reply = await orchestrator.handle_turn(tenant_id, conversation_id, turn.say)
                replies.append(reply)
            outcome = await _outcome(container, tenant_id, conversation_id, reply)
        except Exception as exc:  # a crash is a failed scenario with a message, not a broken test run
            result.failures.append(f"{label} raised {type(exc).__name__}: {exc}")
            return
        result.failures += check_expect(label, turn.expect, outcome)


async def _outcome(container: Container, tenant_id: str, conversation_id: str, reply: AgentReply | None) -> TurnOutcome:
    trace = await container.traces.get(tenant_id, reply.trace_id) if reply else None
    trace = trace or await _latest_trace(container, tenant_id, conversation_id)
    session = await container.sessions.load(tenant_id, conversation_id)
    escalation = trace.escalation_reason.value if trace and trace.escalation_reason else None
    if reply is not None:
        return TurnOutcome(
            reply.decision.value, escalation, reply.awaiting, reply.locale.value, reply.citations, reply.text
        )
    decision = trace.decision.value if trace else None
    return TurnOutcome(decision, escalation, session.awaiting if session else None, None, (), "")
