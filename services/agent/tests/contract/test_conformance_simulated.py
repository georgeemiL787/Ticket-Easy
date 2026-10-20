"""The simulated services pass the plug conformance checks (the same checks will run against the real services in Phase
6)."""

from datetime import date
from pathlib import Path

import pytest

from agent_service.adapters.memory_store import FixedClock
from agent_service.adapters.simulated.evidence import SimulatedEvidenceProvider
from agent_service.adapters.simulated.policy_search import SimulatedPolicySearch
from agent_service.adapters.simulated.rule_checker import SimulatedRuleChecker
from agent_service.adapters.simulated.safety_screen import SimulatedSafetyScreen
from agent_service.adapters.simulated.shop import SimulatedShop
from agent_service.config import Settings
from agent_service.ports import CapabilityClient, EvidenceProvider, PolicyGate
from tests.contract import conformance as conf

FIXTURES = Settings().fixtures_dir
CASE = conf.ConformanceCase()


@pytest.fixture
def search() -> SimulatedPolicySearch:
    return SimulatedPolicySearch(FIXTURES)


@pytest.fixture
def screen() -> SimulatedSafetyScreen:
    return SimulatedSafetyScreen(FIXTURES)


@pytest.fixture
def evidence(search: SimulatedPolicySearch, screen: SimulatedSafetyScreen) -> EvidenceProvider:
    return SimulatedEvidenceProvider(search, screen)


@pytest.fixture
def rules() -> SimulatedRuleChecker:
    return SimulatedRuleChecker(FIXTURES)


@pytest.fixture
def shop(tmp_path: Path) -> SimulatedShop:
    return SimulatedShop(FIXTURES, FixedClock(date(2026, 9, 28)), [CASE.tenant_id])


# ---- the simulated services are what the ports say they are ----


def test_the_simulated_services_satisfy_their_ports(
    evidence: EvidenceProvider, rules: SimulatedRuleChecker, shop: SimulatedShop
) -> None:
    assert (
        isinstance(evidence, EvidenceProvider) and isinstance(rules, PolicyGate) and isinstance(shop, CapabilityClient)
    )


# ---- EvidenceProvider: policy search and safety screen ----


@pytest.mark.parametrize(
    "check",
    [
        conf.check_search_returns_citations_or_a_reason,
        conf.check_search_with_no_match_says_why,
        conf.check_search_respects_top_k,
        conf.check_get_passage,
        conf.check_past_tickets,
        conf.check_classify_risk,
    ],
    ids=lambda f: f.__name__,
)
async def test_evidence_conformance(evidence: EvidenceProvider, check) -> None:  # type: ignore[no-untyped-def]
    await check(evidence, CASE)


async def test_evidence_search_failures_are_upstream_errors(
    evidence: EvidenceProvider, search: SimulatedPolicySearch
) -> None:
    await conf.check_upstream_failures_only(
        lambda: evidence.search_knowledge(CASE.tenant_id, CASE.query, request_id="r"),
        lambda: search.fail_next("search_knowledge"),
    )
    await conf.check_upstream_failures_only(
        lambda: evidence.get_passage(CASE.tenant_id, CASE.known_citation), lambda: search.fail_next("get_passage")
    )
    await conf.check_upstream_failures_only(
        lambda: evidence.search_past_tickets(CASE.tenant_id, CASE.query, request_id="r"),
        lambda: search.fail_next("search_past_tickets"),
    )


async def test_safety_screen_failures_are_upstream_errors(
    evidence: EvidenceProvider, screen: SimulatedSafetyScreen
) -> None:
    await conf.check_upstream_failures_only(
        lambda: evidence.classify_risk(CASE.tenant_id, CASE.risky_message, request_id="r"), screen.fail_next
    )


# ---- PolicyGate: the rule checker ----


@pytest.mark.parametrize(
    "check",
    [
        conf.check_gate_answers_every_valid_request,
        conf.check_gate_denies_a_write_without_identity,
        conf.check_gate_denies_another_tenants_record,
        conf.check_gate_human_approval_never_overrides_a_deny,
        conf.check_gate_is_deterministic,
    ],
    ids=lambda f: f.__name__,
)
async def test_policy_gate_conformance(rules: SimulatedRuleChecker, check) -> None:  # type: ignore[no-untyped-def]
    await check(rules, CASE)


async def test_rule_checker_failures_are_upstream_errors(rules: SimulatedRuleChecker) -> None:
    await conf.check_upstream_failures_only(lambda: rules.check_action(conf._request(CASE)), rules.fail_next)


# ---- CapabilityClient: the simulated shop ----


@pytest.mark.parametrize(
    "check",
    [
        conf.check_list_tools,
        conf.check_unknown_tool,
        conf.check_read_call,
        conf.check_write_needs_a_policy_request_id,
        conf.check_same_key_acts_once,
    ],
    ids=lambda f: f.__name__,
)
async def test_capability_client_conformance(shop: SimulatedShop, check) -> None:  # type: ignore[no-untyped-def]
    await check(shop, CASE)


async def test_shop_failures_are_upstream_errors(shop: SimulatedShop) -> None:
    request = conf.ToolCallRequest(
        request_id="r", tool=CASE.read_tool, arguments=CASE.read_arguments, idempotency_key="k"
    )
    await conf.check_upstream_failures_only(
        lambda: shop.call_tool(CASE.tenant_id, request),
        lambda: shop.fail_next(CASE.read_tool, "BACKEND_UNAVAILABLE"),
    )


# ---- the checks themselves catch a bad implementation ----


class LyingGate:
    """Allows everything and raises on odd input: both are conformance failures."""

    async def check_action(self, request):  # type: ignore[no-untyped-def]
        if request.facts.get("x"):
            raise KeyError("boom")
        from agent_service.contracts.policy import PolicyDecision

        return PolicyDecision(request_id=request.request_id, decision="allow", reason_code="ALWAYS")


async def test_a_gate_that_allows_everything_fails_conformance() -> None:
    with pytest.raises(KeyError):  # raises on the odd request
        await conf.check_gate_answers_every_valid_request(LyingGate(), CASE)  # type: ignore[arg-type]
    with pytest.raises(AssertionError, match="without a verified identity"):
        await conf.check_gate_denies_a_write_without_identity(LyingGate(), CASE)  # type: ignore[arg-type]
    with pytest.raises(AssertionError, match="approval must not turn a deny"):
        await conf.check_gate_human_approval_never_overrides_a_deny(LyingGate(), CASE)  # type: ignore[arg-type]


async def test_a_service_that_raises_the_wrong_error_fails_conformance() -> None:
    def break_it() -> None:
        return None

    async def call() -> None:
        raise ValueError("not an UpstreamError")

    with pytest.raises(AssertionError, match="must raise UpstreamError"):
        await conf.check_upstream_failures_only(call, break_it)

    async def quiet() -> None:
        return None

    with pytest.raises(AssertionError, match="did not happen"):
        await conf.check_upstream_failures_only(quiet, break_it)
