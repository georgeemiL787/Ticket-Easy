"""The policy agent keeps the stricter of its route and the rule checker's, and fails closed (safety-critical)."""

from typing import Any

import pytest

from team_b.adapters.llm_policy_gate import LLMPolicyGate
from team_b.brain.llm_nlu import PROMPTS_DIR, PromptTemplate
from team_b.contracts.errors import UpstreamError
from team_b.contracts.evidence import Passage, RetrievalResult
from team_b.contracts.policy import CheckActionRequest, PolicyDecision
from tests.fakes import FakeLLM

PASSAGE = Passage(
    passage_id="refund_policy@v1#s1",
    document_id="refund_policy",
    version="v1",
    section="s1",
    language="en",
    text="Refunds are allowed within 14 days of delivery. Refunds above 1000 EGP need a manager.",
    score=0.9,
)


class Gate:
    def __init__(self, decision: str) -> None:
        self.decision = decision

    async def check_action(self, request: CheckActionRequest) -> PolicyDecision:
        return PolicyDecision(
            request_id=request.request_id, decision=self.decision, reason_code="ENGINE", citations=("rule#1",)
        )  # type: ignore[arg-type]


class Search:
    def __init__(self, passages: tuple[Passage, ...] = (PASSAGE,), error: Exception | None = None) -> None:
        self.passages, self.error = passages, error

    async def search_knowledge(self, *args: Any, **kwargs: Any) -> RetrievalResult:
        if self.error is not None:
            raise self.error
        if not self.passages:
            return RetrievalResult(empty_reason="below_threshold")
        return RetrievalResult(passages=self.passages)


def request(**over: Any) -> CheckActionRequest:
    base: dict[str, Any] = {
        "request_id": "req-1",
        "tenant_id": "shop_001",
        "action": "create_refund",
        "tool": {"name": "create_refund", "operation_kind": "create", "risk": "high"},
        "facts": {"days_since_delivery": 3},
        "arguments": {"order_id": "NS-20877", "reason": "damaged item"},
    }
    return CheckActionRequest.model_validate({**base, **over})


def verdict(decision: str, citations: list[str] | None = None, **extra: Any) -> dict[str, Any]:
    return {"decision": decision, "reasoning": "because", "citations": citations or [PASSAGE.passage_id], **extra}


def agent(engine: str, llm: FakeLLM, search: Search | None = None) -> LLMPolicyGate:
    return LLMPolicyGate(Gate(engine), search or Search(), llm)  # type: ignore[arg-type]


async def test_the_agent_can_tighten_an_allow_into_a_human_review() -> None:
    llm = FakeLLM(verdict("require_human", message_en="A colleague will check it.", message_ar="زميل هيراجعه."))
    out = await agent("allow", llm).check_action(request())
    assert out.decision == "require_human" and out.reason_code == "POLICY_AGENT_REQUIRE_HUMAN"
    assert out.user_message is not None and out.user_message.en == "A colleague will check it."
    assert PASSAGE.passage_id in out.citations and "rule#1" in out.citations


async def test_the_agent_can_refuse_what_the_rules_allow() -> None:
    out = await agent("allow", FakeLLM(verdict("deny"))).check_action(request())
    assert out.decision == "deny"


@pytest.mark.parametrize("engine", ["deny", "require_human"])
async def test_the_agent_never_loosens_the_rule_checker(engine: str) -> None:
    out = await agent(engine, FakeLLM(verdict("allow"))).check_action(request())
    assert out.decision == engine


async def test_a_deny_from_the_rules_does_not_even_ask_the_model() -> None:
    llm = FakeLLM()
    out = await agent("deny", llm).check_action(request())
    assert out.decision == "deny" and llm.calls == []


async def test_reads_are_not_reviewed() -> None:
    llm = FakeLLM()
    read = request(tool={"name": "get_order", "operation_kind": "read", "risk": "low"})
    assert (await agent("allow", llm).check_action(read)).decision == "allow" and llm.calls == []


async def test_an_allow_that_cites_no_passage_is_not_trusted() -> None:
    out = await agent("allow", FakeLLM(verdict("allow", citations=["made_up#1"]))).check_action(request())
    assert out.decision == "require_human"


async def test_a_message_with_an_invented_number_is_dropped() -> None:
    llm = FakeLLM(verdict("require_human", message_en="We pay 5000 EGP.", message_ar="هندفع 5000 جنيه."))
    out = await agent("allow", llm).check_action(request())
    assert out.decision == "require_human" and out.user_message is None


