"""Sandbox publication helpers: the revising substitute, record-scope evidence, publish and revise."""
from team_c.models import GenerationOutput
from helpers.desk import DeskSubstitute, LABEL, arguments, current, desk_proposal

OWNER = dict(status="succeeded", outputs_present=["ticket"])
DENIED = dict(status="failed", failure_step="s1", failure_outcome="blocked_by_access_check")


class RevisingDesk(DeskSubstitute):
    """AUTHORED TEST SUBSTITUTE that also answers owner revisions with the intended wiring."""
    def call(self, kind, payload, output_model, run):
        if kind.startswith("revision"):
            self.store.attempt(run, "TEST_SUBSTITUTE", "authored-test-only", "succeeded")
            return GenerationOutput(proposals=[desk_proposal(payload["inventory"], self.names, suffix=" (revised)")], capability_gaps=[])
        return super().call(kind, payload, output_model, run)


def publish(d, aid):
    return d.client.post(f"/api/v1/artifacts/{aid}/publications", json=dict(note=LABEL + "sandbox publication"))


def scenario_test(d, aid, name, expect, who, scenario, record_owner=None, **args):
    r = d.client.post(f"/api/v1/artifacts/{aid}/sandbox-tests", json=dict(name=name, identity=who, arguments=args or arguments(d, who), expect=expect,
                                                                         scenario=scenario, record_owner=record_owner))
    assert r.status_code == 200, r.text
    return r.json()


def with_evidence(d, aid):
    """Explicit record-scope scenario: both identities use their own records; bob is refused on alice's record."""
    assert scenario_test(d, aid, "owner_files_request", OWNER, "alice", "own_record")["verdict"] == "passed"
    assert scenario_test(d, aid, "bob_files_own_request", OWNER, "bob", "own_record")["verdict"] == "passed"
    assert scenario_test(d, aid, "cross_user_request", DENIED, "bob", "cross_user", "alice", **arguments(d, "alice"))["verdict"] == "passed"


def revise(d, pid):
    p = current(d, pid)
    r = d.client.post(f"/api/v1/proposals/{pid}/revisions", json=dict(expected_revision=p["review_revision"], instruction="TEST-ONLY revision"))
    assert r.status_code == 200, r.text
