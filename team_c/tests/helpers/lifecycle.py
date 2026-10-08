"""Review lifecycle on the target-shaped inventory: the deterministic TEST SUBSTITUTE and TEST-ONLY review helpers."""
import json
from conftest import refreshed_review, requirement_findings
from team_c.models import CRITERIA, GenerationOutput, ReconciliationOutput

ANSWER = "TEST-ONLY (not a business decision): only the authenticated owner of an item may read or update it."
OWNER_ID_QUESTION = "What is the exact business setting for the owner_id?"


def live_shaped(read, update, questions=True):
    runtime = lambda target, ref: dict(target=target, kind="runtime_argument", reference=ref, step_id=None, response_status=None)
    return dict(name="Item lookup and update", description="Look up an item and update its title/description", business_purpose="Let owners maintain items",
                steps=[dict(id="s1", operation_id=read["id"], purpose="Look up the item", bindings=[runtime("path.id", "item_id")]),
                       dict(id="s2", operation_id=update["id"], purpose="Update the item", bindings=[runtime("path.id", "item_id"), runtime("body.title", "title"), runtime("body.description", "description")])],
                configuration=[], outputs=[dict(name="item", step_id="s1", response_status="200", pointer="/description"), dict(name="updated_item", step_id="s2", response_status="200", pointer="/title")],
                questions=[dict(id="q1", text="How will ownership be verified before accessing private records?", configuration_key=None), dict(id="q2", text=OWNER_ID_QUESTION, configuration_key=None)] if questions else [],
                expected_reads=["Item"], expected_writes=["Item title/description"], assumptions=[], limitations=["Runtime authorization unverified"], risk="medium", risk_rationale="Updates records",
                # Generated before the owner has answered: caller access and record scope are unresolved,
                # so the rating is honestly "revise" until reconciliation re-rates it.
                self_review=dict(criteria=[dict(criterion=c, score=2, reason="Rated before the owner answered the access questions") for c in CRITERIA],
                                 verdict="revise", summary="Who may use this tool and which records they may reach is unresolved."))


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
        resolved = all(f["status"] == "resolved" for f in findings)
        revised = json.loads(json.dumps(content))
        if self.mode == "invent_operation":
            revised["steps"][0]["operation_id"] = "made-up-operation"
        elif self.mode == "invent_field":
            revised["steps"][1]["bindings"].append(dict(target="body.owner_id", kind="runtime_argument", reference="owner_id", step_id=None, response_status=None))
        elif resolved and not any(x.startswith("Owner answered") for x in content["assumptions"]):
            revised["assumptions"].append("Owner answered: only an item's owner may read or update it (TEST-ONLY)")
        else:
            revised = None
        return ReconciliationOutput(findings=findings, revised_proposal=revised, capability_gaps=[],
                                    self_review=refreshed_review("proceed" if resolved else "revise",
                                                                 "Every access and design question is answered." if resolved
                                                                 else "The owner's answers do not yet establish who may use this tool."))


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
