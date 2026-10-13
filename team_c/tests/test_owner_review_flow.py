"""The simplified owner review: access decided once in the policy, answers checked once, suggestions never decide.

Model calls are deterministic TEST SUBSTITUTES (helpers.lifecycle); counts of recorded attempts stand in
for model calls, because every substitute call records exactly one attempt.
"""
import json
import re
from helpers.lifecycle import ANSWER, approve, current, generate, post, supersede_q2
from team_c.compiler.policies import suggested_checks
from team_c.llm.prompts import DESIGN, SYSTEM, TASKS, system_prompt
from team_c.models import AnswerSuggestionOutput


def attempts(app):
    return app.state.store.all("SELECT COUNT(*) AS n FROM attempts")[0]["n"]


def policy_for(p):
    checks = [dict(requirement_id=r["id"], kind="ownership", resource="Item", step_id="s1", response_status="200", resource_field="/owner_id", identity_source="user_id")
              for r in p["requirements"] if r["kind"] == "record_scope"]
    return dict(principals=["resource_owner"], authentication="required", checks=checks,
                scopes=[dict(requirement_id=c["requirement_id"], mode="checked", reason="sandbox-only: owners only") for c in checks],
                financial=False, irreversible=False, external_side_effects=False, idempotent=True)


def answer_questions_only(client, pid, text=ANSWER):
    p = current(client, pid)
    r = post(client, pid, "answers", answers={q["id"]: text for q in p["content"]["questions"]})
    assert r.status_code == 200, r.text
    return r.json()


def checked_without_access_answers(lifecycle):
    """Questions answered and checked; access requirements left for the policy, with no prose answers."""
    app, client, _, spec, scope = lifecycle
    pid = generate(client, spec, scope)
    supersede_q2(client, pid)
    answer_questions_only(client, pid)
    post(client, pid, "reconcile")          # the substitute revises the content once (a new version) ...
    r = post(client, pid, "reconcile")      # ... which this check settles without further change
    assert r.status_code == 200, r.text
    return app, client, pid


def test_unanswered_access_requirements_are_not_sent_to_the_model(lifecycle):
    app, client, _, spec, scope = lifecycle
    pid = generate(client, spec, scope)
    supersede_q2(client, pid)
    answer_questions_only(client, pid)
    sent = []
    original = app.state.service.providers.call
    app.state.service.providers.call = lambda kind, payload, model, run: (sent.append(payload), original(kind, payload, model, run))[1]
    assert post(client, pid, "reconcile").status_code == 200
    assert sent[0]["requirements"] == [] and len(sent[0]["pending_access_decisions"]) == len(current(client, pid)["requirements"])
    # Only the answers under assessment travel, as id/question/text.
    assert set(sent[0]["proposal"]["answers"]) == {"q1"} and set(sent[0]["proposal"]["answers"]["q1"]) == {"id", "question_id", "text"}


def test_accepting_the_policy_settles_the_review_without_another_model_call(lifecycle):
    app, client, pid = checked_without_access_answers(lifecycle)
    p = current(client, pid)
    assert p["state"] == "needs_clarification"
    rec = next(r for r in p["reconciliations"] if r["id"] == p["reconciliation_id"])
    unanswered = {f["question_id"]: f for f in rec["result"]["findings"] if f["question_id"] != "q1"}
    assert unanswered and all(f["status"] == "insufficient" and "access policy" in f["explanation"] and not f["answer_revision_ids"] for f in unanswered.values())
    before = attempts(app)
    saved = post(client, pid, "policy", policy=policy_for(p))
    assert saved.status_code == 200, saved.text
    view = saved.json()
    assert view["state"] == "ready_for_review" and view["lifecycle"]["requirements_confirmed"]
    assert attempts(app) == before
    reused = next(r for r in view["reconciliations"] if r["id"] == view["reconciliation_id"])
    assert reused["result"]["reused_from"] == rec["id"] and reused["successful"]
    # The run is recorded for audit even though no model was called.
    diag = app.state.store.all("SELECT payload FROM diagnostics WHERE run_id=? AND stage='reused_assessment'", (reused["run_id"],))
    assert json.loads(diag[0]["payload"])["model_calls"] == 0
    assert approve(client, pid).status_code == 200


def test_a_changed_answer_is_checked_by_the_model_again(lifecycle):
    app, client, pid = checked_without_access_answers(lifecycle)
    p = current(client, pid)
    assert post(client, pid, "policy", policy=policy_for(p)).json()["state"] == "ready_for_review"
    # Policy decisions are not prose; only the question answer changes.
    assert post(client, pid, "answers", answers={"q1": ANSWER + " Also staff."}).status_code == 200
    before = attempts(app)
    assert post(client, pid, "reconcile").status_code == 200
    assert attempts(app) == before + 1


