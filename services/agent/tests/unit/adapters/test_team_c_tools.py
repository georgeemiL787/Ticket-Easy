"""TeamCCapabilities: Team C's sandbox tools over MCP, against a scripted fake server (tests/fake_team_c_mcp.py).

What must hold: a bound tool is served only while Team C lists it (never by the stand-in), with the business's own
ToolSpec; the identity comes from the verified customer, never from arguments; only `succeeded` is a success; partial,
outcome_unknown, error and a lost answer are never reported as "not done"; tool_not_published drops the cached list.
"""

import json
import sys
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from agent_service.adapters.memory_store import FixedClock
from agent_service.adapters.standins.shop import StandinShop
from agent_service.adapters.team_c_tools import TeamCCapabilities, TeamCConfig
from agent_service.config import PROJECT_ROOT
from agent_service.container import Container, build_container
from agent_service.contracts.errors import UpstreamError
from agent_service.contracts.tools import ToolCallRequest
from tests.support import make_settings

FAKE = Path(__file__).resolve().parents[2] / "fake_team_c_mcp.py"
T = "shop_001"
BUSINESS = "biz-nile"
GET_ORDER_PROPOSAL = "prop-order"
VOUCHER_PROPOSAL = "prop-voucher"
ORDER = {
    "order_id": "NS-20877", "customer_id": "C-100", "order_status": "shipped", "payment_status": "paid",
    "order_total": 890, "placed_at": "2026-09-20", "shipped_at": "2026-09-22", "delivered_at": None,
    "expected_delivery_date": "2026-09-24", "item_name": "Linen summer dress", "product_category": "clothing",
    "is_clearance": False, "item_condition": "unused", "tracking_number": "TRK-20877",
}  # fmt: skip
VOUCHER_ARGS = {"order_id": "NS-20877", "amount": 100, "reason": "late"}
Made = Callable[..., TeamCCapabilities]


def tool_def(
    name: str, proposal: str, version: int = 1, *, read_only: bool = True, sandbox: bool = True, live: bool = False
) -> dict[str, Any]:
    meta = {
        "publication_id": f"pub-{name}", "artifact_id": f"art-{name}", "artifact_sha256": "ab" * 32,
        "proposal_id": proposal, "proposal_version": version, "activated": False,
        "environment": "live_demo" if live else "sandbox" if sandbox else "production", "production_ready": not sandbox,
    }  # fmt: skip
    if live:
        meta["api_host"] = "api.example.com"
    schema = {"type": "object", "properties": {"order_id": {"type": "string"}}, "additionalProperties": False}
    return {
        "name": name, "title": name, "description": "sandbox-only", "annotations": {"readOnlyHint": read_only},
        "inputSchema": schema, "_meta": {"team_c": meta},
    }  # fmt: skip


def envelope(name: str, status: str, output: dict[str, Any] | None = None, writes: list[dict[str, Any]] | None = None,
             **extra: Any) -> dict[str, Any]:  # fmt: skip
    return {
        "status": status, "output": output if status == "succeeded" else None, "message": f"{status} (test)",
        "error": None if status == "succeeded" else {"code": status, "message": "test"},
        "execution_id": f"exe-{status}",
        "writes": writes or [], "tool": name, "publication_id": f"pub-{name}",
        "artifact": {"id": f"art-{name}"}, "production_ready": False, **extra,
    }  # fmt: skip


ORDER_OK = envelope("get_order_v1_aa", "succeeded", ORDER)


