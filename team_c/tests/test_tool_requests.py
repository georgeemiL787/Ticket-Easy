"""Owner-requested tools and capability suggestions.

MOCKED / AUTHORED: triage, suggestion and generation outputs come from an AUTHORED TEST SUBSTITUTE that
reads the operation ids from the payload it receives; reviews are labeled TEST-ONLY decisions and all data
lives in per-test databases with the in-process service-desk fixture. The live-model check is
scripts/request_live.py.
"""
import re
import pytest
from team_c import capabilities
from team_c.config import AppError
from team_c.models import GenerationOutput, RequestTriageOutput, SuggestionOutput
from team_c.providers import constrain, strict_schema
from team_c.web import create_app
from fastapi.testclient import TestClient
from test_openapi_primary import op
from test_publication import RevisingDesk, publish, with_evidence
from test_second_domain import LABEL, desk_proposal, make_desk, review_and_build, generated


class RequestDesk(RevisingDesk):
    """AUTHORED TEST SUBSTITUTE: scripted triage, suggestion and (optionally) generation outputs."""
    def __init__(self, settings, store):
        super().__init__(settings, store)
        self.triage, self.ideas, self.generations, self.payloads = [], [], [], []

    def call(self, kind, payload, output_model, run):
        self.payloads.append((kind, payload))
        if kind in ("request_triage", "suggestion") or (kind == "generation" and self.generations):
            self.store.attempt(run, "TEST_SUBSTITUTE", "authored-test-only", "succeeded")
            if kind == "request_triage":
                return RequestTriageOutput.model_validate(self.triage.pop(0))
            if kind == "suggestion":
                return SuggestionOutput.model_validate(dict(suggestions=self.ideas.pop(0)))
            return GenerationOutput.model_validate(self.generations.pop(0)(payload["inventory"], self.names))
        return super().call(kind, payload, output_model, run)


@pytest.fixture
def desk(tmp_path, monkeypatch):
    monkeypatch.setattr("test_second_domain.DeskSubstitute", RequestDesk)
    d = make_desk(tmp_path, "base")
    d.service = d.app.state.service
    inv, n = d.spec["inventory"], d.n
    d.lookup, d.create = op(inv, "GET", n["prefix"] + n["lookup"])["id"], op(inv, "POST", n["prefix"] + n["create"])["id"]
    d.health = op(inv, "GET", "/health")["id"]
    d.attach = next(o["id"] for o in inv["operations"] if not o["proposal_eligible"])
    d.bid = d.service.spec(d.spec["id"])["business_id"]
    yield d
    d.client.__exit__(None, None, None)


def triage(outcome, operation_ids=(), existing=(), questions=(), missing=()):
    return dict(outcome=outcome, summary="TEST-ONLY triage summary", operation_ids=list(operation_ids), existing_proposal_ids=list(existing),
                questions=list(questions), missing=[dict(kind=k, description=t, operation_ids=list(i)) for k, t, i in missing])


def idea(title, category, operation_ids=(), missing=(), related=()):
    return dict(title=title, purpose=f"TEST-ONLY purpose: {title}", benefit="TEST-ONLY benefit", business_reason="TEST-ONLY reason for this service desk",
                category=category, operation_ids=list(operation_ids), related_proposal_ids=list(related), relationship="TEST-ONLY relationship",
                missing=[dict(kind=k, description=t, operation_ids=list(i)) for k, t, i in missing])


def lookup_only(inv, n):
    """AUTHORED: a read-only lookup tool, used when an accepted suggestion names only the lookup operation."""
    lookup = op(inv, "GET", n["prefix"] + n["lookup"])
    return dict(proposals=[dict(name="Booking lookup", description="Find a booking by reference", business_purpose="Let customers check a booking",
                                steps=[dict(id="s1", operation_id=lookup["id"], purpose="Find the booking",
                                            bindings=[dict(target=f'query.{n["ref"]}', kind="runtime_argument", reference="booking_reference", step_id=None, response_status=None)])],
                                configuration=[], outputs=[dict(name="booking", step_id="s1", response_status="200", pointer=f'/{n["lookup_env"]}/{n["record"]}')],
                                questions=[], expected_reads=["Booking"], expected_writes=[], assumptions=[], limitations=[], risk="low", risk_rationale="Read only")],
                capability_gaps=[])


def ask(d, key, goal="TEST-ONLY: let a customer report a problem with their booking", **extra):
    return d.client.post(f"/api/v1/businesses/{d.bid}/tool-requests", json=dict(goal=goal, examples="TEST-ONLY: my shower is broken, booking ABC", idempotency_key=key, **extra))


