"""Review lifecycle scenarios on a target-shaped OpenAPI 3.1 inventory.

The model is a deterministic TEST SUBSTITUTE and every owner answer is a labeled TEST-ONLY value,
not a business decision.
"""
import json
import pytest
from fastapi.testclient import TestClient
from conftest import requirement_findings
from test_openapi_primary import target_spec, inventory, op
from team_c.config import AppError, Settings
from team_c.grounding import response_only_fields, validate_proposal
from team_c.models import GenerationOutput, ProposalContent, ReconciliationOutput
from team_c.web import create_app

ANSWER = "TEST-ONLY (not a business decision): only the authenticated owner of an item may read or update it."
OWNER_ID_QUESTION = "What is the exact business setting for the owner_id?"


def live_shaped(read, update, questions=True):
    runtime = lambda target, ref: dict(target=target, kind="runtime_argument", reference=ref, step_id=None, response_status=None)
    return dict(name="Item lookup and update", description="Look up an item and update its title/description", business_purpose="Let owners maintain items",
                steps=[dict(id="s1", operation_id=read["id"], purpose="Look up the item", bindings=[runtime("path.id", "item_id")]),
                       dict(id="s2", operation_id=update["id"], purpose="Update the item", bindings=[runtime("path.id", "item_id"), runtime("body.title", "title"), runtime("body.description", "description")])],
                configuration=[], outputs=[dict(name="item", step_id="s1", response_status="200", pointer="/description"), dict(name="updated_item", step_id="s2", response_status="200", pointer="/title")],
                questions=[dict(id="q1", text="How will ownership be verified before accessing private records?", configuration_key=None), dict(id="q2", text=OWNER_ID_QUESTION, configuration_key=None)] if questions else [],
                expected_reads=["Item"], expected_writes=["Item title/description"], assumptions=[], limitations=["Runtime authorization unverified"], risk="medium", risk_rationale="Updates records")


class LifecycleSubstitute:
    """TEST SUBSTITUTE: labeled TEST-ONLY answers resolve, 'both' contradicts, anything else is insufficient."""
    def __init__(self, settings, store):
        self.store, self.mode, self.questions = store, "normal", True

    def call(self, kind, payload, output_model, run):
        self.store.attempt(run, "TEST_SUBSTITUTE", "deterministic-test-only", "succeeded")
        ops = {(o["method"], o["path"]): o for o in payload["inventory"]["operations"]}
        if kind == "generation":
            return GenerationOutput(proposals=[live_shaped(ops["GET", "/api/v1/items/{id}"], ops["PUT", "/api/v1/items/{id}"], self.questions)], capability_gaps=[])
        if kind.startswith("revision"):
            return GenerationOutput(proposals=[dict(payload["proposal"], name="Revised item tool")], capability_gaps=[])
        content, answers = payload["proposal"]["content"], payload["proposal"]["answers"]
        findings = []
        for q in content["questions"]:
            a = answers.get(q["id"])
            text = a["text"] if a else ""
            status = "contradictory" if "both" in text else "resolved" if text.startswith("TEST-ONLY") else "insufficient"
            findings.append(dict(question_id=q["id"], status=status, explanation="Test substitute assessment", answer_revision_ids=[a["id"]] if a else []))
        findings += requirement_findings(payload)
        revised = json.loads(json.dumps(content))
        if self.mode == "invent_operation":
            revised["steps"][0]["operation_id"] = "made-up-operation"
        elif self.mode == "invent_field":
            revised["steps"][1]["bindings"].append(dict(target="body.owner_id", kind="runtime_argument", reference="owner_id", step_id=None, response_status=None))
        elif all(f["status"] == "resolved" for f in findings) and not any(x.startswith("Owner answered") for x in content["assumptions"]):
            revised["assumptions"].append("Owner answered: only an item's owner may read or update it (TEST-ONLY)")
        else:
            revised = None
        return ReconciliationOutput(findings=findings, revised_proposal=revised, capability_gaps=[])


@pytest.fixture
def lifecycle(tmp_path):
    settings = Settings(_env_file=None, database_path=str(tmp_path / "lifecycle.db"), session_secret="test-secret", openrouter_api_key="")
    app = create_app(settings, LifecycleSubstitute)
    with TestClient(app) as client:
        b = client.post("/api/v1/businesses", json=dict(name="Items (test)", description="Test business")).json()
        spec = client.post(f'/api/v1/businesses/{b["id"]}/specifications', files={"file": ("openapi.json", json.dumps(target_spec()).encode())}).json()
        scope = [op(spec["inventory"], m, "/api/v1/items/{id}")["id"] for m in ("GET", "PUT")]
        yield app, client, settings, spec, scope