class Fake:
    """The fake server's script and call log, and a TeamCCapabilities wired to it."""

    def __init__(self, tmp_path: Path) -> None:
        self.script_path = tmp_path / "script.json"
        self.log_path = tmp_path / "calls.jsonl"
        self.script: dict[str, Any] = {"tools": [], "calls": {}}
        self.save()

    def save(self) -> None:
        self.script_path.write_text(json.dumps(self.script), encoding="utf-8")

    def publish(self, *tools: dict[str, Any], **calls: dict[str, Any]) -> None:
        self.script["tools"], self.script["calls"] = list(tools), calls
        self.save()

    def calls(self) -> list[dict[str, Any]]:
        if not self.log_path.exists():
            return []
        return [json.loads(line) for line in self.log_path.read_text(encoding="utf-8").splitlines()]

    def config(self, **tenant: Any) -> dict[str, Any]:
        binding = {
            "business_id": BUSINESS, "identities": {"C-100": "alice", "C-101": "bob"},
            "tools": {"get_order": {"proposal_id": GET_ORDER_PROPOSAL},
                      "apply_voucher": {"proposal_id": VOUCHER_PROPOSAL, "reference_field": "reference_id"}},
            **tenant,
        }  # fmt: skip
        return {
            "command": [sys.executable, str(FAKE)], "list_timeout_s": 10, "call_timeout_s": 3, "start_timeout_s": 15,
            "env": {"FAKE_TEAM_C_SCRIPT": str(self.script_path), "FAKE_TEAM_C_LOG": str(self.log_path)},
            "tenants": {T: binding},
        }  # fmt: skip

    def client(self, **tenant: Any) -> TeamCCapabilities:
        shop = StandinShop(PROJECT_ROOT / "fixtures", FixedClock(date(2026, 9, 28)), [T])
        return TeamCCapabilities(shop, TeamCConfig.model_validate(self.config(**tenant)))


@pytest.fixture
def fake(tmp_path: Path) -> Fake:
    return Fake(tmp_path)


@pytest.fixture
async def made(fake: Fake) -> Any:
    clients: list[TeamCCapabilities] = []

    def make(**tenant: Any) -> TeamCCapabilities:
        clients.append(fake.client(**tenant))
        return clients[-1]

    yield make
    for c in clients:
        await c.aclose()


def request(tool: str, arguments: dict[str, Any] | None = None, customer: str | None = "C-100") -> ToolCallRequest:
    return ToolCallRequest(
        request_id="r1", tool=tool, arguments=arguments or {"order_id": "NS-20877"}, idempotency_key="k1",
        policy_request_id="pol-1", acting_customer_id=customer,
    )  # fmt: skip


# ---- discovery ----


async def test_a_published_tool_is_served_with_the_businesss_own_spec_and_unbound_tools_stay_with_the_stand_in(
    fake: Fake, made: Made
) -> None:
    fake.publish(tool_def("order_v2_aa", GET_ORDER_PROPOSAL, 2), tool_def("order_v1_bb", GET_ORDER_PROPOSAL, 1))
    client = made()
    tools = {t.name: t for t in await client.list_tools(T)}
    assert "verify_customer" in tools and "create_return" in tools  # still the stand-in's
    assert tools["get_order"].operation_kind == "read"
    assert "order_status" in tools["get_order"].output_schema["required"]  # Team B's own declaration
    assert "apply_voucher" not in tools  # bound to Team C and not published there: never the stand-in's
    state = client.status(T)
    assert state is not None and state.resolved["get_order"].team_c_tool == "order_v2_aa"  # newest version
    assert state.resolved["apply_voucher"].problem == "not_published"


@pytest.mark.parametrize(
    ("tools", "problem"),
    [
        ([tool_def("a_v1_1", GET_ORDER_PROPOSAL), tool_def("a_v1_2", GET_ORDER_PROPOSAL)], "ambiguous"),
        ([tool_def("a_v1_1", GET_ORDER_PROPOSAL, read_only=False)], "kind_mismatch"),
        ([tool_def("a_v1_1", GET_ORDER_PROPOSAL, sandbox=False)], "environment_not_allowed"),
    ],
    ids=["two-artifacts-same-version", "a-write-declared-as-read", "not-a-sandbox-publication"],
)
async def test_a_doubtful_publication_is_not_used(
    fake: Fake, made: Made, tools: list[dict[str, Any]], problem: str
) -> None:
    fake.publish(*tools)
    client = made()
    assert "get_order" not in {t.name for t in await client.list_tools(T)}
    state = client.status(T)
    assert state is not None and state.resolved["get_order"].problem == problem