def test_an_earlier_revise_verdict_is_never_reused(lifecycle):
    app, client, pid = checked_without_access_answers(lifecycle)
    with app.state.store.connect(write=True) as c:
        for row in c.execute("SELECT id,result FROM reconciliations WHERE proposal_id=?", (pid,)).fetchall():
            result = json.loads(row["result"])
            result["self_review"]["verdict"] = "revise"
            c.execute("UPDATE reconciliations SET result=? WHERE id=?", (json.dumps(result), row["id"]))
    p = current(client, pid)
    before = attempts(app)
    view = post(client, pid, "policy", policy=policy_for(p)).json()
    assert view["state"] == "needs_reconciliation" and attempts(app) == before
    assert approve(client, pid).status_code == 422
    assert post(client, pid, "reconcile").status_code == 200 and attempts(app) == before + 1


def test_policy_form_suggests_an_ownership_check_from_the_response_schema(lifecycle):
    _, client, _, spec, scope = lifecycle
    pid = generate(client, spec, scope)
    p = current(client, pid)
    suggestions = p["policy_check_suggestions"]
    record = [r for r in p["requirements"] if r["kind"] == "record_scope"]
    assert record and {s["requirement_id"] for s in suggestions} == {r["id"] for r in record}
    check = suggestions[0]["check"]
    assert check["resource_field"] == "/owner_id" and check["step_id"] == "s1" and check["response_status"] == "200"
    assert "owner_id" in suggestions[0]["evidence"]
    # A suggestion is a form default only: nothing is accepted or confirmed until the owner submits.
    assert p["policy"] is None and not p["lifecycle"]["requirements_confirmed"]
    page = client.get(f"/proposals/{pid}").text
    assert "prefilled suggestions" in page and 'value="/owner_id"' in page


def test_no_ownership_suggestion_without_a_required_identity_field(lifecycle):
    _, client, _, spec, scope = lifecycle
    pid = generate(client, spec, scope)
    p = current(client, pid)
    inventory = json.loads(json.dumps(client.get(f'/api/v1/specifications/{spec["id"]}/inventory').json()["inventory"]))
    for o in inventory["operations"]:
        for r in o["responses"].values():
            if r.get("schema") and "owner_id" in (r["schema"].get("required") or []):
                r["schema"]["required"].remove("owner_id")
    assert suggested_checks(p["content"], inventory, p["requirements"]) == []


def test_access_questions_are_prefilled_from_the_accepted_policy(lifecycle):
    app, client, _, spec, scope = lifecycle
    pid = generate(client, spec, scope)
    supersede_q2(client, pid)
    p = current(client, pid)
    assert p["answer_suggestions"] == {}  # no policy yet: nothing to restate
    view = post(client, pid, "policy", policy=policy_for(p)).json()
    suggestion = view["answer_suggestions"]["q1"]  # "How will ownership be verified ...?"
    assert "accepted access policy" in suggestion["text"] and "/owner_id" in suggestion["text"]
    assert "q1" not in view["answers"]  # prefilled on the page, never saved for the owner
    page = client.get(f"/proposals/{pid}").text
    assert "Restated from the access policy you accepted" in page


def test_answer_assistant_is_isolated_cached_and_never_saves_answers(lifecycle):
    app, client, _, spec, scope = lifecycle
    pid = generate(client, spec, scope)
    supersede_q2(client, pid)
    service = app.state.service
    original, seen = service.providers.call, []
    def call(kind, payload, model, run):
        if kind != "answer_suggestion":
            return original(kind, payload, model, run)
        seen.append(payload)
        app.state.store.attempt(run, "TEST_SUBSTITUTE", "deterministic-sandbox-only", "succeeded")
        assert model is AnswerSuggestionOutput
        return AnswerSuggestionOutput(suggestions=[dict(question_id="q1", owner_decision=True, answer="guessed rule", options=["Owner only", "Any signed-in user"],
                                                        confidence="low", operation_ids=[], evidence="sandbox-only: ownership is a business decision")])
    service.providers.call = call
    p = current(client, pid)
    first = client.post(f'/api/v1/proposals/{pid}/versions/{p["version"]}/answer-suggestions')
    assert first.status_code == 200, first.text
    # An owner decision carries options only; a draft answer there would be a guessed business rule.
    assert first.json()["suggestions"][0]["answer"] == "" and first.json()["suggestions"][0]["options"]
    # Isolation: the assistant sees the proposal, questions and evidence, never answers or the reviewer's findings.
    payload = seen[0]
    assert set(payload) == {"business", "proposal", "questions", "access_policy", "inventory", "related_operations"}
    assert "questions" not in payload["proposal"] and [q["id"] for q in payload["questions"]] == ["q1"]
    before = attempts(app)
    again = client.post(f'/api/v1/proposals/{pid}/versions/{p["version"]}/answer-suggestions')
    assert again.json() == first.json() and attempts(app) == before  # same inputs: cached, no second call
    view = current(client, pid)
    assert view["answers"] == {} and view["ai_answer_suggestions"]["q1"]["owner_decision"]
    page = client.get(f"/proposals/{pid}").text
    assert "it is your decision" in page and "Owner only" in page and "guessed rule" not in page