def proposal_count(d):
    return len(d.service.store.all("SELECT id FROM proposals"))


def test_supported_request_becomes_a_grounded_proposal_in_normal_review(desk):
    d = desk
    d.provider.triage.append(triage("feasible", [d.lookup, d.create]))
    r = ask(d, "request-key-1")
    assert r.status_code == 200, r.text
    req = r.json()
    assert req["status"] == "proposed" and req["source"] == "owner" and len(req["proposal_ids"]) == 1 and req["unresolved"] == []
    [p] = req["proposals"]
    assert p["state"] == "needs_clarification" and not p["built"] and not p["published"]
    view = d.client.get(f'/api/v1/proposals/{p["proposal_id"]}').json()
    assert view["derived"]["owner_request_id"] == req["id"] and view["derived"]["generation_scope"] == [d.lookup, d.create] and view["decisions"] == []
    (k1, triage_payload), (k2, gen_payload) = d.provider.payloads
    assert (k1, k2) == ("request_triage", "generation") and "inventory" not in triage_payload
    assert {e["id"] for e in triage_payload["operation_index"]["operations"]} == {d.lookup, d.create, d.health, d.attach}
    assert all(set(e) == {"id", "method", "path", "summary", "status", "auth", "inputs", "returns"} for e in triage_payload["operation_index"]["operations"])
    assert gen_payload["owner_request"]["goal"] == req["goal"]
    assert req["coverage"]["partial"] is False and len(req["coverage"]["considered_ids"]) == 4
    # A repeated submission returns the same request without another model call; a reused key with other content is refused.
    assert ask(d, "request-key-1").json()["id"] == req["id"] and len(d.provider.payloads) == 2
    conflict = ask(d, "request-key-1", goal="TEST-ONLY: something else entirely")
    assert conflict.status_code == 409 and conflict.json()["code"] == "idempotency_conflict"
    assert ask(d, "request-key-2", approve=True).status_code == 422
    assert d.client.post(f'/api/v1/tool-requests/{req["id"]}/clarifications', json=dict(text="more")).json()["code"] == "request_closed"


def test_ambiguous_request_asks_questions_and_clarification_reprocesses(desk):
    d = desk
    d.provider.triage += [triage("needs_clarification", questions=["Should the tool file a service request, or only show the booking?"]),
                          triage("feasible", [d.lookup, d.create])]
    req = ask(d, "ambiguous-1", goal="TEST-ONLY: help customers with bookings").json()
    assert req["status"] == "needs_clarification" and req["proposal_ids"] == []
    assert req["unresolved"] == [dict(kind="question", description="Should the tool file a service request, or only show the booking?", operation_ids=[], source="triage")]
    empty = d.client.post(f'/api/v1/tool-requests/{req["id"]}/clarifications', json=dict(text=""))
    assert empty.status_code == 422 and empty.json()["code"] == "clarification_required"
    again = d.client.post(f'/api/v1/tool-requests/{req["id"]}/clarifications', json=dict(text="TEST-ONLY: file a service request")).json()
    assert again["status"] == "proposed" and len(again["proposal_ids"]) == 1 and [c["text"] for c in again["clarifications"]] == ["TEST-ONLY: file a service request"]
    assert d.provider.payloads[1][1]["owner_request"]["clarifications"] == ["TEST-ONLY: file a service request"]
    assert len(again["run_ids"]) == 3


def test_absent_operations_stay_unavailable_and_invented_ones_are_rejected(desk):
    d = desk
    d.provider.triage += [triage("unavailable", missing=[("absent_operation", "No operation cancels a booking", [])]),
                          triage("unavailable", missing=[("unsupported_operation", "Attachments cannot be executed", [d.attach])]),
                          triage("feasible", ["invented-operation"]),
                          triage("feasible", [d.attach]),
                          triage("unavailable", missing=[("absent_operation", "Claims lookup is missing", [d.lookup])]),
                          triage("unavailable", missing=[("missing_information", "Which bookings?", [])])]
    cancel = ask(d, "absent-1", goal="TEST-ONLY: let customers cancel a booking").json()
    assert cancel["status"] == "unavailable" and cancel["proposal_ids"] == [] and cancel["unresolved"][0]["kind"] == "absent_operation"
    assert [k for k, _ in d.provider.payloads] == ["request_triage"]
    photos = ask(d, "absent-2", goal="TEST-ONLY: let customers attach photos").json()
    assert photos["status"] == "unavailable" and photos["unresolved"][0]["operation_ids"] == [d.attach]
    for key in ("absent-3", "absent-4", "absent-5", "absent-6"):
        bad = ask(d, key).json()
        assert bad["status"] == "failed" and bad["error"]["code"] == "invalid_request_triage" and bad["proposal_ids"] == []
    assert proposal_count(d) == 0
    failed = d.service.store.all("SELECT status FROM runs WHERE kind='request_triage' ORDER BY created_at")
    assert [r["status"] for r in failed] == ["succeeded", "succeeded", "failed", "failed", "failed", "failed"]


