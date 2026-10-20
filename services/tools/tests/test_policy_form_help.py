"""The access form helps instead of failing: suggested effects, and a plain way out of an impossible resource-owner choice."""
import pytest
from starlette.datastructures import FormData

from tools_service.config import AppError
from tools_service.services.proposals import Proposals
from tools_service.web import actions

OPS = {
    "refund": {"method": "POST", "path": "/refunds", "inputs": {"body.amount": {}}},
    "cancel": {"method": "POST", "path": "/orders/{id}/cancel", "inputs": {}},
    "lookup": {"method": "GET", "path": "/orders/{id}", "inputs": {}},
}


def test_a_read_only_tool_suggests_no_effects():
    assert Proposals._suggested_effects({"steps": [{"operation_id": "lookup"}]}, OPS) == {}


def test_a_refund_is_suggested_as_financial_and_a_cancel_as_irreversible():
    found = Proposals._suggested_effects({"steps": [{"operation_id": "refund"}, {"operation_id": "cancel"}]}, OPS)
    assert found["financial"] is True and found["irreversible"] is True and found["external_side_effects"] is True


class Service:
    def view(self, pid, version):
        return {"requirements": [], "capability": {"inputs": []}}

    def accept_policy(self, *args):
        raise AssertionError("an incomplete policy must not be accepted")


def test_resource_owner_without_a_check_says_how_to_fix_it():
    form = FormData([("principals", "resource_owner")])
    with pytest.raises(AppError) as error:
        actions.accept_policy(Service(), form, "p1", 1, 1)
    assert "untick Resource owner" in error.value.message and "ownership check" in error.value.message
