"""Conformance checks for the plugs: what any implementation of a port must do, stand-in or real service.

Each check is an async function that takes the object to test (and a ConformanceCase naming the demo data to use),
and fails with an AssertionError that says which rule was broken. They use only the port's public methods, so the same
functions run unchanged against the stand-ins now and against Team A's and Team C's real services in Phase 6.

Ports and what is checked:
  EvidenceProvider  search returns passages with citations or an empty_reason (never both, never neither); an unknown
                    citation gives None; classify_risk flags the risky message and not the ordinary one; a failing
                    service raises UpstreamError and nothing else.
  PolicyGate        check_action answers allow, deny or require_human with a reason_code for every valid request and
                    never raises; a write without a verified identity is denied; another tenant's record is denied;
                    a human approval never turns a deny into an allow; a failing service raises UpstreamError only.
  CapabilityClient  list_tools returns ToolSpecs with an input_schema; an unknown tool is TOOL_NOT_PUBLISHED or
                    NOT_FOUND; a write needs the policy request id; the same idempotency key never acts twice; a
                    failing service raises UpstreamError only.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from team_b.contracts.errors import UpstreamError
from team_b.contracts.policy import CheckActionRequest, PolicyDecision
from team_b.contracts.tools import ToolCallRequest, ToolResult, ToolSpec
from team_b.ports import CapabilityClient, EvidenceProvider, PolicyGate

DECISIONS = {"allow", "deny", "require_human"}
UNKNOWN_TOOL_CODES = {"TOOL_NOT_PUBLISHED", "NOT_FOUND"}


@dataclass(frozen=True)
class ConformanceCase:
    """The demo data the checks use. Defaults fit the Nile Style demo shop."""

    tenant_id: str = "shop_001"
    query: str = "what is the return policy"
    nonsense_query: str = "zzqx vwk plmn"
    known_citation: str = "return_policy@v2#s2"
    unknown_citation: str = "nothing@v9#s9"
    risky_message: str = "this is a scam and I will call my lawyer"
    clean_message: str = "I would like to return my order please"
    read_tool: str = "get_order"
    read_arguments: dict[str, Any] = field(default_factory=lambda: {"order_id": "NS-20877"})
    write_tool: str = "create_ticket"
    write_arguments: dict[str, Any] = field(
        default_factory=lambda: {"subject": "conformance", "description": "conformance check"}
    )
    write_action: str = "create_return"  # a write capability the rule checker has rules for
    write_facts: dict[str, Any] = field(default_factory=lambda: {"days_since_delivery": 3})


def _request(case: ConformanceCase, **over: Any) -> CheckActionRequest:
    base: dict[str, Any] = {
        "request_id": "conf-1", "tenant_id": case.tenant_id, "action": case.write_action,
        "tool": {"name": case.write_action, "operation_kind": "create", "risk": "medium"},
        "identity": {"verified": True, "customer_id": "C-100"}, "facts": dict(case.write_facts),
        "arguments": {}, "as_of": "2026-09-28",
    }  # fmt: skip
    return CheckActionRequest.model_validate({**base, **over})


async def check_upstream_failures_only(call: Callable[[], Awaitable[Any]], break_next_call: Callable[[], None]) -> None:
    """When the service fails, the caller sees UpstreamError (so it can hand off), never another exception."""
    break_next_call()
    try:
        await call()
    except UpstreamError:
        return
    except Exception as exc:  # noqa: BLE001 - any other type is the bug this check exists to catch
        raise AssertionError(f"a failing service must raise UpstreamError, not {type(exc).__name__}: {exc}") from exc
    raise AssertionError("the failure that was switched on did not happen")


# ---- EvidenceProvider ----


async def check_search_returns_citations_or_a_reason(provider: EvidenceProvider, case: ConformanceCase) -> None:
    found = await provider.search_knowledge(case.tenant_id, case.query, request_id="conf-1")
    assert found.passages or found.empty_reason, "a search result must have passages or an empty_reason"
    assert not (found.passages and found.empty_reason), "passages and empty_reason must not both be set"
    assert found.passages, f"the demo query {case.query!r} must find a passage"
    for passage in found.passages:
        assert passage.passage_id and passage.text.strip(), "every passage needs a citation id and text"
        assert passage.score == passage.score, "a score must be a number"  # not NaN
    scores = [p.score for p in found.passages]
    assert scores == sorted(scores, reverse=True), "passages must come best first"


async def check_search_with_no_match_says_why(provider: EvidenceProvider, case: ConformanceCase) -> None:
    found = await provider.search_knowledge(case.tenant_id, case.nonsense_query, request_id="conf-2")
    assert not found.passages and found.empty_reason, "an unanswerable query must return no passages and a reason"


async def check_search_respects_top_k(provider: EvidenceProvider, case: ConformanceCase) -> None:
    found = await provider.search_knowledge(case.tenant_id, case.query, request_id="conf-3", top_k=1)
    assert len(found.passages) <= 1, "top_k must limit the number of passages"


async def check_get_passage(provider: EvidenceProvider, case: ConformanceCase) -> None:
    passage = await provider.get_passage(case.tenant_id, case.known_citation)
    assert passage is not None and passage.passage_id == case.known_citation, "a known citation returns its passage"
    assert await provider.get_passage(case.tenant_id, case.unknown_citation) is None, "an unknown citation is None"


async def check_past_tickets(provider: EvidenceProvider, case: ConformanceCase) -> None:
    result = await provider.search_past_tickets(case.tenant_id, case.query, request_id="conf-4")
    assert result.tickets or result.empty_reason, "a past-ticket result must have tickets or an empty_reason"
    assert not (result.tickets and result.empty_reason), "tickets and empty_reason must not both be set"


async def check_classify_risk(provider: EvidenceProvider, case: ConformanceCase) -> None:
    risky = await provider.classify_risk(case.tenant_id, case.risky_message, request_id="conf-5")
    assert risky.flagged and risky.categories and risky.matched_terms, "a risky message must be flagged with why"
    clean = await provider.classify_risk(case.tenant_id, case.clean_message, request_id="conf-6")
    assert not clean.flagged and not clean.categories, "an ordinary request must not be flagged"


# ---- PolicyGate ----


def _assert_decision(decision: PolicyDecision, request: CheckActionRequest) -> None:
    assert decision.decision in DECISIONS, f"decision must be one of {sorted(DECISIONS)}, got {decision.decision!r}"
    assert decision.reason_code, "every decision needs a reason_code"
    assert decision.request_id == request.request_id, "the answer must carry the request_id it answers"


async def check_gate_answers_every_valid_request(gate: PolicyGate, case: ConformanceCase) -> None:
    """Odd but valid requests (missing facts, unknown action, wrong types) get an answer, never an error."""
    odd_facts: list[dict[str, Any]] = [{}, {"days_since_delivery": "soon"}, {"days_since_delivery": None}, {"x": [1]}]
    requests = [_request(case, facts=f) for f in odd_facts]
    requests += [
        _request(case, action="never_heard_of_it", tool={"name": "never_heard_of_it", "operation_kind": "update"}),
        _request(case, action="read_thing", tool={"name": "read_thing", "operation_kind": "read"}),
        _request(case, as_of=None),
        _request(case, risk_categories=["fraud_suspected"]),
        _request(case, arguments={"amount": "lots"}),
    ]
    for request in requests:
        _assert_decision(await gate.check_action(request), request)


async def check_gate_denies_a_write_without_identity(gate: PolicyGate, case: ConformanceCase) -> None:
    request = _request(case, identity={"verified": False})
    decision = await gate.check_action(request)
    _assert_decision(decision, request)
    assert decision.decision == "deny", "a write without a verified identity must be denied"


async def check_gate_denies_another_tenants_record(gate: PolicyGate, case: ConformanceCase) -> None:
    request = _request(case, resource_tenant_id="some_other_tenant")
    decision = await gate.check_action(request)
    assert decision.decision == "deny", "a record of another tenant must be denied"


async def check_gate_human_approval_never_overrides_a_deny(gate: PolicyGate, case: ConformanceCase) -> None:
    approval = {"approved_by": "agent_1", "case_id": "case_1", "approved_at": datetime(2026, 9, 28, tzinfo=UTC)}
    request = _request(case, identity={"verified": False}, human_approval=approval)
    assert (await gate.check_action(request)).decision == "deny", "an approval must not turn a deny into an allow"


async def check_gate_is_deterministic(gate: PolicyGate, case: ConformanceCase) -> None:
    request = _request(case)
    first, second = await gate.check_action(request), await gate.check_action(request)
    assert (first.decision, first.reason_code) == (second.decision, second.reason_code), "same request, same answer"


# ---- CapabilityClient ----


async def check_list_tools(client: CapabilityClient, case: ConformanceCase) -> list[ToolSpec]:
    tools = await client.list_tools(case.tenant_id)
    assert tools, "the shop must publish at least one tool"
    names = [t.name for t in tools]
    assert len(set(names)) == len(names), "tool names must be unique"
    for tool in tools:
        assert isinstance(tool, ToolSpec), "list_tools returns ToolSpec objects"
        assert tool.input_schema.get("type") == "object", f"{tool.name}: input_schema must describe an object"
        assert isinstance(tool.input_schema.get("properties", {}), dict), f"{tool.name}: input_schema needs properties"
        assert tool.capability, f"{tool.name}: a tool needs a capability name"
    return tools


async def check_unknown_tool(client: CapabilityClient, case: ConformanceCase) -> None:
    request = ToolCallRequest(request_id="conf-7", tool="no_such_tool", arguments={}, idempotency_key="conf-key-7")
    result = await client.call_tool(case.tenant_id, request)
    assert result.status == "error" and result.error_code in UNKNOWN_TOOL_CODES, (
        f"an unknown tool must answer one of {sorted(UNKNOWN_TOOL_CODES)}, got {result.error_code!r}"
    )
    assert not result.write_may_have_applied, "an unknown tool changes nothing"


async def check_read_call(client: CapabilityClient, case: ConformanceCase) -> None:
    request = ToolCallRequest(
        request_id="conf-8", tool=case.read_tool, arguments=case.read_arguments, idempotency_key="conf-key-8"
    )
    result = await client.call_tool(case.tenant_id, request)
    assert result.status == "success" and result.data, "a valid read returns data"
    assert result.audit_id, "every call leaves an audit id"


async def check_write_needs_a_policy_request_id(client: CapabilityClient, case: ConformanceCase) -> None:
    request = ToolCallRequest(
        request_id="conf-9", tool=case.write_tool, arguments=case.write_arguments, idempotency_key="conf-key-9"
    )
    result = await client.call_tool(case.tenant_id, request)
    assert result.status == "error" and not result.write_may_have_applied, (
        "a write without the policy request id that allowed it must be refused, and change nothing"
    )


async def check_same_key_acts_once(client: CapabilityClient, case: ConformanceCase) -> None:
    request = ToolCallRequest(
        request_id="conf-10", tool=case.write_tool, arguments=case.write_arguments, idempotency_key="conf-key-10",
        policy_request_id="conf-policy-1",
    )  # fmt: skip
    first = await client.call_tool(case.tenant_id, request)
    again = await client.call_tool(case.tenant_id, request.model_copy(update={"request_id": "conf-10b"}))
    assert write_result_is_verifiable(first), "a valid write succeeds with an audit id and a reference id"
    assert again.status == "success" and again.reference_id == first.reference_id, (
        "the same idempotency key must return the first result, not act again"
    )
    other_use = request.model_copy(
        update={"request_id": "conf-10c", "arguments": {**case.write_arguments, "subject": "x"}}
    )
    result = await client.call_tool(case.tenant_id, other_use)
    assert result.status == "error", "an idempotency key reused for different arguments must be refused"


def write_result_is_verifiable(result: ToolResult) -> bool:
    """What the brain needs before it may say "done": success, an audit id and a reference id."""
    return result.status == "success" and bool(result.audit_id) and bool(result.reference_id)