def test_model_schema_limits_ids_to_the_index_and_known_tools():
    schema = strict_schema(RequestTriageOutput.model_json_schema())
    constrain(schema, "operation_ids", ["a", "b"])
    constrain(schema, "existing_proposal_ids", [])
    assert schema["properties"]["operation_ids"]["items"]["enum"] == ["a", "b"]
    assert schema["$defs"]["MissingCapability"]["properties"]["operation_ids"]["items"]["enum"] == ["a", "b"]
    assert schema["properties"]["existing_proposal_ids"]["maxItems"] == 0


def test_existing_equivalent_tools_are_recognized_not_duplicated(desk):
    d = desk
    pid = generated(d)
    d.provider.triage += [triage("existing_tool", existing=[pid]), triage("feasible", [d.lookup, d.create])]
    same = ask(d, "existing-1").json()
    assert same["status"] == "existing_tool" and [t["proposal_id"] for t in same["existing_tools"]] == [pid] and proposal_count(d) == 1
    # Even when triage misses it, the generated proposal matches the existing one step for step and is not stored.
    missed = ask(d, "existing-2").json()
    assert missed["status"] == "existing_tool" and missed["existing_proposal_ids"] == [pid] and missed["proposal_ids"] == [] and proposal_count(d) == 1
    run = d.client.get(f'/api/v1/proposal-runs/{missed["run_ids"][-1]}').json()
    assert run["result"]["duplicates"] == [pid]
    # Observed live: triage called a cancellation feasible, generation reported the gap and repeated the existing tool.
    d.provider.triage.append(triage("feasible", [d.lookup, d.create]))
    d.provider.generations.append(lambda inv, n: dict(proposals=[desk_proposal(inv, n)], capability_gaps=[dict(requested_capability="cancel a booking", explanation="No operation cancels a booking")]))
    gap = ask(d, "existing-3", goal="TEST-ONLY: let customers cancel a booking").json()
    assert gap["status"] == "unavailable" and gap["existing_proposal_ids"] == [pid] and gap["unresolved"][-1]["kind"] == "capability_gap" and proposal_count(d) == 1


