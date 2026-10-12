"""A proposal must earn review: the model rates it, and the application checks it too."""
import pytest

from team_c.capabilities import behavior
from team_c.models import CRITERIA
from team_c.quality import assess, existing_tools, structural

OP_LIST = "a" * 32
OP_GET = "b" * 32


def inventory():
    return {"operations": [
        {"id": OP_LIST, "method": "GET", "path": "/orders", "inputs": {"query.page": {"required": False}}},
        {"id": OP_GET, "method": "GET", "path": "/orders/{order_id}",
         "inputs": {"path.order_id": {"required": True}}},
    ]}


def proposal(**overrides):
    content = {
        "name": "Order lookup", "description": "d", "business_purpose": "p",
        "steps": [
            {"id": "s1", "operation_id": OP_LIST, "purpose": "list", "bindings": []},
            {"id": "s2", "operation_id": OP_GET, "purpose": "get",
             "bindings": [{"target": "path.order_id", "kind": "runtime_argument", "reference": "order_id",
                           "step_id": None, "response_status": None}]},
        ],
        "configuration": [], "questions": [],
        "outputs": [{"name": "order_list", "step_id": "s1", "response_status": "200", "pointer": "/items"},
                    {"name": "detail", "step_id": "s2", "response_status": "200", "pointer": "/id"}],
        "expected_reads": [], "expected_writes": [], "assumptions": [], "limitations": [],
        "risk": "low", "risk_rationale": "r",
        "self_review": {"criteria": [{"criterion": c, "score": 4, "reason": "ok"} for c in CRITERIA],
                        "verdict": "proceed", "summary": "ready"},
    }
    content.update(overrides)
    return content


def keys(findings):
    return {f["key"] for f in findings}


def test_a_sound_proposal_passes():
    report = assess(proposal(), inventory())
    assert report["ready"] and report["verdict"] == "proceed" and report["score"] == 4
    assert report["blockers"] == []


def test_an_output_returning_the_whole_response_is_flagged_but_not_blocked():
    """An empty JSON Pointer is legal: it selects the whole body.

    That is worth telling the owner, because it exposes fields nobody asked for, but it is not a
    reason to refuse approval.
    """
    content = proposal()
    content["outputs"][0]["pointer"] = ""
    report = assess(content, inventory())
    assert report["ready"]
    assert "whole_response_output" in keys(report["findings"])


def test_two_outputs_reading_the_same_field_are_blocked():
    content = proposal()
    content["outputs"][1].update(step_id="s1", response_status="200", pointer="/items")
    assert "duplicate_output" in keys(assess(content, inventory())["findings"])


def test_a_required_input_left_unbound_is_blocked():
    content = proposal()
    content["steps"][1]["bindings"] = []
    assert "unbound_required_input" in keys(assess(content, inventory())["findings"])


def test_a_tool_that_repeats_an_existing_tool_is_blocked_whatever_it_is_called():
    """Same steps and bindings is the same tool, even under a different name."""
    existing = existing_tools([dict(proposal_id="other", state="ready_for_review", content=proposal())])
    report = assess(proposal(name="Totally different words"), inventory(), existing)
    assert not report["ready"]
    assert "duplicate_tool" in keys(report["findings"])
    # The comparison is on behavior, so renaming the proposal does not hide it.
    assert behavior(proposal()) == behavior(proposal(name="Totally different words"))


def test_a_rejected_tool_is_not_a_duplicate():
    existing = existing_tools([dict(proposal_id="other", state="rejected", content=proposal())])
    assert assess(proposal(), inventory(), existing)["ready"]


def test_the_same_title_as_an_existing_tool_is_a_warning_not_a_blocker():
    existing = existing_tools([dict(proposal_id="other", state="ready_for_review",
                                    content=dict(proposal(), steps=[], outputs=[]))])
    report = assess(proposal(), inventory(), existing)
    assert report["ready"] and "duplicate_title" in keys(report["findings"])


def test_a_low_model_score_blocks_even_without_deterministic_defects():
    content = proposal()
    content["self_review"]["criteria"][0]["score"] = 2
    report = assess(content, inventory())
    assert not report["ready"] and "low_self_score" in keys(report["findings"])


def test_a_model_asking_to_revise_blocks():
    content = proposal(self_review=dict(criteria=[{"criterion": c, "score": 4, "reason": "ok"} for c in CRITERIA],
                                        verdict="revise", summary="too broad"))
    report = assess(content, inventory())
    assert not report["ready"] and "self_verdict_revise" in keys(report["findings"])


def test_a_proposal_with_no_self_review_is_still_assessed_deterministically():
    content = proposal(self_review=None)
    report = assess(content, inventory())
    assert report["assessed"] is False and report["score"] is None
    assert report["ready"]  # nothing deterministically wrong
    assert report["verdict"] == "unrated"  # no model rating to go on


def test_defects_are_found_even_when_the_model_rates_perfectly():
    """The model's own rating never overrides what the application can establish."""
    content = proposal()
    content["self_review"] = {"criteria": [{"criterion": c, "score": 5, "reason": "excellent"} for c in CRITERIA],
                              "verdict": "proceed", "summary": "great"}
    content["outputs"][0]["pointer"] = ""
    content["steps"][1]["bindings"] = []  # a required input left unbound
    report = assess(content, inventory())
    assert not report["ready"] and report["score"] == 5


def test_structural_findings_name_the_step_and_input():
    content = proposal()
    content["steps"][1]["bindings"] = []
    finding = next(f for f in structural(content, inventory()) if f["key"] == "unbound_required_input")
    assert "s2" in finding["detail"] and "path.order_id" in finding["detail"]