@pytest.mark.parametrize(
    "llm",
    [FakeLLM(UpstreamError("llm", "TIMEOUT", "slow")), FakeLLM({"decision": "maybe"}, {"nope": 1})],
)
async def test_when_the_model_fails_an_allowed_write_goes_to_a_person(llm: FakeLLM) -> None:
    out = await agent("allow", llm).check_action(request())
    assert out.decision == "require_human" and out.reason_code == "POLICY_AGENT_UNAVAILABLE"


async def test_when_the_search_fails_an_allowed_write_goes_to_a_person() -> None:
    search = Search(error=UpstreamError("team_a", "TIMEOUT", "slow"))
    out = await agent("allow", FakeLLM(), search).check_action(request())
    assert out.decision == "require_human"


async def test_with_no_policy_passage_the_rule_checker_decides_alone() -> None:
    llm = FakeLLM()
    out = await agent("allow", llm, Search(passages=())).check_action(request())
    assert out.decision == "allow" and llm.calls == []


async def test_a_recorded_human_approval_turns_the_agents_review_into_allow_but_not_a_deny() -> None:
    approval = {"approved_by": "sara", "case_id": "case-1", "approved_at": "2026-09-28T10:00:00Z"}
    allowed = await agent("allow", FakeLLM(verdict("require_human"))).check_action(request(human_approval=approval))
    assert allowed.decision == "allow"
    denied = await agent("allow", FakeLLM(verdict("deny"))).check_action(request(human_approval=approval))
    assert denied.decision == "deny"


async def test_a_low_risk_allow_needs_no_citation_but_a_risky_one_does() -> None:
    low = request(tool={"name": "create_ticket", "operation_kind": "create", "risk": "low"})
    out = await agent("allow", FakeLLM(verdict("allow", citations=["none#1"]))).check_action(low)
    assert out.decision == "allow"


# ---- prompt v2: a person is asked only when a person can add something ----


def test_the_gate_uses_the_reviewer_prompt_and_the_old_policy_prompts_are_kept() -> None:
    gate = agent("allow", FakeLLM())
    assert gate.prompt_version == "reviewer_v1"
    assert (PROMPTS_DIR / "policy_agent_v1.md").is_file()


def test_v2_asks_a_person_only_in_the_three_named_cases() -> None:
    system = PromptTemplate.load("policy_agent_v2").system
    assert "When in doubt" not in system and "never allow" not in system  # v1's catch-all is gone
    assert "Being unsure is not a reason on its own" in system
    for case in ("(a) a fact", "(b) the passages themselves name an exception", "(c) the amount is above"):
        assert case in system
    assert "name any other option" in system  # a deny says why and names the alternatives the passages allow
    assert "When in doubt" in PromptTemplate.load("policy_agent_v1").system  # the old version is untouched


async def test_a_borderline_case_the_passages_decide_is_denied_with_the_alternative_not_sent_to_a_person() -> None:
    llm = FakeLLM(
        verdict(
            "deny",
            message_en="Refunds are allowed within 14 days of delivery, but an exchange may be possible.",
            message_ar="الاسترداد خلال 14 يوم من الاستلام، لكن ممكن نشوف استبدال.",
        )
    )
    out = await agent("allow", llm).check_action(request(facts={"days_since_delivery": 15}))
    assert out.decision == "deny" and out.reason_code == "POLICY_AGENT_DENY"
    assert out.user_message is not None and "exchange" in out.user_message.en
    assert "Being unsure is not a reason on its own" in llm.calls[0]["system"]  # the model was given v2


async def test_a_case_the_passages_decide_as_allowed_stays_allowed() -> None:
    out = await agent("allow", FakeLLM(verdict("allow"))).check_action(request(facts={"days_since_delivery": 14}))
    assert out.decision == "allow"


async def test_a_real_exception_case_still_goes_to_a_person() -> None:
    llm = FakeLLM(
        verdict(
            "require_human",
            message_en="A colleague will look at it.",
            message_ar="زميل هيبص على الموضوع.",
        )
    )
    out = await agent("allow", llm).check_action(request(arguments={"order_id": "NS-20877", "reason": "damaged item"}))
    assert out.decision == "require_human" and out.reason_code == "POLICY_AGENT_REQUIRE_HUMAN"


@pytest.mark.parametrize("engine", ["deny", "require_human"])
async def test_v2_cannot_loosen_a_rule_checker_deny_or_hold(engine: str) -> None:
    out = await agent(engine, FakeLLM(verdict("allow"))).check_action(request())
    assert out.decision == engine


async def test_v2_does_not_trust_an_allow_that_cites_nothing() -> None:
    out = await agent("allow", FakeLLM(verdict("allow", citations=["made_up#1"]))).check_action(request())
    assert out.decision == "require_human"