def test_answer_assistant_output_must_cover_each_open_question_once(lifecycle):
    app, client, _, spec, scope = lifecycle
    pid = generate(client, spec, scope)
    supersede_q2(client, pid)
    original = app.state.service.providers.call
    def call(kind, payload, model, run):
        if kind != "answer_suggestion":
            return original(kind, payload, model, run)
        return AnswerSuggestionOutput(suggestions=[])
    app.state.service.providers.call = call
    p = current(client, pid)
    r = client.post(f'/api/v1/proposals/{pid}/versions/{p["version"]}/answer-suggestions')
    assert r.status_code == 502 and r.json()["code"] == "invalid_model_output"
    assert current(client, pid)["ai_answer_suggestions"] == {}


def test_web_save_and_check_is_one_step(lifecycle):
    app, client, _, spec, scope = lifecycle
    pid = generate(client, spec, scope)
    supersede_q2(client, pid)
    page = client.get(f"/proposals/{pid}").text
    csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
    assert 'action="/actions/answers_and_check"' in page and "Save and check answers" in page
    # Access requirements are decided in the policy form, not typed as prose.
    p = current(client, pid)
    assert not any(f'name="q:{r["id"]}"' in page for r in p["requirements"])
    before = attempts(app)
    r = client.post("/actions/answers_and_check", data={"csrf": csrf, "proposal_id": pid, "version": p["version"], "revision": p["review_revision"], "q:q1": ANSWER}, follow_redirects=False)
    assert r.status_code == 303, r.text
    # One model call; the check is recorded (the substitute's revision may have moved it to a new version).
    assert attempts(app) == before + 1 and app.state.store.all("SELECT id FROM reconciliations WHERE proposal_id=?", (pid,))


def test_checklist_lists_only_what_blocks_approval(lifecycle):
    app, client, pid = checked_without_access_answers(lifecycle)
    items = {i["key"]: i for i in current(client, pid)["checklist"]}
    assert items["questions"]["done"] and not items["access"]["done"]
    view = post(client, pid, "policy", policy=policy_for(current(client, pid))).json()
    assert all(i["done"] for i in view["checklist"])


def test_prompts_are_split_per_task():
    triage, suggestion = system_prompt("request_triage"), system_prompt("suggestion")
    assert DESIGN not in triage and DESIGN not in suggestion and TASKS["request_triage"] in triage
    assert SYSTEM == system_prompt("generation") and DESIGN in SYSTEM and "For reconciliation" not in SYSTEM
    assert system_prompt("repair: x") == system_prompt("revision: y") and "For revision:" in system_prompt("repair: x")
    assert all(t in system_prompt("unknown_task") for t in TASKS.values())
    assert len(triage) < len(SYSTEM) / 2


