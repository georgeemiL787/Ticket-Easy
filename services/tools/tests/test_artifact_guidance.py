"""Guides the owner through what is left, instead of only stating that something is missing."""
import pytest

from team_c import publishing


def test_guidance_lists_the_steps_in_order_and_names_the_action():
    content = {"name": "Order lookup", "access_requirements": [
        {"id": "rs", "kind": "record_scope", "enforcement": {"unrestricted": False}}],
        "access_policy": {"principals": ["end_user"]}}
    steps = publishing.next_steps(content, [], [], [], None, False)
    assert [s["key"] for s in steps] == ["identity", "success", "own_record", "cross_user", "guards", "publish"]
    assert all(not s["done"] and s["action"] for s in steps)
    # The cross-owner step must say how to obtain one; it is the least discoverable requirement.
    cross = next(s for s in steps if s["key"] == "cross_user")
    assert "second identity" in cross["action"] and "record_owner" in cross["action"]


def test_guidance_marks_completed_work_as_done_and_drops_its_action():
    content = {"name": "T", "access_requirements": [
        {"id": "rs", "kind": "record_scope", "enforcement": {"unrestricted": False}}],
        "access_policy": {"principals": ["end_user"]}}
    tests = [
        {"id": "1", "verdict": "passed", "scenario": "own_record", "execution_id": "e1",
         "expectation": {"status": "succeeded"}},
        {"id": "2", "verdict": "passed", "scenario": "cross_user", "execution_id": "e2",
         "expectation": {"status": "failed"}},
    ]
    executions = [
        {"id": "e1", "report": {"status": "succeeded"}},
        {"id": "e2", "report": {"status": "failed", "failure": {"step_id": "s2", "outcome": "blocked_by_access_check"},
                               "trace": [{"step_id": "s2", "http_status": 200}]}},
    ]
    steps = publishing.next_steps(content, tests, executions, ["alice", "bob"],
                                  {"passed": True}, False)
    done = {s["key"] for s in steps if s["done"]}
    assert {"identity", "success", "own_record", "cross_user", "guards"} <= done
    assert next(s for s in steps if s["key"] == "publish")["action"]
    # A finished step must not keep nagging.
    assert all(not s["action"] for s in steps if s["done"])


def test_guidance_omits_cross_user_when_no_record_scope_is_enforced():
    content = {"name": "T", "access_requirements": [
        {"id": "ca", "kind": "caller_access", "enforcement": {}}],
        "access_policy": {"principals": ["end_user"]}}
    keys = [s["key"] for s in publishing.next_steps(content, [], [], [], None, False)]
    assert "cross_user" not in keys and "own_record" not in keys


def test_guidance_treats_an_unrestricted_scope_as_needing_no_cross_owner_test():
    content = {"name": "T", "access_requirements": [
        {"id": "rs", "kind": "record_scope", "enforcement": {"unrestricted": True}}],
        "access_policy": {"principals": ["end_user"]}}
    keys = [s["key"] for s in publishing.next_steps(content, [], [], [], None, False)]
    assert "cross_user" not in keys


def test_guidance_reports_a_completed_publication():
    content = {"name": "T", "access_requirements": [], "access_policy": {"principals": ["end_user"]}}
    steps = publishing.next_steps(content, [], [], ["alice"], {"passed": True}, True)
    assert next(s for s in steps if s["key"] == "publish")["done"]


def test_artifact_built_from_a_corrected_spec_says_why_it_cannot_run():
    """An artifact whose declared login issues only admin tokens cannot satisfy an end-user policy.

    Saying so plainly beats an endless "cannot publish yet".
    """
    content = {"name": "Old", "access_policy": {"principals": ["end_user", "resource_owner"]},
               "connector": {"auth": {"type": "oauth2_password", "token_path": "/api/v1/admin/login"}}}
    message = publishing.unrunnable(content, ["alice"])
    assert message and "/api/v1/admin/login" in message and "end_user" in message
    assert "Rebuild" in message


def test_artifact_with_a_matching_login_is_not_flagged():
    content = {"name": "New", "access_policy": {"principals": ["end_user"]},
               "connector": {"auth": {"type": "oauth2_password", "token_path": "/api/v1/customers/login"}}}
    assert publishing.unrunnable(content, ["alice"]) is None
    # No identity configured is a setup gap, not a dead artifact.
    assert "test identity" in publishing.unrunnable(
        {"name": "N", "access_policy": {"principals": ["end_user"]},
         "connector": {"auth": {"type": "oauth2_password", "token_path": "/api/v1/customers/login"}}}, [])


def test_unrunnable_ignores_policies_that_allow_admin():
    content = {"name": "T", "access_policy": {"principals": ["admin", "end_user"]},
               "connector": {"auth": {"type": "oauth2_password", "token_path": "/api/v1/admin/login"}}}
    assert publishing.unrunnable(content, ["root"]) is None