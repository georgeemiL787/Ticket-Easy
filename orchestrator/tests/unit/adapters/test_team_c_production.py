# ruff: noqa: F811  (the fixtures are imported from test_team_c_tools and used by name)
"""Production tools through TeamCCapabilities: one service-credential process per business, customer as context.

Against the scripted fake server (tests/fake_team_c_mcp.py, "split_production": the process started with
--context-from-client lists and runs only production tools). What must hold: production is used only when the operator
allows it for that tool AND Team C marks the publication production_ready; no verified customer means nothing is sent;
the customer id and idempotency key travel in _meta.ticketeasy, never in the arguments; a production result is trusted
only when it says production for the same publication.
"""

from typing import Any

from tests.unit.adapters.test_team_c_tools import (  # noqa: F401  (fake and made are fixtures)
    GET_ORDER_PROPOSAL,
    ORDER,
    Fake,
    Made,
    envelope,
    fake,
    made,
    request,
    tool_def,
)

T = "shop_001"
PROD_TOOL = "order_prod_v1_cc"
PROD_OK = envelope(PROD_TOOL, "succeeded", ORDER, production_ready=True, environment="production")


def publish(fake: Fake, *tools: dict[str, Any], **calls: dict[str, Any]) -> None:
    fake.publish(*tools, **calls)
    fake.script["split_production"] = True
    fake.save()


def production_fake(fake: Fake, **calls: dict[str, Any]) -> None:
    publish(fake, tool_def(PROD_TOOL, GET_ORDER_PROPOSAL, sandbox=False), **{PROD_TOOL: PROD_OK, **calls})


def production_binding() -> dict[str, Any]:
    return {"tools": {"get_order": {"proposal_id": GET_ORDER_PROPOSAL, "environments": ["production"]}}}


async def test_a_production_tool_runs_on_the_production_process_with_the_verified_customer_as_context(
    fake: Fake, made: Made
) -> None:
    production_fake(fake)
    client = made(**production_binding())
    assert "get_order" in {t.name for t in await client.list_tools(T)}
    state = client.status(T)
    assert state is not None and state.resolved["get_order"].environment == "production"
    result = await client.call_tool(T, request("get_order", customer="C-101"))
    assert result.status == "success" and result.audit_id == "team-c:exe-succeeded"
    [call] = [c for c in fake.calls() if c["tool"] == PROD_TOOL]
    assert call["production"] is True
    assert call["meta"] == {"ticketeasy": {"context": {"customer_id": "C-101"}, "idempotency_key": "k1"}}
    assert call["arguments"] == {"order_id": "NS-20877"}  # nothing about the customer in the arguments


async def test_no_verified_customer_means_nothing_is_sent_to_production(fake: Fake, made: Made) -> None:
    production_fake(fake)
    client = made(**production_binding())
    await client.list_tools(T)
    result = await client.call_tool(T, request("get_order", customer=None))
    assert result.status == "error" and result.error_code == "IDENTITY_NOT_MAPPED"
    assert [c for c in fake.calls() if c["tool"] == PROD_TOOL] == []


async def test_production_is_used_only_where_the_operator_allows_it(fake: Fake, made: Made) -> None:
    production_fake(fake)
    client = made()  # the default binding allows the sandbox only
    assert "get_order" not in {t.name for t in await client.list_tools(T)}
    # a sandbox publication is never used by a binding that asks for production
    publish(fake, tool_def("order_v1_aa", GET_ORDER_PROPOSAL))
    only_production = made(**production_binding())
    assert "get_order" not in {t.name for t in await only_production.list_tools(T)}
    state = only_production.status(T)
    assert state is not None and state.resolved["get_order"].problem in ("not_published", "environment_not_allowed")


async def test_a_publication_that_claims_production_without_team_cs_flag_is_not_used(fake: Fake, made: Made) -> None:
    liar = tool_def(PROD_TOOL, GET_ORDER_PROPOSAL, sandbox=False)
    liar["_meta"]["team_c"]["production_ready"] = False
    publish(fake, liar, **{PROD_TOOL: PROD_OK})
    client = made(**production_binding())
    assert "get_order" not in {t.name for t in await client.list_tools(T)}
    state = client.status(T)
    assert state is not None and state.resolved["get_order"].problem == "environment_not_allowed"


async def test_sandbox_and_production_both_allowed_and_published_is_ambiguous_never_a_silent_choice(
    fake: Fake, made: Made
) -> None:
    publish(fake, tool_def("order_v1_aa", GET_ORDER_PROPOSAL), tool_def(PROD_TOOL, GET_ORDER_PROPOSAL, sandbox=False))
    client = made(tools={"get_order": {"proposal_id": GET_ORDER_PROPOSAL, "environments": ["sandbox", "production"]}})
    await client.list_tools(T)
    state = client.status(T)
    assert state is not None and state.resolved["get_order"].problem == "ambiguous"


async def test_a_production_answer_is_trusted_only_for_the_same_production_publication(fake: Fake, made: Made) -> None:
    other = envelope(PROD_TOOL, "succeeded", ORDER, production_ready=True, environment="production")
    other["publication_id"] = "pub-other"
    wrong = (
        envelope(PROD_TOOL, "succeeded", ORDER, environment="production"),  # not marked production
        envelope(PROD_TOOL, "succeeded", ORDER, production_ready=True, environment="sandbox"),  # another environment
        other,  # another publication
    )
    for answer in wrong:
        production_fake(fake, **{PROD_TOOL: answer})
        client = made(**production_binding())
        await client.list_tools(T)
        result = await client.call_tool(T, request("get_order"))
        assert result.status == "error" and result.error_code == "TEAM_C_BAD_RESULT"


async def test_a_refusal_before_any_request_is_a_clean_rejection(fake: Fake, made: Made) -> None:
    refused = {
        "status": "rejected",
        "output": None,
        "message": "A production call needs the verified customer's trusted context. No request was sent.",
        "error": {"code": "context_missing", "category": "invalid_arguments", "message": "x"},
        "execution_id": None,
        "writes": [],
        "tool": None,
        "publication_id": None,
        "artifact": None,
        "environment": None,
        "production_ready": False,
    }
    production_fake(fake, **{PROD_TOOL: refused})
    client = made(**production_binding())
    await client.list_tools(T)
    result = await client.call_tool(T, request("get_order"))
    assert result.status == "error" and result.error_code == "TEAM_C_REJECTED" and not result.write_may_have_applied
    assert "invalid_arguments" in (result.error_message or "")