async def test_team_c_down_hides_its_tools_but_the_stand_ins_stay(fake: Fake, made: Made) -> None:
    fake.publish(tool_def("get_order_v1_aa", GET_ORDER_PROPOSAL))
    fake.script["refuse_identities"] = ["alice", "bob"]
    fake.save()
    client = made()
    names = {t.name for t in await client.list_tools(T)}
    assert "get_order" not in names and "verify_customer" in names
    state = client.status(T)
    assert state is not None and state.error and state.resolved["get_order"].problem == "team_c_unavailable"


async def test_a_new_publication_appears_on_the_next_listing_without_a_restart(fake: Fake, made: Made) -> None:
    client = made()
    assert "get_order" not in {t.name for t in await client.list_tools(T)}
    fake.publish(tool_def("get_order_v1_aa", GET_ORDER_PROPOSAL))
    assert "get_order" in {t.name for t in await client.list_tools(T)}


# ---- calls ----


async def test_a_success_maps_the_output_and_runs_as_the_verified_customers_own_identity(
    fake: Fake, made: Made
) -> None:
    fake.publish(
        tool_def("get_order_v1_aa", GET_ORDER_PROPOSAL),
        get_order_v1_aa=envelope("get_order_v1_aa", "succeeded", {"order": {**ORDER}}, echo_identity=True),
    )
    fields = {k: f"/order/{k}" for k in ORDER} | {"ran_as": "ran_as"}
    binding = {"proposal_id": GET_ORDER_PROPOSAL, "arguments": {"order_id": "order_number"}, "fields": fields}
    client = made(tools={"get_order": binding})
    await client.list_tools(T)
    result = await client.call_tool(T, request("get_order", customer="C-101"))
    assert result.status == "success" and result.audit_id == "team-c:exe-succeeded"
    assert result.data["order_status"] == "shipped" and result.data["delivered_at"] is None  # a real null is kept
    assert result.data["ran_as"] == "bob"  # C-101 is bob in the operator's map
    sent = fake.calls()
    assert sent == [{"identity": "bob", "business_id": BUSINESS, "tool": "get_order_v1_aa",
                     "arguments": {"order_number": "NS-20877"}}]  # fmt: skip


async def test_an_unverified_or_unmapped_customer_is_not_sent(fake: Fake, made: Made) -> None:
    fake.publish(tool_def("get_order_v1_aa", GET_ORDER_PROPOSAL), get_order_v1_aa=ORDER_OK)
    client = made()
    await client.list_tools(T)
    for customer in (None, "C-999"):
        result = await client.call_tool(T, request("get_order", customer=customer))
        assert result.error_code == "IDENTITY_NOT_MAPPED" and not result.write_may_have_applied
    assert fake.calls() == []


async def test_arguments_cannot_choose_the_identity(fake: Fake, made: Made) -> None:
    fake.publish(
        tool_def("get_order_v1_aa", GET_ORDER_PROPOSAL),
        get_order_v1_aa=envelope("get_order_v1_aa", "succeeded", ORDER, echo_identity=True),
    )
    client = made()
    await client.list_tools(T)
    await client.call_tool(T, request("get_order", {"order_id": "NS-20877", "identity": "bob", "user": "bob"}))
    assert [c["identity"] for c in fake.calls()] == ["alice"]  # extra arguments are passed on for Team C to refuse


@pytest.mark.parametrize(
    ("status", "writes", "code", "uncertain"),
    [
        ("rejected", [], "TEAM_C_REJECTED", False),
        ("failed", [{"step_id": "s1", "write_state": "not_attempted"}], "TEAM_C_FAILED", False),
        ("failed", [{"step_id": "s1", "write_state": "rejected_unverified"}], "TEAM_C_FAILED", True),
        ("partial", [{"step_id": "s1", "write_state": "applied"}], "PARTIAL_WRITE", True),
        ("outcome_unknown", [{"step_id": "s1", "write_state": "unknown"}], "OUTCOME_UNKNOWN", True),
        ("error", [], "TEAM_C_ERROR", True),
    ],
)
async def test_only_succeeded_is_a_success_and_an_unclear_write_is_never_reported_as_not_done(
    fake: Fake, made: Made, status: str, writes: list[dict[str, Any]], code: str,
    uncertain: bool,
) -> None:  # fmt: skip
    name = "issue_voucher_v1_cc"
    fake.publish(tool_def(name, VOUCHER_PROPOSAL, read_only=False), **{name: envelope(name, status, writes=writes)})
    client = made()
    await client.list_tools(T)
    result = await client.call_tool(T, request("apply_voucher", VOUCHER_ARGS))
    assert result.status == "error" and result.error_code == code
    assert result.write_may_have_applied is uncertain and result.retryable is False
    assert result.audit_id == f"team-c:exe-{status}"
    assert len(fake.calls()) == 1  # never repeated


