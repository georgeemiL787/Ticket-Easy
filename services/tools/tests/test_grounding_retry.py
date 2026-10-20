"""Proposal writing: per-operation input names in the decoding format, one retry with the check's errors, visible reasons.
MOCKED / AUTHORED: model responses are transport substitutes or the authored RequestDesk; per-test databases only."""
import json
import httpx
import pytest
from team_c.config import AppError, Settings
from team_c.grounding import drop_stale_configuration, validate_proposal
from team_c.models import GenerationOutput, ProposalContent
from team_c.providers import Providers
from team_c.service import Service
from team_c.storage import Store, now, uid
from conftest import answer_reconcile, setup_proposal
from helpers.openapi import inventory, op, proposal
from helpers.tool_requests import ask, lookup_only, triage


def test_step_bindings_are_limited_to_their_own_operation_inputs(tmp_path):
    inv = inventory()
    items, item, update = op(inv, "GET", "/api/v1/items/"), op(inv, "GET", "/api/v1/items/{id}"), op(inv, "PUT", "/api/v1/items/{id}")
    settings = Settings(_env_file=None, database_path=str(tmp_path / "m.db"), ollama_context=200_000)
    store = Store(settings.database_path)
    bid = uid()
    with store.connect(write=True) as c:
        c.execute("INSERT INTO businesses VALUES(?,?,?,?)", (bid, "Items", "Items", now()))
    sent = []
    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(500)
    providers = Providers(settings, store, httpx.MockTransport(handler))
    payload = dict(business=dict(name="Items", description="Items"), inventory=Service.scoped(inv, [items["id"], item["id"], update["id"]]))
    try:
        providers.call("generation", payload, GenerationOutput, store.start_run(bid, "generation", {}))
    except Exception:
        pass
    defs = sent[0]["format"]["$defs"]
    variants = {v["properties"]["operation_id"]["enum"][0]: v for v in defs["Step"]["anyOf"]}
    assert set(variants) == {items["id"], item["id"], update["id"]}
    for o in (items, item, update):
        bindings = variants[o["id"]]["properties"]["bindings"]
        assert bindings["maxItems"] == len(o["inputs"]) and bindings["minItems"] == sum(f["required"] for f in o["inputs"].values())
        # Decoding order: the operation is committed before any binding target.
        assert list(variants[o["id"]]["properties"]) == ["id", "operation_id", "purpose", "bindings"]
        *kinds, previous = bindings["items"]["anyOf"]
        assert previous == {"$ref": "#/$defs/PreviousOutputBinding"}
        for k in kinds:
            assert defs[k["properties"]["target"]["$ref"].rsplit("/", 1)[1]]["enum"] == sorted(o["inputs"])
    # The observed live failure: an "inputs." prefix is not an input name of any step.
    every = sorted({key for o in (items, item, update) for key in o["inputs"]})
    assert "inputs.path.id" not in every and all(v["properties"]["target"]["enum"] == every for v in defs["PreviousOutputBinding"]["anyOf"])


def test_unknown_configuration_error_names_both_fixes():
    """The observed live retry: a per-use input declared as an unknown setting without a linked question."""
    inv = inventory()
    item = op(inv, "GET", "/api/v1/items/{id}")
    p = proposal(item, dict(name="title", step_id="s1", response_status="200", pointer="/title"))
    p["configuration"] = [dict(key="item_id", value_json=None)]
    p["steps"][0]["bindings"][0]["kind"] = "business_configuration"
    with pytest.raises(AppError) as caught:
        validate_proposal(ProposalContent(**p), inv)
    [error] = caught.value.details["errors"]
    assert "configuration_key item_id" in error and "bind path.id as runtime_argument and remove configuration item_id" in error


def test_unencoded_configuration_value_is_explained():
    """The observed live reconciliation: an owner answer copied into value_json as bare text."""
    inv = inventory()
    item = op(inv, "GET", "/api/v1/items/{id}")
    p = proposal(item, dict(name="title", step_id="s1", response_status="200", pointer="/title"))
    p["configuration"] = [dict(key="item_id", value_json="all items")]
    p["steps"][0]["bindings"][0]["kind"] = "business_configuration"
    with pytest.raises(AppError) as caught:
        validate_proposal(ProposalContent(**p), inv)
    [error] = caught.value.details["errors"]
    assert "configuration item_id is not valid JSON" in error and "text needs quotes" in error