def test_suggestions_are_grounded_and_decisions_touch_only_the_suggestion(desk):
    d = desk
    pid = generated(d)
    a = review_and_build(d, pid)
    with_evidence(d, a["id"])
    pub = publish(d, a["id"]).json()
    d.provider.ideas.append([
        idea("Report a booking problem", "feasible", [d.lookup, d.create]),
        idea("Service status for staff", "feasible", [d.health]),
        idea("Booking lookup for customers", "needs_clarification", [d.lookup], [("missing_information", "Which booking fields may customers see?", [])]),
        idea("Photo attachments", "feasible", [d.attach]),
        idea("Cancel a booking", "blocked_by_missing_api", missing=[("absent_operation", "No operation cancels a booking", [])]),
        idea("Invented refunds", "feasible", ["refund-operation"]),
        idea("Cancel a booking", "blocked_by_missing_api", missing=[("absent_operation", "Duplicate within the batch", [])]),
    ])
    r = d.client.post(f"/api/v1/businesses/{d.bid}/suggestion-runs", json=dict(count=4))
    assert r.status_code == 200, r.text
    batch = r.json()
    got = {s["content"]["title"]: s for s in batch["suggestions"]}
    assert list(got) == ["Service status for staff", "Booking lookup for customers", "Photo attachments", "Cancel a booking"]
    assert [s["category"] for s in got.values()] == ["feasible", "needs_clarification", "blocked_by_missing_api", "blocked_by_missing_api"]
    assert got["Photo attachments"]["content"]["category_adjusted_from"] == "feasible"
    assert got["Photo attachments"]["content"]["missing"][0] == dict(kind="unsupported_operation", description=f"{d.attach} is not executable by Team C", operation_ids=[d.attach])
    assert batch["recognized"] == [dict(title="Report a booking problem", proposal_id=pid, name="Service request for a booking")]
    assert {w["title"]: w["reason"] for w in batch["withheld"]} == {"Invented refunds": "cites an operation outside the reviewed index", "Cancel a booking": "duplicates an earlier suggestion"}
    assert batch["coverage"]["considered_ids"] and batch["coverage"]["partial"] is False and batch["requested"] == 4
    decide = lambda s, **body: d.client.post(f'/api/v1/suggestions/{s["id"]}/decision', json=body)
    # Dismissal changes only the suggestion: the existing proposal, artifact and publication stay as they were.
    before = (d.client.get(f"/api/v1/proposals/{pid}").json()["state"], d.client.get(f'/api/v1/artifacts/{a["id"]}').json()["sha256"])
    assert decide(got["Service status for staff"], action="dismiss", note=LABEL + "not needed").json()["suggestion"]["status"] == "dismissed"
    assert (d.client.get(f"/api/v1/proposals/{pid}").json()["state"], d.client.get(f'/api/v1/artifacts/{a["id"]}').json()["sha256"]) == before
    assert d.client.get(f'/api/v1/publications/{pub["id"]}').json()["effective_status"] == "published"
    assert decide(got["Service status for staff"], action="accept").json()["code"] == "suggestion_closed"
    blocked = decide(got["Photo attachments"], action="accept")
    assert blocked.status_code == 409 and blocked.json()["code"] == "suggestion_blocked"
    assert decide(got["Booking lookup for customers"], action="accept").json()["code"] == "clarification_required"
    # Acceptance starts the normal proposal workflow (no triage, scope = the suggestion's operations); it is not approval.
    d.provider.generations.append(lookup_only)
    calls = len(d.provider.payloads)
    accepted = decide(got["Booking lookup for customers"], action="accept", clarification=LABEL + "reference and dates only").json()
    req = accepted["request"]
    assert accepted["suggestion"]["status"] == "accepted" and accepted["suggestion"]["request_id"] == req["id"]
    assert req["source"] == "suggestion" and req["status"] == "proposed" and req["operation_scope"] == [d.lookup]
    assert [k for k, _ in d.provider.payloads[calls:]] == ["generation"]
    [new] = req["proposals"]
    assert new["state"] == "needs_clarification" and not new["built"] and d.client.get(f'/api/v1/proposals/{new["proposal_id"]}').json()["decisions"] == []
    assert d.client.post(f'/api/v1/proposals/{new["proposal_id"]}/artifacts', json=dict(connector_id="desk")).status_code in (409, 422)
    assert decide(got["Booking lookup for customers"], action="accept").json()["request"]["id"] == req["id"]
    revised = decide(got["Cancel a booking"], action="revise", title="TEST-ONLY: cancel or reschedule a booking").json()
    assert revised["revised"]["status"] == "revised" and revised["suggestion"]["parent_id"] == got["Cancel a booking"]["id"]
    assert revised["suggestion"]["content"]["revised_by_owner"] is True and revised["suggestion"]["category"] == "blocked_by_missing_api"
    # A later run does not bring back dismissed or accepted ideas.
    d.provider.ideas.append([idea("Service status for staff", "feasible", [d.health]), idea("Booking lookup for customers", "needs_clarification", [d.lookup], [("missing_information", "x", [])])])
    again = d.client.post(f"/api/v1/businesses/{d.bid}/suggestion-runs", json={}).json()
    assert again["suggestions"] == [] and {w["reason"] for w in again["withheld"]} == {"duplicates an earlier suggestion"}
    assert again["requested"] == d.service.settings.suggestion_count