async def test_an_error_on_a_read_is_not_an_unclear_write(fake: Fake, made: Made) -> None:
    fake.publish(tool_def("get_order_v1_aa", GET_ORDER_PROPOSAL), get_order_v1_aa=envelope("get_order_v1_aa", "error"))
    client = made()
    await client.list_tools(T)
    result = await client.call_tool(T, request("get_order"))
    assert result.error_code == "TEAM_C_ERROR" and not result.write_may_have_applied


async def test_a_disabled_tool_answers_not_published_and_the_next_call_asks_team_c_again(
    fake: Fake, made: Made
) -> None:
    fake.publish(tool_def("get_order_v1_aa", GET_ORDER_PROPOSAL), get_order_v1_aa=ORDER_OK)
    client = made()
    await client.list_tools(T)
    assert (await client.call_tool(T, request("get_order"))).status == "success"
    fake.publish()  # disabled in Team C: the old tool list in Team B does not let the call through
    result = await client.call_tool(T, request("get_order"))
    assert result.error_code == "TOOL_NOT_PUBLISHED" and not result.write_may_have_applied
    again = await client.call_tool(T, request("get_order"))  # re-listed: nothing is published, nothing is sent
    assert again.error_code == "TOOL_NOT_PUBLISHED"
    assert len(fake.calls()) == 2


async def test_a_lost_answer_is_an_unknown_outcome_for_a_write(fake: Fake, made: Made) -> None:
    name = "issue_voucher_v1_cc"
    fake.publish(tool_def(name, VOUCHER_PROPOSAL, read_only=False), **{name: {"sleep": 10}})
    client = made()
    await client.list_tools(T)
    with pytest.raises(UpstreamError) as caught:
        await client.call_tool(T, request("apply_voucher", VOUCHER_ARGS))
    assert caught.value.code == "TIMEOUT" and caught.value.retryable is False  # never BACKEND_UNAVAILABLE ("not sent")
    assert len(fake.calls()) == 1


async def test_a_server_that_dies_mid_call_is_an_unknown_outcome(fake: Fake, made: Made) -> None:
    name = "issue_voucher_v1_cc"
    fake.publish(tool_def(name, VOUCHER_PROPOSAL, read_only=False), **{name: {"exit": 1}})
    client = made()
    await client.list_tools(T)
    with pytest.raises(UpstreamError) as caught:
        await client.call_tool(T, request("apply_voucher", VOUCHER_ARGS))
    assert caught.value.code == "CONNECTION_LOST" and not caught.value.retryable


async def test_a_server_that_cannot_start_is_unavailable_and_nothing_was_sent(fake: Fake, made: Made) -> None:
    fake.publish(tool_def("get_order_v1_aa", GET_ORDER_PROPOSAL), get_order_v1_aa=ORDER_OK)
    client = made()
    await client.list_tools(T)  # listed as alice
    fake.script["refuse_identities"] = ["bob"]
    fake.save()
    with pytest.raises(UpstreamError) as caught:
        await client.call_tool(T, request("get_order", customer="C-101"))
    assert caught.value.code == "BACKEND_UNAVAILABLE"
    assert fake.calls() == []