def rebound(content):
    """AUTHORED: the observed live reconciliation, an owner setting rebound as a runtime argument with its declaration left behind."""
    data = content.model_dump()
    for b in data["steps"][1]["bindings"]:
        if b["kind"] == "business_configuration":
            b["kind"] = "runtime_argument"
    return ProposalContent.model_validate(data)


def unencoded(content):
    content.configuration[0].value_json = "support"
    return content


def scripted_reconciliation(env, edits):
    """Wrap the deterministic substitute: each reconciliation's revised proposal is the current content after the next edit."""
    app, client, _ = env
    original, payloads = app.state.service.providers.call, []
    def call(kind, payload, model, run):
        result = original(kind, payload, model, run)
        if kind == "reconciliation":
            payloads.append(payload)
            result.revised_proposal = edits.pop(0)(ProposalContent.model_validate(payload["proposal"]["content"]))
        return result
    app.state.service.providers.call = call
    return payloads


def misattributed_reconciliation(env, always=False):
    """Wrap the deterministic substitute: each requirement finding quotes the question's answer instead of its own."""
    app, client, _ = env
    original, payloads = app.state.service.providers.call, []
    def call(kind, payload, model, run):
        result = original(kind, payload, model, run)
        if kind == "reconciliation":
            payloads.append(payload)
            if always or "evidence_feedback" not in payload:
                quoted = payload["proposal"]["answers"]["q1"]["id"]
                result.findings = [f.model_copy(update=dict(answer_revision_ids=[quoted])) if f.question_id != "q1" and f.answer_revision_ids else f for f in result.findings]
        return result
    app.state.service.providers.call = call
    return payloads


def test_drop_stale_configuration_only_removes_rebound_settings(env):
    pid = setup_proposal(env)[2]["proposal_ids"][0]
    content = ProposalContent.model_validate(env[1].get(f"/api/v1/proposals/{pid}").json()["content"])
    assert drop_stale_configuration(content) == (content, [])
    cleaned, removed = drop_stale_configuration(rebound(content))
    assert removed == [content.configuration[0].key] and cleaned.configuration == []
    assert [q.id for q in cleaned.questions] == ["q1"] and cleaned.questions[0].configuration_key is None
    # A declaration nothing uses that is not a runtime argument either stays, so the check still reports it.
    unused = content.model_copy(deep=True)
    unused.steps[1].bindings = [b for b in unused.steps[1].bindings if b.kind != "business_configuration"]
    assert drop_stale_configuration(unused) == (unused, [])


def test_reconciliation_cleans_up_a_rebound_setting(env):
    app, client, _ = env
    pid = setup_proposal(env)[2]["proposal_ids"][0]
    payloads = scripted_reconciliation(env, [rebound])
    r = answer_reconcile(client, pid, "Use support.")
    assert r.status_code == 200 and r.json()["version"] == 2, r.text
    assert len(payloads) == 1
    content = client.get(f"/api/v1/proposals/{pid}").json()["content"]
    assert content["configuration"] == [] and content["questions"][0]["configuration_key"] is None
    stages = [d["stage"] for d in client.get(f'/api/v1/proposal-runs/{r.json()["run_id"]}').json()["diagnostics"]]
    assert "stale_configuration_removed" in stages


def test_rejected_reconciliation_is_retried_once_with_the_check_errors(env):
    app, client, _ = env
    pid = setup_proposal(env)[2]["proposal_ids"][0]
    payloads = scripted_reconciliation(env, [unencoded, rebound])
    r = answer_reconcile(client, pid, "Use support.")
    assert r.status_code == 200 and r.json()["version"] == 2, r.text
    assert len(payloads) == 2 and "grounding_feedback" not in payloads[0]
    feedback = payloads[1]["grounding_feedback"]
    assert any("not valid JSON" in e for e in feedback["errors"]) and feedback["previous_proposal"]["configuration"][0]["value_json"] == "support"