def test_proposals_overview_and_action_urls_opened_as_pages(lifecycle):
    _, client, _, spec, scope = lifecycle
    pid = generate(client, spec, scope)
    page = client.get("/proposals")
    assert page.status_code == 200 and f"/proposals/{pid}" in page.text and "Needs your review" in page.text
    assert client.get("/proposals?stage=build").status_code == 200
    r = client.get("/actions/build_artifact", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/proposals?stage=build"
    r = client.get(f"/actions/build_artifact?proposal_id={pid}", follow_redirects=False)
    assert r.headers["location"] == f"/proposals/{pid}#build"
    # Actions still change nothing on GET.
    assert client.get("/actions/decision", follow_redirects=False).headers["location"] == "/proposals"


def test_without_questions_the_policy_alone_settles_the_review(lifecycle):
    app, client, _, spec, scope = lifecycle
    app.state.service.providers.questions = False
    pid = generate(client, spec, scope)
    # The generation-time rating is only reused when it already passes the approval gate.
    with app.state.store.connect(write=True) as c:
        row = c.execute("SELECT content FROM versions WHERE proposal_id=?", (pid,)).fetchone()
        content = json.loads(row["content"])
        content["self_review"]["verdict"] = "proceed"
        for c_ in content["self_review"]["criteria"]:
            c_["score"] = 4
        c.execute("UPDATE versions SET content=? WHERE proposal_id=?", (json.dumps(content), pid))
    before = attempts(app)
    view = post(client, pid, "policy", policy=policy_for(current(client, pid))).json()
    rec = next(r for r in view["reconciliations"] if r["id"] == view["reconciliation_id"])
    assert view["state"] == "ready_for_review" and rec["result"]["reused_from"] == "generation" and attempts(app) == before
    assert "Nothing left to check" in client.get(f"/proposals/{pid}").text
    assert approve(client, pid).status_code == 200


def test_a_proceed_verdict_with_failing_scores_is_not_reused(lifecycle):
    app, client, _, spec, scope = lifecycle
    app.state.service.providers.questions = False
    pid = generate(client, spec, scope)
    with app.state.store.connect(write=True) as c:
        row = c.execute("SELECT content FROM versions WHERE proposal_id=?", (pid,)).fetchone()
        content = json.loads(row["content"])
        content["self_review"]["verdict"] = "proceed"  # scores stay 2/5, which the gate blocks
        c.execute("UPDATE versions SET content=? WHERE proposal_id=?", (json.dumps(content), pid))
    view = post(client, pid, "policy", policy=policy_for(current(client, pid))).json()
    assert view["state"] == "needs_reconciliation"


def test_without_questions_a_revise_rating_still_goes_to_the_model(lifecycle):
    app, client, _, spec, scope = lifecycle
    app.state.service.providers.questions = False
    pid = generate(client, spec, scope)  # generation rates "revise" until access is decided
    view = post(client, pid, "policy", policy=policy_for(current(client, pid))).json()
    assert view["state"] == "needs_reconciliation" and approve(client, pid).status_code == 422


def test_index_entries_show_every_response_field_of_a_wide_record():
    """At a 12-field cut-off an order lookup lost items/status/totals and triage called it unavailable."""
    from team_c.capabilities import entry
    fields = {f"field_{i:02d}": {"type": "string"} for i in range(22)} | {"status": {"type": "string"}, "items": {"type": "array"}}
    op = dict(id="o", method="GET", path="/orders/{id}", summary="Get order", inputs={"path.id": {}}, declared_auth={"status": "required"},
              proposal_eligible=True, supported=True, responses={"200": {"schema": {"type": "object", "properties": fields}}})
    assert {"items", "status"} <= set(entry(op)["returns"])


def test_triage_starts_from_its_own_smaller_slice(tmp_path):
    from team_c.config import Settings
    from team_c.services.runs import Runs
    settings = Settings(_env_file=None, database_path=str(tmp_path / "x.db"), llm_primary="ollama", ollama_context=200_000)
    budgets = []
    import team_c.services.runs as runs_module
    original = runs_module.capabilities.operation_index
    runs_module.capabilities.operation_index = lambda inv, b, *a: (budgets.append(b), original(inv, b, *a))[1]
    try:
        inv = dict(operations=[])
        Runs(settings, None, None).fitted_index("request_triage", lambda i, c: {}, inv)
        Runs(settings, None, None).fitted_index("suggestion", lambda i, c: {}, inv)
        settings.capability_index_chars = 500  # the general setting stays the ceiling
        Runs(settings, None, None).fitted_index("request_triage", lambda i, c: {}, inv)
    finally:
        runs_module.capabilities.operation_index = original
    assert budgets == [settings.triage_index_chars, 16000, 500]


def test_assistant_is_offered_even_when_a_question_is_prefilled_from_the_policy(lifecycle):
    """Seen live: a policy restatement hid the assistant button, and an admin-only policy restated nothing about ownership."""
    _, client, _, spec, scope = lifecycle
    pid = generate(client, spec, scope)
    supersede_q2(client, pid)
    p = current(client, pid)
    admin_only = dict(principals=["admin"], authentication="required",
                      scopes=[dict(requirement_id=r["id"], mode="unrestricted", reason="sandbox-only: staff tool") for r in p["requirements"] if r["kind"] == "record_scope"])
    view = post(client, pid, "policy", policy=admin_only).json()
    assert "administrators" in view["answer_suggestions"]["q1"]["text"]
    page = client.get(f"/proposals/{pid}").text
    assert "Draft answers with the AI assistant" in page and "Prefilled suggestion" in page


def test_policy_summary_states_when_no_ownership_check_applies():
    from team_c.services.proposals import policy_summary
    text = policy_summary(dict(principals=["admin"], checks=[], scopes=[]))
    assert "administrators" in text and "No per-record ownership check applies" in text