def generate(client, spec, scope):
    r = client.post(f'/api/v1/inventories/{spec["id"]}/proposal-runs', json=dict(operation_ids=scope))
    assert r.status_code == 200, r.text
    return r.json()["proposal_ids"][0]


def current(client, pid):
    return client.get(f"/api/v1/proposals/{pid}").json()


def post(client, pid, suffix, **body):
    p = current(client, pid)
    return client.post(f'/api/v1/proposals/{pid}/versions/{p["version"]}/{suffix}', json=dict(expected_revision=p["review_revision"], **body))


def answer_all(client, pid, text=ANSWER, requirement_text=ANSWER):
    p = current(client, pid)
    answers = {q["id"]: text for q in p["content"]["questions"]} | {r["id"]: requirement_text for r in p["requirements"]}
    r = post(client, pid, "answers", answers=answers)
    assert r.status_code == 200, r.text
    return r.json()


def approve(client, pid, key="lifecycle-approval"):
    return post(client, pid, "decisions", action="approve_to_build", reason="TEST-ONLY approval", idempotency_key=key)


def confirm_all(client, pid):
    for r in current(client, pid)["requirements"]:
        if r["status"] == "owner_confirmed":
            continue
        response = post(client, pid, f'requirements/{r["id"]}/confirm')
        assert response.status_code == 200, response.text


def supersede_q2(client, pid):
    r = post(client, pid, "questions/q2/supersede", reason="TEST-ONLY review: owner_id is a response-only field; ownership is covered by the record-scope requirement.")
    assert r.status_code == 200, r.text
    return r.json()


def test_inventory_exposes_distinct_state_fields(lifecycle):
    app, client, _, spec, _ = lifecycle
    inv = client.get(f'/api/v1/specifications/{spec["id"]}/inventory').json()["inventory"]
    assert inv["document_valid"] and inv["eligible_operation_count"] == inv["summary"]["proposal_eligible"] > 0
    assert inv["proposal_generation_ready"] is inv["valid"] is True
    # Records stored before these fields existed are filled on read.
    with app.state.store.connect(write=True) as c:
        old = {k: v for k, v in inv.items() if k not in ("eligible_operation_count", "proposal_generation_ready")}
        c.execute("UPDATE specifications SET inventory=? WHERE id=?", (json.dumps(old), spec["id"]))
    filled = client.get(f'/api/v1/specifications/{spec["id"]}/inventory').json()["inventory"]
    assert filled["eligible_operation_count"] == inv["eligible_operation_count"] and filled["proposal_generation_ready"]


def test_response_only_question_field_is_flagged_generically():
    for field in ("owner_id", "holder_key"):
        doc = json.loads(json.dumps(target_spec()).replace("owner_id", field))
        inv = inventory(doc)
        content = live_shaped(op(inv, "GET", "/api/v1/items/{id}"), op(inv, "PUT", "/api/v1/items/{id}"))
        content["questions"][1]["text"] = OWNER_ID_QUESTION.replace("owner_id", field)
        ops = {o["id"]: o for o in inv["operations"]}
        validate_proposal(ProposalContent(**content), inv)
        review = response_only_fields(ProposalContent(**content), ops)
        assert [(n["question_id"], n["field"]) for n in review] == [("q2", field)]
        assert sorted(review[0]["returned_by"]) == ["GET /api/v1/items/{id} 200", "PUT /api/v1/items/{id} 200"]
    # A field the steps accept as input (title) is not flagged.
    content["questions"][1]["text"] = "Which title should be used?"
    assert response_only_fields(ProposalContent(**content), ops) == []


def test_invalid_question_is_superseded_with_history(lifecycle):
    _, client, _, spec, scope = lifecycle
    pid = generate(client, spec, scope)
    p = current(client, pid)
    assert [n["question_id"] for n in p["question_review"]] == ["q2"]
    assert post(client, pid, "questions/q2/supersede", reason="   ").status_code == 422
    assert supersede_q2(client, pid)["version"] == 2
    v1, v2 = client.get(f"/api/v1/proposals/{pid}?version=1").json(), current(client, pid)
    assert v1["state"] == "superseded" and [q["id"] for q in v1["content"]["questions"]] == ["q1", "q2"]
    assert [q["id"] for q in v2["content"]["questions"]] == ["q1"] and v2["question_review"] == []
    assert v2["supersessions"][0]["question"] == OWNER_ID_QUESTION and "response-only" in v2["supersessions"][0]["reason"]
    assert any(line.startswith("Question q2 removed") and "superseded" in line for line in v2["change_summary"])
    assert v2["state"] == "needs_clarification"