async def test_a_result_that_claims_production_is_not_trusted(fake: Fake, made: Made) -> None:
    fake.publish(
        tool_def("get_order_v1_aa", GET_ORDER_PROPOSAL),
        get_order_v1_aa=envelope("get_order_v1_aa", "succeeded", ORDER) | {"production_ready": True},
    )
    client = made()
    await client.list_tools(T)
    assert (await client.call_tool(T, request("get_order"))).error_code == "TEAM_C_BAD_RESULT"


def test_the_example_config_is_valid_and_its_paths_are_relative_to_the_file() -> None:
    config = TeamCConfig.load(PROJECT_ROOT / "config" / "team_c.example.json")
    assert set(config.tenants["shop_001"].tools) == {"get_order", "create_ticket"}
    team_c = (PROJECT_ROOT.parent / "tools").resolve()
    assert config.cwd == str(team_c) and Path(config.command[0]).is_relative_to(team_c)
    command = config.server_command("biz", "alice")
    assert command[3:7] == ["--business-id", "biz", "--identity", "alice"]
    assert ["--sandbox-hosts", "127.0.0.1:8102"] == command[-2:]


async def test_a_live_demo_publication_is_used_only_where_the_operator_allows_it(fake: Fake, made: Made) -> None:
    live_ok = envelope("get_order_v1_aa", "succeeded", ORDER, environment="live_demo")
    fake.publish(tool_def("get_order_v1_aa", GET_ORDER_PROPOSAL, live=True), get_order_v1_aa=live_ok)
    default = made()
    assert "get_order" not in {t.name for t in await default.list_tools(T)}
    state = default.status(T)
    assert state is not None and state.resolved["get_order"].problem == "environment_not_allowed"
    allowed = made(tools={"get_order": {"proposal_id": GET_ORDER_PROPOSAL, "environments": ["sandbox", "live_demo"]}})
    assert "get_order" in {t.name for t in await allowed.list_tools(T)}
    resolved = allowed.status(T).resolved["get_order"]  # type: ignore[union-attr]
    assert resolved.environment == "live_demo" and resolved.api_host == "api.example.com"
    result = await allowed.call_tool(T, request("get_order"))
    assert result.status == "success" and result.data["order_status"] == "shipped"


async def test_only_the_allowed_environment_is_used_and_two_allowed_ones_are_ambiguous(fake: Fake, made: Made) -> None:
    sandbox, live = (
        tool_def("order_sbx_v1_aa", GET_ORDER_PROPOSAL),
        tool_def("order_live_v1_bb", GET_ORDER_PROPOSAL, live=True),
    )
    fake.publish(sandbox, live)
    live_only = made(tools={"get_order": {"proposal_id": GET_ORDER_PROPOSAL, "environments": ["live_demo"]}})
    await live_only.list_tools(T)
    assert live_only.status(T).resolved["get_order"].team_c_tool == "order_live_v1_bb"  # type: ignore[union-attr]
    sandbox_only = made()
    await sandbox_only.list_tools(T)
    assert sandbox_only.status(T).resolved["get_order"].team_c_tool == "order_sbx_v1_aa"  # type: ignore[union-attr]
    both = made(tools={"get_order": {"proposal_id": GET_ORDER_PROPOSAL, "environments": ["sandbox", "live_demo"]}})
    assert "get_order" not in {t.name for t in await both.list_tools(T)}
    assert both.status(T).resolved["get_order"].problem == "ambiguous"  # type: ignore[union-attr]


async def test_a_result_from_another_environment_is_not_trusted(fake: Fake, made: Made) -> None:
    fake.publish(tool_def("get_order_v1_aa", GET_ORDER_PROPOSAL, live=True), get_order_v1_aa=ORDER_OK)  # says sandbox
    client = made(tools={"get_order": {"proposal_id": GET_ORDER_PROPOSAL, "environments": ["live_demo"]}})
    await client.list_tools(T)
    assert (await client.call_tool(T, request("get_order"))).error_code == "TEAM_C_BAD_RESULT"


