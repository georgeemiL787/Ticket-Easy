"""The Team A HTTP adapters against a fake server (httpx.MockTransport) that replays Team A's recorded examples."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from team_b.adapters.team_a_http import HttpEvidenceProvider, HttpPolicyGate, TeamAClient
from team_b.contracts.errors import UpstreamError
from team_b.contracts.policy import CheckActionRequest
from team_b.ports import EvidenceProvider, PolicyGate

EXAMPLES = Path(__file__).resolve().parents[4] / "team_a" / "contracts" / "examples"
Handler = Callable[[httpx.Request], httpx.Response]

pytestmark = pytest.mark.skipif(not EXAMPLES.is_dir(), reason="team_a/contracts/examples is not in this checkout")


def recorded(name: str) -> httpx.Response:
    response = json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf-8"))["response"]
    return httpx.Response(response["status"], json=response["body"])


def team_a(handler: Handler) -> TeamAClient:
    return TeamAClient("http://team-a.test/", timeout_s=1.0, transport=httpx.MockTransport(handler))


def evidence(handler: Handler) -> HttpEvidenceProvider:
    return HttpEvidenceProvider(team_a(handler))


def gate(handler: Handler) -> HttpPolicyGate:
    return HttpPolicyGate(team_a(handler))


def request(**over: Any) -> CheckActionRequest:
    base: dict[str, Any] = {
        "request_id": "req-1",
        "tenant_id": "shop_001",
        "action": "create_refund",
        "tool": {"name": "create_refund", "operation_kind": "create", "risk": "high"},
        "identity": {"verified": True, "customer_id": "C-100", "method": "phone+order_id"},
        "facts": {"order_status": "delivered", "delivered_at": "2026-09-08"},
        "arguments": {"order_id": "NS-20877", "amount": 800},
        "as_of": "2026-09-28",
    }
    return CheckActionRequest.model_validate({**base, **over})


def test_they_satisfy_the_ports() -> None:
    assert isinstance(evidence(lambda r: recorded("search_knowledge.success")), EvidenceProvider)
    assert isinstance(gate(lambda r: recorded("check_action.allow")), PolicyGate)


# ---- evidence ----


async def test_search_knowledge_sends_team_as_request_and_reads_its_passages() -> None:
    seen: list[httpx.Request] = []

    def handler(r: httpx.Request) -> httpx.Response:
        seen.append(r)
        return recorded("search_knowledge.success")

    result = await evidence(handler).search_knowledge(
        "shop_001", "momken araga3?", request_id="req-2", conversation_id="conv-42", top_k=3
    )
    assert seen[0].method == "POST" and seen[0].url.path == "/v1/knowledge/search"
    assert json.loads(seen[0].content) == {
        "request_id": "req-2",
        "tenant_id": "shop_001",
        "conversation_id": "conv-42",
        "query": "momken araga3?",
        "top_k": 3,
    }
    assert result.passages and result.passages[0].citation == "return_policy@v2#s2" and result.empty_reason is None


async def test_search_knowledge_with_no_evidence_says_why() -> None:
    result = await evidence(lambda r: recorded("search_knowledge.empty")).search_knowledge(
        "shop_001", "zzz", request_id="r"
    )
    assert not result.passages and result.empty_reason is not None


async def test_get_passage_found_and_missing() -> None:
    seen: list[httpx.Request] = []

    def found(r: httpx.Request) -> httpx.Response:
        seen.append(r)
        return recorded("get_passage.success")

    passage = await evidence(found).get_passage("shop_001", "return_policy@v2#s2")
    assert passage is not None and seen[0].url.params["citation"] == "return_policy@v2#s2"
    assert seen[0].url.params["tenant_id"] == "shop_001"
    assert await evidence(lambda r: recorded("get_passage.failure")).get_passage("shop_001", "x@v9#s9") is None


async def test_past_tickets() -> None:
    found = await evidence(lambda r: recorded("search_past_tickets.success")).search_past_tickets(
        "shop_001", "el order et2akhar", request_id="r"
    )
    assert found.tickets
    none = await evidence(lambda r: recorded("search_past_tickets.empty")).search_past_tickets(
        "shop_001", "zzz", request_id="r"
    )
    assert not none.tickets and none.empty_reason is not None


async def test_classify_risk_flagged_and_clear() -> None:
    flagged = await evidence(lambda r: recorded("classify_risk.flagged")).classify_risk("shop_001", "x", request_id="r")
    assert flagged.flagged and flagged.categories and flagged.matched_terms
    clear = await evidence(lambda r: recorded("classify_risk.clear")).classify_risk("shop_001", "x", request_id="r")
    assert not clear.flagged and clear.categories == ()


# ---- rule checker ----


async def test_check_action_is_sent_in_team_as_format() -> None:
    seen: list[httpx.Request] = []

    def handler(r: httpx.Request) -> httpx.Response:
        seen.append(r)
        return recorded("check_action.deny")

    await gate(handler).check_action(request(risk_categories=["legal"]))
    assert seen[0].url.path == "/v1/policy/check-action"
    assert json.loads(seen[0].content) == {
        "request_id": "req-1",
        "tenant_id": "shop_001",
        "conversation_id": None,
        "tool": {"name": "create_refund", "operation_kind": "create", "risk": "high", "exposes_personal_data": True},
        "arguments": {"order_id": "NS-20877", "amount": 800},
        "facts": {"order_status": "delivered", "delivered_at": "2026-09-08"},
        "identity": {"verified": True, "customer_id": "C-100", "method": "phone+order_id"},
        "resource_tenant_id": None,
        "risk_categories": ["legal"],
        "as_of": "2026-09-28",
    }


@pytest.mark.parametrize(
    ("name", "decision"),
    [
        ("check_action.allow", "allow"),
        ("check_action.deny", "deny"),
        ("check_action.require_human", "require_human"),
        ("check_action.missing_context", "deny"),
    ],
)
async def test_check_action_reads_every_recorded_answer(name: str, decision: str) -> None:
    result = await gate(lambda r: recorded(name)).check_action(request())
    assert result.decision == decision and result.reason_code


async def test_a_human_approval_turns_require_human_into_allow() -> None:
    approval = {"approved_by": "Sara", "case_id": "case-1", "approved_at": "2026-09-28T10:00:00Z"}
    result = await gate(lambda r: recorded("check_action.require_human")).check_action(request(human_approval=approval))
    assert result.decision == "allow" and result.reason_code == "APPROVED_BY_HUMAN" and "Sara" in result.rationale


async def test_a_human_approval_never_overrides_a_deny() -> None:
    approval = {"approved_by": "Sara", "case_id": "case-1", "approved_at": "2026-09-28T10:00:00Z"}
    result = await gate(lambda r: recorded("check_action.deny")).check_action(request(human_approval=approval))
    assert result.decision == "deny"


# ---- failures: always UpstreamError, never a made-up answer ----


def always(response: httpx.Response) -> Handler:
    return lambda r: response


def error(status: int, code: str) -> httpx.Response:
    return httpx.Response(status, json={"error": {"code": code, "message": "m", "request_id": None}})


@pytest.mark.parametrize(
    ("response", "code", "retryable"),
    [
        (error(503, "INDEX_NOT_BUILT"), "BACKEND_UNAVAILABLE", True),
        (httpx.Response(500, text="boom"), "BACKEND_UNAVAILABLE", True),
        (error(401, "UNAUTHORIZED"), "UNAUTHORIZED", False),
        (error(404, "TENANT_NOT_FOUND"), "TENANT_NOT_FOUND", False),
        (error(422, "INVALID_REQUEST"), "BAD_REQUEST", False),
        (httpx.Response(200, text="<html>not json</html>"), "BAD_RESPONSE", False),
        (httpx.Response(200, json={"passages": "nope"}), "BAD_RESPONSE", False),
    ],
)
async def test_failures_become_upstream_errors(response: httpx.Response, code: str, retryable: bool) -> None:
    with pytest.raises(UpstreamError) as caught:
        await evidence(always(response)).search_knowledge("shop_001", "q", request_id="r")
    assert caught.value.service == "team_a" and caught.value.code == code and caught.value.retryable is retryable
    with pytest.raises(UpstreamError):
        await gate(always(response)).check_action(request())


async def test_a_missing_tenant_on_get_passage_is_an_error_but_a_missing_passage_is_not() -> None:
    with pytest.raises(UpstreamError, match="TENANT_NOT_FOUND"):
        await evidence(always(error(404, "TENANT_NOT_FOUND"))).get_passage("nope", "a@v1#s1")


async def test_unreachable_and_slow_services_are_retryable_upstream_errors() -> None:
    def refuse(r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    def slow(r: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    for handler, code in ((refuse, "BACKEND_UNAVAILABLE"), (slow, "TIMEOUT")):
        with pytest.raises(UpstreamError) as caught:
            await gate(handler).check_action(request())
        assert caught.value.code == code and caught.value.retryable
