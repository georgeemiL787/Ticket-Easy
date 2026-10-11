"""The stand-ins pass the plug conformance checks (the same checks will run against the real services in Phase 6)."""

from datetime import date
from pathlib import Path

import pytest

from team_b.adapters.memory_store import FixedClock
from team_b.adapters.standins.evidence import StandinEvidenceProvider
from team_b.adapters.standins.policy_search import PolicySearchStandin
from team_b.adapters.standins.rule_checker import RuleCheckerStandin
from team_b.adapters.standins.safety_screen import SafetyScreenStandin
from team_b.adapters.standins.shop import StandinShop
from team_b.config import Settings
from team_b.ports import CapabilityClient, EvidenceProvider, PolicyGate
from tests.contract import conformance as conf

FIXTURES = Settings().fixtures_dir
CASE = conf.ConformanceCase()


@pytest.fixture
def search() -> PolicySearchStandin:
    return PolicySearchStandin(FIXTURES)


@pytest.fixture
def screen() -> SafetyScreenStandin:
    return SafetyScreenStandin(FIXTURES)


@pytest.fixture
def evidence(search: PolicySearchStandin, screen: SafetyScreenStandin) -> EvidenceProvider:
    return StandinEvidenceProvider(search, screen)


@pytest.fixture
def rules() -> RuleCheckerStandin:
    return RuleCheckerStandin(FIXTURES)


@pytest.fixture
def shop(tmp_path: Path) -> StandinShop:
    return StandinShop(FIXTURES, FixedClock(date(2026, 9, 28)), [CASE.tenant_id])


# ---- the stand-ins are what the ports say they are ----


def test_the_standins_satisfy_their_ports(
    evidence: EvidenceProvider, rules: RuleCheckerStandin, shop: StandinShop
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
    evidence: EvidenceProvider, search: PolicySearchStandin
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
    evidence: EvidenceProvider, screen: SafetyScreenStandin
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
async def test_policy_gate_conformance(rules: RuleCheckerStandin, check) -> None:  # type: ignore[no-untyped-def]
    await check(rules, CASE)


async def test_rule_checker_failures_are_upstream_errors(rules: RuleCheckerStandin) -> None:
    await conf.check_upstream_failures_only(lambda: rules.check_action(conf._request(CASE)), rules.fail_next)


# ---- CapabilityClient: the stand-in shop ----


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
async def test_capability_client_conformance(shop: StandinShop, check) -> None:  # type: ignore[no-untyped-def]
    await check(shop, CASE)


async def test_shop_failures_are_upstream_errors(shop: StandinShop) -> None:
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
        from team_b.contracts.policy import PolicyDecision

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