@pytest.mark.parametrize(
    ("category", "code"),
    [("not_found", "NOT_FOUND"), ("rate_limited", "TEAM_C_FAILED"), ("authentication_failed", "TEAM_C_FAILED")],
)
async def test_a_read_failure_keeps_team_cs_category(fake: Fake, made: Made, category: str, code: str) -> None:
    failed = envelope("get_order_v1_aa", "failed") | {"error": {"code": "rejected_by_api", "category": category}}
    fake.publish(tool_def("get_order_v1_aa", GET_ORDER_PROPOSAL), get_order_v1_aa=failed)
    client = made()
    await client.list_tools(T)
    result = await client.call_tool(T, request("get_order"))
    assert result.error_code == code and result.error_message is not None and result.error_message.startswith(category)
    assert not result.write_may_have_applied and not result.retryable


def test_a_binding_names_exactly_one_tool() -> None:
    with pytest.raises(ValueError):
        TeamCConfig.model_validate({"command": ["x"], "tenants": {T: {"business_id": "b", "tools": {"get_order": {}}}}})


# ---- through the brain ----


def brain(tmp_path: Path, fake: Fake) -> Container:
    path = tmp_path / "team_c.json"
    path.write_text(json.dumps(fake.config()), encoding="utf-8")
    return build_container(make_settings(tmp_path, fixed_today=date(2026, 9, 28), team_c_config=path))


async def test_the_agent_answers_an_order_question_from_team_cs_tool_as_the_verified_customer(
    tmp_path: Path, fake: Fake
) -> None:
    fake.publish(
        tool_def("get_order_v1_aa", GET_ORDER_PROPOSAL), get_order_v1_aa=envelope("get_order_v1_aa", "succeeded", ORDER)
    )
    c = brain(tmp_path, fake)
    assert c.orchestrator is not None and c.team_c is not None
    try:
        first = await c.orchestrator.handle_turn(T, "tc1", "Where is my order NS-20877?")
        second = await c.orchestrator.handle_turn(T, "tc1", "01012345601")
    finally:
        await c.team_c.aclose()
    assert first.decision.value == "verify_identity"  # the stand-in's verify_customer: identity first, nothing read
    assert second.decision.value == "answer"
    assert [x["identity"] for x in fake.calls()] == ["alice"]  # C-100, verified by phone, acts as alice
    traces = await c.traces.for_conversation(T, "tc1")
    reads = [call for t in traces for call in t.tool_calls if call.tool == "get_order"]
    assert reads and reads[-1].audit_id == "team-c:exe-succeeded"


async def test_an_unknown_outcome_from_team_c_hands_over_and_the_write_is_not_repeated(
    tmp_path: Path, fake: Fake
) -> None:
    name = "issue_voucher_v1_cc"
    fake.publish(
        tool_def("get_order_v1_aa", GET_ORDER_PROPOSAL),  # the action reads the order first (facts for the rules)
        tool_def(name, VOUCHER_PROPOSAL, read_only=False),
        get_order_v1_aa=envelope("get_order_v1_aa", "succeeded", ORDER),
        **{name: envelope(name, "outcome_unknown", writes=[{"step_id": "s1", "write_state": "unknown"}])},
    )
    c = brain(tmp_path, fake)
    assert c.orchestrator is not None and c.team_c is not None
    try:
        replies = [
            await c.orchestrator.handle_turn(T, "tc2", text)
            for text in ("I want a voucher of 100 EGP for the late delivery of order NS-20877", "01012345601", "yes")
        ]
    finally:
        await c.team_c.aclose()
    assert [r.decision.value for r in replies] == ["verify_identity", "confirm", "handoff"]
    last = await c.traces.get(T, replies[-1].trace_id)
    assert last is not None and last.escalation_reason is not None
    assert last.escalation_reason.value == "unverified_result" and replies[-1].handoff_case_id
    writes = [x for x in fake.calls() if x["tool"] == name]
    assert len(writes) == 1 and writes[0]["identity"] == "alice"  # exactly once, never retried, as the verified C-100
    assert c.shop is not None and not any(e.tool == "apply_voucher" for e in c.shop.audit_log(T))  # not the stand-in