def test_reconciliation_contract_requires_a_finding_per_requirement(lifecycle, tmp_path):
    import httpx
    from team_c.providers import Providers
    app, client, _, spec, scope = lifecycle
    pid = generate(client, spec, scope)
    answer_all(client, pid, text="first answer", requirement_text="first answer")
    answer_all(client, pid)
    service = app.state.service
    view = service.view(pid)
    sent = []
    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(500)
    settings = Settings(_env_file=None, database_path=str(tmp_path / "x.db"), ollama_context=200_000, llm_fallback="none")
    service.providers = Providers(settings, app.state.store, httpx.MockTransport(handler))
    with pytest.raises(AppError):
        service.reconcile(pid, view["version"], view["review_revision"])
    schema = sent[0]["format"]
    ids = ["q1", "q2"] + [r["id"] for r in view["requirements"]]
    assert schema["$defs"]["Finding"]["properties"]["question_id"]["enum"] == ids
    assert schema["$defs"]["Question"]["properties"]["id"]["enum"] == ["q1", "q2"]
    assert schema["properties"]["findings"]["minItems"] == schema["properties"]["findings"]["maxItems"] == len(ids)
    user = sent[0]["messages"][1]["content"]
    data = json.loads(user.split("UNTRUSTED_DATA\n", 1)[1].rsplit("\nEND_UNTRUSTED_DATA", 1)[0])
    assert [r["id"] for r in data["requirements"]] == [r["id"] for r in view["requirements"]]
    # Latest answers appear once (proposal.answers); only superseded revisions are repeated as history.
    latest = {a["id"] for a in data["proposal"]["answers"].values()}
    assert len(latest) == len(ids) and {a["text"] for a in data["earlier_answers"]} == {"first answer"} and not latest & {a["id"] for a in data["earlier_answers"]}


def test_proposal_page_shows_requirements_and_html_supersession(lifecycle):
    import re
    _, client, _, spec, scope = lifecycle
    pid = generate(client, spec, scope)
    page = client.get(f"/proposals/{pid}").text
    assert "Access and identity requirements" in page and "Runtime-ready: <strong>no</strong>" in page and "Question review:" in page
    csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
    p = current(client, pid)
    r = client.post("/actions/supersede_question", data=dict(csrf=csrf, proposal_id=pid, version=p["version"], revision=p["review_revision"], question_id="q2", reason="TEST-ONLY: response-only field"))
    assert r.status_code == 200 and "Changes from version 1" in r.text and "Question q2 removed" in r.text


def test_requirements_block_approval_even_when_the_model_asks_nothing(lifecycle):
    app, client, _, spec, scope = lifecycle
    app.state.service.providers.questions = False
    pid = generate(client, spec, scope)
    p = current(client, pid)
    assert p["content"]["questions"] == [] and p["state"] == "needs_clarification"
    assert {r["kind"] for r in p["requirements"]} == {"caller_access", "record_scope"}
    assert all(r["status"] == "unanswered" and r["evidence"]["runtime"] == "awaiting_implementation" for r in p["requirements"])
    # Unanswered: reconciliation cannot make it ready and approval is rejected.
    assert post(client, pid, "reconcile").json()["state"] == "needs_clarification"
    assert approve(client, pid).status_code == 422
    # Nonempty answer plus a model "resolved" still is not proof without owner confirmation.
    answer_all(client, pid)
    confirm = post(client, pid, f'requirements/{p["requirements"][0]["id"]}/confirm')
    assert confirm.status_code == 422 and confirm.json()["code"] == "confirmation_blocked"
    r = post(client, pid, "reconcile").json()
    assert r["material_change"]
    assert post(client, pid, "reconcile").json()["state"] == "ready_for_review"
    blocked = approve(client, pid)
    assert blocked.status_code == 422 and set(blocked.json()["details"]["requirements"]) == {r["id"] for r in p["requirements"]}
    confirm_all(client, pid)
    assert approve(client, pid).status_code == 200


@pytest.mark.parametrize("text,status", [("yes", "insufficient"), ("", "insufficient"), ("TEST-ONLY: both the owner and any logged-in user", "contradictory")])
def test_insufficient_or_contradictory_answers_stay_unresolved(lifecycle, text, status):
    _, client, _, spec, scope = lifecycle
    pid = generate(client, spec, scope)
    supersede_q2(client, pid)
    answer_all(client, pid, requirement_text=text)
    assert post(client, pid, "reconcile").json()["state"] == "needs_clarification"
    p = current(client, pid)
    expected = "unanswered" if not text else status
    assert {r["status"] for r in p["requirements"]} == {expected}
    assert post(client, pid, f'requirements/{p["requirements"][0]["id"]}/confirm').status_code == 422
    assert approve(client, pid).status_code == 422