def test_restricted_operations_stay_restricted_and_budget_limits_are_reported(desk):
    ops = [dict(id="read", method="GET", path="/things", proposal_eligible=True, inputs={}, responses={}),
           dict(id="admin", method="DELETE", path="/admin/things", proposal_eligible=False, exposure=dict(classification="restricted"), inputs={}, responses={}),
           dict(id="upload", method="POST", path="/files", proposal_eligible=False, exposure=dict(classification="technically_unsupported"), inputs={}, responses={})]
    index, coverage = capabilities.operation_index(dict(operations=ops), 10_000)
    assert [e["status"] for e in index] == ["eligible", "restricted", "unsupported"] and not coverage["partial"]
    out = SuggestionOutput.model_validate(dict(suggestions=[
        idea("Admin cleanup", "feasible", ["admin"]),
        idea("Blocked cleanup", "blocked_by_missing_api", missing=[("restricted_operation", "Deleting is restricted", ["admin"])]),
        idea("Mislabelled", "needs_clarification", ["read"]),
        idea("Read A", "feasible", ["read"]), idea("Read B", "feasible", ["read"])]))
    kept, withheld, _ = capabilities.screen(out, index, [], [], 2)
    assert [s["title"] for s in kept] == ["Blocked cleanup", "Read A"] and kept[0]["category"] == "blocked_by_missing_api"
    assert {w["title"]: w["reason"] for w in withheld} == {"Admin cleanup": "uses a restricted operation; restricted operations stay restricted",
                                                          "Mislabelled": "labelled needs_clarification without naming what is missing",
                                                          "Read B": "duplicates an earlier suggestion"}
    with pytest.raises(AppError, match="not an eligible operation"):
        capabilities.check_triage(RequestTriageOutput.model_validate(triage("feasible", ["admin"])), index, [])
    capabilities.check_triage(RequestTriageOutput.model_validate(triage("unavailable", missing=[("restricted_operation", "restricted", ["admin"])])), index, [])
    small, cut = capabilities.operation_index(dict(operations=ops), 150)
    assert [e["id"] for e in small] == ["read"] and cut["partial"] and cut["omitted_ids"] == ["admin", "upload"]
    # Through the service: the batch records which operations were considered and that coverage was incomplete.
    d = desk
    d.service.settings.capability_index_chars = 250
    d.provider.ideas.append([idea("Service status for staff", "feasible", [d.health])])
    batch = d.client.post(f"/api/v1/businesses/{d.bid}/suggestion-runs", json={}).json()
    sent = d.provider.payloads[-1][1]["operation_index"]
    assert batch["coverage"]["partial"] and sent["coverage"]["partial"] and len(sent["operations"]) == batch["coverage"]["considered"] < 4
    assert set(batch["coverage"]["omitted_ids"]) | set(batch["coverage"]["considered_ids"]) == {d.lookup, d.create, d.health, d.attach}


def test_requests_and_suggestion_decisions_survive_a_restart(desk):
    d = desk
    d.provider.triage.append(triage("feasible", [d.lookup, d.create]))
    req = ask(d, "restart-1").json()
    d.provider.ideas.append([idea("Service status for staff", "feasible", [d.health])])
    [s] = d.client.post(f"/api/v1/businesses/{d.bid}/suggestion-runs", json={}).json()["suggestions"]
    d.client.post(f'/api/v1/suggestions/{s["id"]}/decision', json=dict(action="dismiss", note=LABEL + "not needed"))
    with TestClient(create_app(d.service.settings, RequestDesk)) as fresh:
        [again] = fresh.get(f"/api/v1/businesses/{d.bid}/tool-requests").json()
        assert (again["id"], again["status"], again["proposal_ids"]) == (req["id"], "proposed", req["proposal_ids"])
        stored = fresh.get(f"/api/v1/businesses/{d.bid}/suggestions").json()
        assert [(x["id"], x["status"], x["decision_note"]) for x in stored["suggestions"]] == [(s["id"], "dismissed", LABEL + "not needed")]
        assert len(stored["batches"]) == 1


def test_business_page_request_form_is_submitted_once(desk):
    d = desk
    page = d.client.get(f"/businesses/{d.bid}").text
    assert "Request a tool" in page and "Suggest additional tools" in page
    csrf, key = re.search(r'name="csrf" value="([^"]+)"', page)[1], re.search(r'name="request_key" value="([^"]+)"', page)[1]
    d.provider.triage.append(triage("needs_clarification", questions=["TEST-ONLY question: which bookings?"]))
    form = dict(csrf=csrf, business_id=d.bid, request_key=key, goal="TEST-ONLY: help customers with bookings", examples="")
    for _ in range(2):
        assert d.client.post("/actions/request_tool", data=form, follow_redirects=False).status_code == 303
    assert len(d.client.get(f"/api/v1/businesses/{d.bid}/tool-requests").json()) == 1
    page = d.client.get(f"/businesses/{d.bid}").text
    assert "Needs clarification" in page and "TEST-ONLY question: which bookings?" in page and "Send and process again" in page
    d.provider.ideas.append([idea("Service status for staff", "feasible", [d.health])])
    assert d.client.post("/actions/suggest", data=dict(csrf=csrf, business_id=d.bid, count="1"), follow_redirects=False).status_code == 303
    [s] = d.client.get(f"/api/v1/businesses/{d.bid}/suggestions").json()["suggestions"]
    assert d.client.post("/actions/decide_suggestion", data=dict(csrf=csrf, suggestion_id=s["id"], decision="dismiss", note=LABEL + "no"), follow_redirects=False).status_code == 303
    assert "Service status for staff" in d.client.get(f"/businesses/{d.bid}").text
    assert d.client.get(f"/api/v1/businesses/{d.bid}/suggestions").json()["suggestions"][0]["status"] == "dismissed"