def test_a_bound_tool_can_echo_an_argument_and_set_a_constant_after_a_team_c_success() -> None:
    from agent_service.adapters.team_c_tools import ResolvedTool, ToolBinding, _result

    envelope = {
        "status": "succeeded", "execution_id": "e1", "production_ready": False, "environment": "sandbox",
        "output": {"ref": "RET-30001"},
    }  # fmt: skip
    raw = {"structuredContent": envelope}
    bound = ToolBinding(
        proposal_id="p", fields={"reference_id": "ref"}, reference_field="reference_id", echo=["order_id"],
        constants={"status": "created"},
    )  # fmt: skip
    resolved = ResolvedTool(name="create_return", environment="sandbox")
    result = _result(raw, bound, resolved, True, {"order_id": "NS-1"})
    assert result.status == "success"
    assert result.data == {"reference_id": "RET-30001", "order_id": "NS-1", "status": "created"}
    failed = _result(
        {"structuredContent": {**envelope, "status": "failed"}}, bound, resolved, True, {"order_id": "NS-1"}
    )
    assert failed.status == "error" and not failed.data


# ---- a business's active bundle rebinds its tools ----


def bundle_with(tool: str, proposal: str, business: str = BUSINESS) -> Any:
    from datetime import UTC, datetime

    from agent_service.bundles import tools_from_config
    from agent_service.domain.bundle import BundleProfile, BundleTool, TenantBundle, ToolBindingRef
    from agent_service.domain.tenant import TenantRegistry

    config = TenantRegistry.from_dir(PROJECT_ROOT / "config" / "tenants").get(T)
    others = tuple(t for t in tools_from_config(config) if t.name != tool)
    bound = BundleTool(name=tool, source="team_c", binding=ToolBindingRef(business_id=business, proposal_id=proposal))
    return TenantBundle(
        tenant_id=T, version=2, status="active", created_at=datetime(2026, 9, 28, tzinfo=UTC), created_by="test",
        config=config, profile=BundleProfile(display_name=config.display_name), tools=(*others, bound),
    )  # fmt: skip


async def test_an_active_bundle_replaces_the_files_tool_bindings_and_keeps_its_identities(
    fake: Fake, made: Made
) -> None:
    fake.publish(tool_def("order_v1_aa", GET_ORDER_PROPOSAL), tool_def("ret_v1_bb", "prop-return"))
    client = made()
    await client.refresh(T)
    assert set(client.config.tenants[T].tools) == {"get_order", "apply_voucher"}
    await client.on_bundle_change(bundle_with("create_return", "prop-return"), T)
    binding = client.config.tenants[T]
    assert set(binding.tools) == {"create_return"} and binding.tools["create_return"].proposal_id == "prop-return"
    assert binding.identities == {"C-100": "alice", "C-101": "bob"}  # the operator's mapping carries over
    assert client.status(T) is None  # its cached listing was dropped
    await client.on_bundle_change(None, T)  # rolled back to the file
    assert set(client.config.tenants[T].tools) == {"get_order", "apply_voucher"}


async def test_a_bundle_without_tools_service_tools_leaves_the_file_binding_alone(fake: Fake, made: Made) -> None:
    client = made()
    bundle = bundle_with("create_return", "prop-return").model_copy(update={"tools": ()})
    await client.on_bundle_change(bundle, T)
    assert set(client.config.tenants[T].tools) == {"get_order", "apply_voucher"}


async def test_the_customer_id_is_left_out_for_a_tool_that_takes_no_customer(fake: Fake, made: Made) -> None:
    listing = tool_def("list_orders_v1_aa", "prop-list")
    listing["inputSchema"] = {"type": "object", "properties": {}, "additionalProperties": False}
    fake.publish(listing, list_orders_v1_aa=envelope("list_orders_v1_aa", "succeeded", {"orders": [ORDER]}))
    client = made(tools={"list_customer_orders": {"proposal_id": "prop-list"}})
    await client.list_tools(T)
    result = await client.call_tool(T, request("list_customer_orders", {"customer_id": "C-100"}))
    assert result.status == "success" and [c["arguments"] for c in fake.calls()] == [{}]
