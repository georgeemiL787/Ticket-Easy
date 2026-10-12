"""The operation index must not silently discard the operations a request needs.

Filling the index in inventory order and truncating it at a character budget drops whatever sorts
last, so a request for orders on a large API was told the orders endpoints did not exist. The owner's
goal is the only evidence of what they want, so it ranks the index before the budget is spent.
"""
import pytest

from team_c import capabilities


def inventory():
    return {"operations": [
        {"id": "p1", "method": "GET", "path": "/api/v1/products/", "summary": "List products",
         "inputs": {}, "responses": {"200": {"schema": {"type": "object"}}},
         "declared_auth": {"status": "required"}, "proposal_eligible": True},
        {"id": "p2", "method": "GET", "path": "/api/v1/products/{slug}", "summary": "Get product",
         "inputs": {}, "responses": {"200": {"schema": {"type": "object"}}},
         "declared_auth": {"status": "required"}, "proposal_eligible": True},
        {"id": "o1", "method": "GET", "path": "/api/v1/orders/", "summary": "List orders",
         "inputs": {}, "responses": {"200": {"schema": {"type": "object"}}},
         "declared_auth": {"status": "required"}, "proposal_eligible": True},
        {"id": "o2", "method": "GET", "path": "/api/v1/orders/{order_id}", "summary": "Get order",
         "inputs": {}, "responses": {"200": {"schema": {"type": "object"}}},
         "declared_auth": {"status": "required"}, "proposal_eligible": True},
    ]}


def paths(index):
    return [e["path"] for e in index]


def test_without_a_goal_the_index_is_filled_in_inventory_order():
    index, coverage = capabilities.operation_index(inventory(), 400)
    assert paths(index) == ["/api/v1/products/", "/api/v1/products/{slug}"]
    assert coverage["goal_ranked"] is False


def test_a_goal_ranks_the_index_so_the_requested_operations_survive():
    goal = "Let a signed-in customer view their own orders and read the full detail of one order they choose."
    index, coverage = capabilities.operation_index(inventory(), 400, None, (), goal)
    assert set(paths(index)) == {"/api/v1/orders/", "/api/v1/orders/{order_id}"}
    assert coverage["goal_ranked"] is True


def test_ranking_follows_the_goal_not_the_api():
    """The same index serves whichever goal names it; nothing is hardcoded per endpoint."""
    index, _ = capabilities.operation_index(inventory(), 400, None, (), "what products do we have")
    assert paths(index) == ["/api/v1/products/", "/api/v1/products/{slug}"]


def test_ranking_never_drops_an_eligible_operation_the_budget_can_hold():
    """Reordering must not cost coverage: a generous budget still carries every operation."""
    index, coverage = capabilities.operation_index(inventory(), 100_000, None, (), "orders")
    assert len(index) == 4 and coverage["partial"] is False


def test_a_goal_with_no_usable_words_leaves_the_order_unchanged():
    index, coverage = capabilities.operation_index(inventory(), 400, None, (), "the a of")
    assert paths(index) == ["/api/v1/products/", "/api/v1/products/{slug}"]
    assert coverage["goal_ranked"] is True  # ranking was attempted, it just found no signal


def test_restricted_operations_stay_below_eligible_ones_even_when_the_goal_names_them():
    """Eligibility outranks relevance: a restricted endpoint is never promoted above an eligible one."""
    inv = inventory()
    inv["operations"][0]["proposal_eligible"] = False
    inv["operations"][0]["exposure"] = {"classification": "restricted", "signals": [{"effect": "restricts", "detail": "internal"}]}
    index, _ = capabilities.operation_index(inv, 100_000, None, (), "products")
    assert index[0]["status"] == "eligible"
    assert index[-1]["status"] == "restricted"


def test_relevance_scores_a_repeated_word_higher_than_a_single_mention():
    once = capabilities.relevance("orders")
    twice = capabilities.relevance("orders orders orders")
    entry = {"path": "/api/v1/orders/", "summary": "orders"}
    assert twice(entry) > once(entry) > 0


def test_relevance_scores_a_path_segment_above_a_description_word():
    segment = capabilities.relevance("orders")
    in_path = {"path": "/api/v1/orders/", "summary": "list"}
    in_text = {"path": "/api/v1/records/", "summary": "orders"}
    assert segment(in_path) > segment(in_text)