def test_second_rejected_reconciliation_fails_with_both_runs(env):
    app, client, _ = env
    pid = setup_proposal(env)[2]["proposal_ids"][0]
    payloads = scripted_reconciliation(env, [unencoded, unencoded])
    r = answer_reconcile(client, pid, "Use support.")
    assert r.status_code == 422 and r.json()["code"] == "invalid_bindings" and len(payloads) == 2
    details = r.json()["details"]
    assert len(details["earlier_run_ids"]) == 1 and details["earlier_run_ids"][0] != details["run_id"]
    assert client.get(f"/api/v1/proposals/{pid}").json()["version"] == 1


def test_a_finding_citing_another_items_answer_is_retried_once_with_its_own(env):
    app, client, _ = env
    pid = setup_proposal(env)[2]["proposal_ids"][0]
    payloads = misattributed_reconciliation(env)
    r = answer_reconcile(client, pid, "Use support.")
    assert r.status_code == 200, r.text
    assert len(payloads) == 2 and "evidence_feedback" not in payloads[0]
    answers = payloads[1]["proposal"]["answers"]
    feedback = payloads[1]["evidence_feedback"]
    requirements = {req["id"] for req in client.get(f"/api/v1/proposals/{pid}").json()["requirements"]}
    assert {f["question_id"] for f in feedback} == requirements and "q1" not in {f["question_id"] for f in feedback}
    assert all(f["answer_revision_id"] == answers[f["question_id"]]["id"] for f in feedback)


def test_a_second_finding_that_still_cites_the_wrong_answer_fails(env):
    app, client, _ = env
    pid = setup_proposal(env)[2]["proposal_ids"][0]
    payloads = misattributed_reconciliation(env, always=True)
    r = answer_reconcile(client, pid, "Use support.")
    assert r.status_code == 422 and r.json()["code"] == "invalid_reconciliation", r.text
    assert len(payloads) == 2 and "evidence_feedback" in payloads[1]
    details = r.json()["details"]
    assert len(details["earlier_run_ids"]) == 1 and details["earlier_run_ids"][0] != details["run_id"]
    assert client.get(f"/api/v1/proposals/{pid}").json()["version"] == 1


def bad_target(inv, n):
    """AUTHORED: the observed live mistake, an "inputs." prefix on a real input name."""
    out = lookup_only(inv, n)
    binding = out["proposals"][0]["steps"][0]["bindings"][0]
    binding["target"] = "inputs." + binding["target"]
    return out


def test_rejected_proposal_is_retried_once_with_the_check_errors(desk):
    d = desk
    d.provider.triage.append(triage("feasible", [d.lookup]))
    d.provider.generations += [bad_target, lookup_only]
    req = ask(d, "retry-key-1").json()
    assert req["status"] == "proposed" and len(req["proposal_ids"]) == 1
    kinds = [k for k, _ in d.provider.payloads]
    assert kinds == ["request_triage", "generation", "generation"]
    first, second = d.provider.payloads[1][1], d.provider.payloads[2][1]
    assert "grounding_feedback" not in first
    feedback = second["grounding_feedback"]
    assert any("Unknown target inputs." in e for e in feedback["errors"])
    assert feedback["previous_proposal"]["steps"][0]["bindings"][0]["target"].startswith("inputs.")
    # Both proposal-writing runs stay visible on the request; the rejected one keeps its reasons.
    assert len(req["run_ids"]) == 3
    rejected = d.service.store.one("SELECT error FROM runs WHERE id=?", (req["run_ids"][1],))
    assert any("Unknown target inputs." in e for e in json.loads(rejected["error"])["details"]["errors"])


def test_second_rejection_fails_and_shows_what_the_check_found(desk):
    d = desk
    d.provider.triage.append(triage("feasible", [d.lookup]))
    d.provider.generations += [bad_target, bad_target]
    r = ask(d, "retry-key-2")
    assert r.status_code == 200, r.text
    req = r.json()
    assert req["status"] == "failed" and req["error"]["code"] == "invalid_bindings" and len(req["run_ids"]) == 3
    assert [k for k, _ in d.provider.payloads] == ["request_triage", "generation", "generation"]
    assert any("Unknown target inputs." in e for e in req["error"]["errors"]) and "proposal" not in req["error"]
    page = d.client.get(f"/businesses/{d.bid}").text
    assert "What the check found" in page and "Unknown target inputs." in page