def test_clarification_creates_new_version_and_reruns_grounding(lifecycle):
    _, client, _, spec, scope = lifecycle
    pid = generate(client, spec, scope)
    supersede_q2(client, pid)
    answer_all(client, pid)
    r = post(client, pid, "reconcile").json()
    assert r == dict(r, version=3, state="needs_reconciliation", material_change=True)
    v2, v3 = client.get(f"/api/v1/proposals/{pid}?version=2").json(), current(client, pid)
    assert v2["state"] == "superseded" and v2["content"]["assumptions"] == []
    assert "authorization_review" in v3["derived"] and v3["derived"]["source_operations"] == [s["operation_id"] for s in v3["content"]["steps"]]
    assert any(line.startswith("assumptions added") for line in v3["change_summary"])
    # Copied answers are new evidence: the copies need a fresh assessment and confirmation.
    assert {r["status"] for r in v3["requirements"]} == {"awaiting_reconciliation"}
    assert approve(client, pid).status_code == 422


@pytest.mark.parametrize("mode", ["invent_operation", "invent_field"])
def test_invented_operation_or_field_is_rejected_and_prior_version_preserved(lifecycle, mode):
    app, client, _, spec, scope = lifecycle
    pid = generate(client, spec, scope)
    supersede_q2(client, pid)
    answer_all(client, pid)
    before = current(client, pid)
    app.state.service.providers.mode = mode
    r = post(client, pid, "reconcile")
    assert r.status_code == 422 and r.json()["code"] == "invalid_bindings"
    after = current(client, pid)
    assert (after["version"], after["state"], after["content"], after["review_revision"]) == (before["version"], before["state"], before["content"], before["review_revision"])
    assert len(after["versions"]) == 2 and after["reconciliations"] == []
    assert client.get(f'/api/v1/proposal-runs/{r.json()["details"]["run_id"]}').json()["status"] == "failed"


def test_approval_duplicate_revision_and_restart(lifecycle):
    app, client, settings, spec, scope = lifecycle
    pid = generate(client, spec, scope)
    supersede_q2(client, pid)
    answer_all(client, pid)
    post(client, pid, "reconcile")
    assert post(client, pid, "reconcile").json()["state"] == "ready_for_review"
    assert approve(client, pid).status_code == 422
    confirm_all(client, pid)
    # Changing a confirmed answer invalidates the confirmation.
    rid = current(client, pid)["requirements"][0]["id"]
    post(client, pid, "answers", answers={rid: ANSWER + " (edited)"})
    assert next(r for r in current(client, pid)["requirements"] if r["id"] == rid)["status"] == "awaiting_reconciliation"
    assert approve(client, pid).status_code == 422
    assert post(client, pid, "reconcile").json()["state"] == "ready_for_review"
    confirm_all(client, pid)
    d = approve(client, pid)
    assert d.status_code == 200, d.text
    p = current(client, pid)
    assert p["state"] == "approved_to_build" and p["lifecycle"] == dict(p["lifecycle"], approved_to_build=True, requirements_confirmed=True, runtime_ready=False)
    snapshot = json.loads(d.json()["snapshot"])
    assert snapshot["runtime_ready"] is False and {r["status"] for r in snapshot["requirements"]} == {"owner_confirmed"}
    # Duplicate approval: same key or a new key for the same action returns the one decision.
    assert approve(client, pid).json()["id"] == d.json()["id"]
    assert approve(client, pid, key="another-approval-key").json()["id"] == d.json()["id"]
    assert len(current(client, pid)["decisions"]) == 1
    # A later revision needs fresh answers, confirmations and approval.
    rev = client.post(f"/api/v1/proposals/{pid}/revisions", json=dict(expected_revision=p["review_revision"], instruction="Use a clearer name"))
    assert rev.status_code == 200 and rev.json()["version"] == 4 and rev.json()["state"] == "needs_clarification"
    assert {r["status"] for r in current(client, pid)["requirements"]} == {"unanswered"}
    assert approve(client, pid, key="v4-approval").status_code == 422
    with TestClient(create_app(settings, LifecycleSubstitute)) as restarted:
        p = restarted.get(f"/api/v1/proposals/{pid}?version=3").json()
        assert [v["state"] for v in p["versions"]] == ["superseded", "superseded", "superseded", "needs_clarification"]
        assert len(p["decisions"]) == 1 and p["decisions"][0]["version"] == 3
        assert p["answer_history"] and p["supersessions"][0]["question_id"] == "q2"
        assert {r["status"] for r in p["requirements"]} == {"owner_confirmed"}
        with restarted.app.state.store.connect() as c:
            assert c.execute("SELECT COUNT(*) FROM requirements WHERE proposal_id=?", (pid,)).fetchone()[0] == 2
            assert c.execute("SELECT COUNT(DISTINCT version) FROM requirement_scope WHERE proposal_id=?", (pid,)).fetchone()[0] == 4
