"""Production: a service credential scoped to the verified customer by the tools service, promotion with its checklist, rollback, auto-disable.

MOCKED / AUTHORED: the proposal comes from the AUTHORED TEST SUBSTITUTE, owner decisions are sandbox-only, the target is the
in-process service-desk fixture, which here also accepts a business SERVICE token that may act on any booking (so the API itself
no longer separates customers: only the tools service's check does). The real stdio server and the Nile deployment: ops/local/production_check.py.
"""
import json
import re
from pathlib import Path
import pytest
from tools_service import promotions as rules
from tools_service.config import AppError
from tools_service.executor import Run
from tools_service.mcp_server import client_context
from helpers.desk import KEY, generated, make_desk, review_and_build_policy
from helpers.publication import RevisingDesk, publish

SERVICE_TOKEN = "service-token-DO-NOT-LEAK-0123456789"
ENV = "DESK_SERVICE_TOKEN"
FIXTURE_ENV = "DESK_FIXTURE_KEY"


@pytest.fixture
def prod(tmp_path, monkeypatch):
    monkeypatch.setattr("helpers.desk.DeskSubstitute", RevisingDesk)
    monkeypatch.setenv(ENV, SERVICE_TOKEN)
    monkeypatch.setenv(FIXTURE_ENV, KEY)
    d = make_desk(tmp_path, "base")
    d.service = d.app.state.service
    d.backend.app.state.service_token = SERVICE_TOKEN
    s = d.service.settings
    s.production_enabled, s.production_hosts, s.production_private_hosts = True, "desk.test:80", "desk.test:80"
    s.production_audit_dir = str(tmp_path / "audit")
    holder = lambda who: d.people[who]["number"] if d.n["holder_int"] else d.people[who]["id"]
    s.production_smoke_context = json.dumps({d.ctx: holder("alice")})
    s.production_smoke_arguments = json.dumps(dict(booking_reference=d.booking["alice"]["ref"], category=d.n["categories"][0], details="smoke"))
    pid = generated(d)
    d.sandbox_connector = d.service.connectors.save(pid, "http://desk.test", [d.ctx], confirmed=True, fixtures=dict(base_url="http://desk.test", key_ref=FIXTURE_ENV))
    d.source = review_and_build_policy(d, pid, d.sandbox_connector)
    d.business = d.source["content"]["proposal"]["business_id"]
    assert d.service.auto_test(d.source["id"])["outcome"] == "ready_to_publish"
    assert publish(d, d.source["id"]).status_code == 200
    d.prod_connector = d.service.connectors.save(pid, "http://desk.test", [d.ctx], confirmed=True, environment="production", secret_ref="env:" + ENV)
    d.ctx_of = lambda who: {d.ctx: holder(who)}
    d.promote = lambda **kw: d.service.promote(d.source["id"], None, **dict(dict(actor="Mona Admin", note="sandbox-only promotion", rollback_contact="oncall@example.com", accept_no_idempotency=True), **kw))
    d.call = lambda promo, who, owner=None, **extra: d.service.invoke_published(
        d.business, "service", d.service.publication(promo["publication_id"])["tool_name"], extra.pop("arguments", None) or dict(
            booking_reference=d.booking[owner or who]["ref"], category=d.n["categories"][0], details="Leaking tap"),
        extra.pop("context", d.ctx_of(who)), extra.pop("key", None), production=True)
    yield d
    d.client.__exit__(None, None, None)


def test_a_production_connector_names_its_secret_and_must_be_allowed_by_the_operator(prod):
    d = prod
    pid = d.source["content"]["proposal"]["id"]
    save = lambda **kw: d.service.connectors.save(pid, "http://desk.test", [d.ctx], **dict(dict(confirmed=True, environment="production", secret_ref="env:" + ENV), **kw))
    for kw, code in ((dict(secret_ref=SERVICE_TOKEN), "production_secret_ref"), (dict(confirmed=False), "production_confirmation")):
        with pytest.raises(AppError) as exc:
            save(**kw)
        assert exc.value.code == code
    d.service.settings.production_hosts = "elsewhere.test:80"
    with pytest.raises(AppError) as exc:
        save()
    assert exc.value.code == "destination_not_allowed"
    d.service.settings.production_enabled = False
    with pytest.raises(AppError) as exc:
        save()
    assert exc.value.code == "production_disabled"
    listing = next(c for c in d.service.connectors_of(d.business) if c["id"] == d.prod_connector)
    assert listing["environment"] == "production" and listing["secret_ref"] == "env:" + ENV and listing["secret_set"] is True
    assert SERVICE_TOKEN not in json.dumps(d.service.connectors_of(d.business))
    with pytest.raises(AppError) as exc:  # sandbox connectors take no service credential
        d.service.connectors.save(pid, "http://desk.test", [d.ctx], confirmed=True, secret_ref="env:" + ENV)
    assert exc.value.code == "secret_ref_production_only"


def test_promotion_runs_a_checklist_and_production_is_true_only_after_it(prod):
    d = prod
    assert all(p["production_ready"] is False for p in d.service.publications(d.business))
    refused = None
    with pytest.raises(AppError) as exc:
        d.promote(accept_no_idempotency=False)  # the API declares no idempotency header for the write
    assert exc.value.code == "promotion_refused"
    refused = exc.value.details["checklist"]
    assert refused["idempotency"]["ok"] is False and "explicitly accept" in refused["idempotency"]["detail"]
    assert refused["smoke"]["ok"] is False and "not run" in refused["smoke"]["detail"]  # production is not touched by a refused promotion
    assert [p for p in d.service.promotions(d.business)][0]["status"] == "refused"
    assert not [p for p in d.service.publications(d.business) if p["environment"] == "production"]
    promo = d.promote()
    assert promo["status"] == "active" and promo["actor"] == "Mona Admin" and promo["rollback_contact"] == "oncall@example.com"
    assert set(promo["checklist"]) == {"source_published", "approval_current", "enforcement_reviewed", "sandbox_evidence", "service_scoped", "content_identical",
                                       "secret_resolved", "idempotency", "rate_limits", "rollback_contact", "smoke"}
    assert all(v["ok"] for v in promo["checklist"].values()), promo["checklist"]
    assert promo["checklist"]["content_identical"]["detail"].startswith("identical")
    assert promo["idempotency"]["accepted_without"]  # recorded: the admin accepted no idempotency for the write
    pub = d.service.publication(promo["publication_id"])
    assert pub["environment"] == "production" and pub["production_ready"] is True and pub["production_activation"] is True
    assert pub["production_assessment"] == "passed" and pub["execution_enabled"] and pub["promotion_id"] == promo["id"]
    # the sandbox publication of the source stays a sandbox one, production false
    sandbox = next(p for p in d.service.publications(d.business) if p["environment"] == "sandbox")
    assert sandbox["production_ready"] is False
    art = d.service.artifact(promo["production_artifact_id"])["content"]
    assert art["connector"]["environment"] == "production" and art["activation"]["activated"] is True and art["execution_blockers"] == []
    assert d.client.get(f"/api/v1/artifacts/{d.source['id']}").json()["content"]["activation"]["activated"] is False


def test_promotion_refuses_missing_evidence_a_missing_secret_a_failed_smoke_check_and_production_off(prod, monkeypatch):
    d = prod
    monkeypatch.delenv(ENV)
    with pytest.raises(AppError) as exc:
        d.promote()
    assert exc.value.details["checklist"]["secret_resolved"]["ok"] is False and "NOT available" in exc.value.details["checklist"]["secret_resolved"]["detail"]
    monkeypatch.setenv(ENV, SERVICE_TOKEN)
    d.service.settings.production_smoke_arguments = json.dumps(dict(booking_reference=d.booking["bob"]["ref"], category=d.n["categories"][0], details="x"))
    with pytest.raises(AppError) as exc:  # the smoke customer (alice) does not own bob's booking: the tools service's check refuses it
        d.promote()
    smoke = exc.value.details["checklist"]["smoke"]
    assert smoke["ok"] is False and "failed" in smoke["detail"]
    d.service.settings.production_smoke_context = ""
    with pytest.raises(AppError) as exc:
        d.promote()
    assert "not configured" in exc.value.details["checklist"]["smoke"]["detail"]
    # an artifact whose sandbox tests were withdrawn cannot be promoted
    d.service.settings.production_smoke_context = json.dumps(d.ctx_of("alice"))
    d.service.settings.production_smoke_arguments = json.dumps(dict(booking_reference=d.booking["alice"]["ref"], category=d.n["categories"][0], details="x"))
    d.client.post(f"/api/v1/artifacts/{d.source['id']}/sandbox-tests", json=dict(name="auto.own_record.a", identity=d.service.artifact(d.source["id"])["tests"][0]["scenario"] and "x", arguments={}, expect=dict(status="rejected")))
    with pytest.raises(AppError) as exc:
        d.promote(actor="x", note="")
    assert exc.value.code == "promotion_attribution"
    d.service.settings.production_enabled = False
    with pytest.raises(AppError) as exc:
        d.promote()
    assert exc.value.code == "production_disabled"


def test_a_service_credential_cannot_reach_another_customers_record(prod):
    d = prod
    promo = d.promote()
    before = len(d.state()["requests"])  # the automatic sandbox tests filed some already
    ok = d.call(promo, "alice")
    assert ok["status"] == "succeeded" and ok["environment"] == "production" and ok["production_ready"] is True
    filed = d.state()["requests"]
    assert len(filed) == before + 1 and filed[-1]["filed_by"] == d.people["alice"]["id"]
    # bob's context against alice's booking: the API alone would allow it (service token), the tools service's check refuses before the write
    denied = d.call(promo, "bob", owner="alice")
    assert denied["status"] == "failed" and denied["error"]["code"] == "blocked_by_access_check" and denied["error"]["category"] == "permission_denied"
    assert len(d.state()["requests"]) == before + 1
    execution = [e for e in d.service.artifact(promo["production_artifact_id"])["executions"] if e["mode"] == "mcp_production"]
    assert [e["identity"] for e in execution] == ["service", "service"] and all("context_sha256" in e["report"] for e in execution)


def test_context_is_required_declared_and_never_an_argument(prod):
    d = prod
    promo = d.promote()
    for context, code in ((None, "context_missing"), ({}, "context_missing"), ({**d.ctx_of("alice"), "role": "admin"}, "context_undeclared"), ({d.ctx: ""}, "context_invalid")):
        result = d.call(promo, "alice", context=context)
        assert result["status"] == "rejected" and result["error"]["code"] == code and "No request was sent" in result["message"] or result["error"]["code"] == code
    arguments = dict(booking_reference=d.booking["alice"]["ref"], category=d.n["categories"][0], details="x", account_id="someone-else")
    before = len(d.state()["requests"])
    assert d.call(promo, "alice", arguments=arguments)["error"]["code"] == "invalid_arguments"
    assert len(d.state()["requests"]) == before
    # the production tool is invisible to, and not callable by, an ordinary (identity) server
    tool = d.service.publication(promo["publication_id"])["tool_name"]
    assert tool not in [t["name"] for t in d.service.published_tools(d.business, "alice")]
    assert tool in [t["name"] for t in d.service.published_tools(d.business, "service", production=True)]
    with pytest.raises(AppError) as exc:
        d.service.invoke_published(d.business, "alice", tool, dict(booking_reference=d.booking["alice"]["ref"], category=d.n["categories"][0], details="x"), d.ctx_of("alice"))
    assert exc.value.code == "tool_not_published"
    # and a production server runs no sandbox tool
    sandbox_tool = next(p["tool_name"] for p in d.service.publications(d.business) if p["environment"] == "sandbox")
    with pytest.raises(AppError):
        d.service.invoke_published(d.business, "service", sandbox_tool, {}, d.ctx_of("alice"), None, production=True)


def test_the_server_reads_client_context_only_in_production_mode():
    class P:
        meta = {"ticketeasy": {"context": {"customer_id": "C-1"}, "idempotency_key": "abcdef0123456789"}}
    assert client_context(P) == ({"customer_id": "C-1"}, "abcdef0123456789")
    P.meta = {"ticketeasy": {"context": "C-1", "idempotency_key": "short"}}
    assert client_context(P) == (None, None)
    P.meta = None
    assert client_context(P) == (None, None)


def test_an_api_a_production_artifact_may_not_run_outside_its_publication(prod):
    d = prod
    promo = d.promote()
    aid = promo["production_artifact_id"]
    r = d.client.post(f"/api/v1/artifacts/{aid}/sandbox-runs", json=dict(identity="service", arguments=dict(booking_reference="x", category=d.n["categories"][0], details="x")))
    assert r.status_code == 403 and r.json()["code"] == "production_requires_publication"
    again = d.client.post(f"/api/v1/artifacts/{d.source['id']}/publications", json=dict(note="x"))
    assert again.status_code == 200  # the source's own sandbox publication is idempotent
    r = d.client.post(f"/api/v1/artifacts/{aid}/publications", json=dict(note="x", environment="production"))
    assert r.status_code == 422 or r.json().get("code") in ("use_promotion", "invalid_input")


def test_rollback_blocks_the_next_call_and_leaves_the_sandbox_publication(prod):
    d = prod
    promo = d.promote()
    assert d.call(promo, "alice")["status"] == "succeeded"
    rolled = d.service.rollback_promotion(promo["id"], "Mona Admin", "sandbox-only rollback")
    assert rolled["status"] == "rolled_back" and rolled["rolled_back_by"] == "Mona Admin"
    pub = d.service.publication(promo["publication_id"])
    assert pub["status"] == "disabled" and pub["production_ready"] is False and pub["production_assessment"] == "rolled_back"
    with pytest.raises(AppError) as exc:
        d.call(promo, "alice")
    assert exc.value.code == "tool_not_published"
    assert next(p for p in d.service.publications(d.business) if p["environment"] == "sandbox")["status"] == "published"
    with pytest.raises(AppError) as exc:
        d.service.rollback_promotion(promo["id"], "Mona Admin", "again")
    assert exc.value.code == "promotion_not_active"


def test_an_error_spike_switches_the_tool_off_but_refused_customers_never_do(prod, monkeypatch):
    d = prod
    d.service.settings.production_auto_disable_failures = 2
    promo = d.promote()
    for _ in range(4):  # a customer after someone else's record is refused by the tools service's check: not an API problem
        assert d.call(promo, "bob", owner="alice")["status"] == "failed"
    assert d.service.promotion(promo["id"])["status"] == "active"
    monkeypatch.setenv(ENV, "a-revoked-service-token")  # the business rotated the credential: the API now answers 401
    assert d.call(promo, "alice")["error"]["category"] == "authentication_failed"
    assert d.service.promotion(promo["id"])["status"] == "active"
    d.call(promo, "alice")
    auto = d.service.promotion(promo["id"])
    assert auto["status"] == "rolled_back" and auto["rolled_back_by"] == "auto-disable" and "switched off automatically" in auto["rollback_note"]
    assert d.service.publication(promo["publication_id"])["status"] == "disabled"


def test_per_customer_and_per_connector_budgets(prod):
    d = prod
    promo = d.promote()
    d.service.settings.production_calls_per_customer_per_minute = 3  # the promotion's smoke check already counted one call for alice
    assert d.call(promo, "alice")["status"] == "succeeded" and d.call(promo, "alice")["status"] == "succeeded"
    limited = d.call(promo, "alice")
    assert limited["status"] == "rejected" and limited["error"]["category"] == "rate_limited"
    assert d.call(promo, "bob")["status"] == "succeeded"  # another customer is counted separately
    d.service.settings.production_calls_per_minute = 3
    assert d.call(promo, "bob")["error"]["category"] == "rate_limited"


def test_the_secret_and_the_context_never_appear_anywhere_but_hashes_are_audited(prod, tmp_path):
    d = prod
    promo = d.promote()
    d.call(promo, "alice")
    d.call(promo, "bob", owner="alice")
    tables = []
    for table in ("executions", "publications", "promotions", "sandbox_connectors", "runs", "artifacts", "sandbox_tests"):
        tables += [dict(r) for r in d.service.store.all(f"SELECT * FROM {table}")]
    dump = json.dumps(tables, default=str)
    dump += json.dumps(d.service.connectors_of(d.business)) + json.dumps(d.service.promotions(d.business)) + d.client.get(f"/artifacts/{d.source['id']}").text
    dump += d.client.get(f"/artifacts/{d.service.promotion(promo['id'])['production_artifact_id']}").text
    audit = Path(d.service.settings.production_audit_dir)
    files = list(audit.glob("production-*.jsonl"))
    assert files
    audit_text = "".join(f.read_text(encoding="utf-8") for f in files)
    assert SERVICE_TOKEN not in dump and SERVICE_TOKEN not in audit_text
    alice = str(d.ctx_of("alice")[d.ctx])
    assert alice not in audit_text and d.booking["alice"]["ref"] not in audit_text  # ids and hashes only
    lines = [json.loads(x) for x in audit_text.splitlines()]
    assert len(lines) >= 3 and all(l["context_sha256"] and l["execution_id"] and l["status"] for l in lines if l["mode"] == "mcp_production")


def test_admin_token_gates_production_actions_through_the_api(prod):
    d = prod
    url = f"/api/v1/artifacts/{d.source['id']}/promotions"
    body = dict(actor="Mona Admin", note="sandbox-only", rollback_contact="oncall@example.com", accept_no_idempotency=True)
    assert d.client.post(url, json=body).json()["code"] == "admin_token_required"  # no token configured: production actions are off
    d.service.settings.admin_token = "operator-token"
    assert d.client.post(url, json=body).json()["code"] == "admin_token_invalid"
    assert d.client.post(url, json=body, headers={"x-tools-admin-token": "wrong"}).status_code == 403
    done = d.client.post(url, json=body, headers={"x-tools-admin-token": "operator-token"})
    assert done.status_code == 200 and done.json()["status"] == "active"
    # once a token is set it guards every change through the REST API, reads stay open
    assert d.client.get(f"/api/v1/businesses/{d.business}/promotions").status_code == 200
    assert d.client.post(f"/api/v1/artifacts/{d.source['id']}/auto-test", json={}).status_code == 403
    pid = d.source["content"]["proposal"]["id"]
    assert d.client.post(f"/api/v1/promotions/{done.json()['id']}/rollback", json=dict(actor="x", note="y")).status_code == 403
    page = d.client.post("/actions/save_connector", data=dict(proposal_id=pid, environment="production", base_url="http://desk.test"))
    assert page.status_code in (403, 422)


def test_idempotency_header_is_declared_by_the_api_and_sent_as_a_trusted_header_never_an_argument():
    content = dict(steps=[dict(id="s1", effect="read", operation_id="o1"), dict(id="s2", effect="write", operation_id="o2"), dict(id="s3", effect="write", operation_id="o3")])
    ops = [dict(id="o1", parameters=[]), dict(id="o2", parameters=[dict(**{"in": "header", "name": "Idempotency-Key"})]), dict(id="o3", parameters=[dict(**{"in": "query", "name": "idempotency-key"})])]
    assert rules.idempotency_plan(content, ops) == {"s2": "Idempotency-Key", "s3": None}
    from tools_service.artifacts import LIMITS
    artifact = dict(limits=LIMITS, proposal=dict(business_id="b"))
    run = Run(artifact, {}, dict(context={}), "http://api.test", None, dict(key="0123456789abcdef", steps={"s2": "Idempotency-Key"}))
    assert run.transport_headers(dict(id="s2", effect="write", parameters=[]), {"Authorization": "Bearer x"})["Idempotency-Key"] == "0123456789abcdef"
    assert "Idempotency-Key" not in run.transport_headers(dict(id="s2", effect="read", parameters=[]), {})
    assert "Idempotency-Key" not in run.transport_headers(dict(id="s1", effect="write", parameters=[]), {})


def test_content_that_differs_beyond_the_destination_is_refused():
    base = dict(name="t", steps=[1], connector=dict(id="a", base_url="http://x", auth=None), activation=dict(activated=False), execution_blockers=[])
    twin = dict(base, connector=dict(id="b", base_url="http://y", auth=None, environment="production"), activation=dict(activated=True))
    assert rules.differences(base, twin) == []
    assert rules.differences(base, dict(twin, steps=[1, 2])) == ["steps"]
    assert rules.differences(base, dict(twin, connector=dict(id="b", base_url="http://y", auth={"type": "http_bearer"}))) == ["connector.auth"]
    assert rules.differences(base, dict(twin, execution_blockers=["x"])) == ["execution_blockers"]


def test_a_secret_can_also_be_a_file_that_only_the_tools_service_reads(prod, tmp_path, monkeypatch):
    from tools_service.connectors import resolve_secret, secret_name
    path = tmp_path / "svc.key"
    path.write_text(SERVICE_TOKEN + chr(10), encoding="utf-8")
    assert resolve_secret("file:" + str(path)) == SERVICE_TOKEN and resolve_secret("file:" + str(tmp_path / "missing")) is None
    assert secret_name("file:../etc/passwd") is None and secret_name("env:1BAD") is None and secret_name(SERVICE_TOKEN) is None and secret_name("env:OK_NAME") == "env:OK_NAME"
    monkeypatch.delenv(ENV)
    pid = prod.source["content"]["proposal"]["id"]
    cid = prod.service.connectors.save(pid, "http://desk.test", [prod.ctx], confirmed=True, environment="production", secret_ref="file:" + str(path))
    promo = prod.service.promote(prod.source["id"], cid, "Mona Admin", "sandbox-only", "oncall@example.com", True)
    assert promo["checklist"]["secret_resolved"]["ok"] and prod.call(promo, "alice")["status"] == "succeeded"


def test_the_artifact_page_shows_the_promotion_read_only(prod):
    d = prod
    promo = d.promote()
    page = d.client.get(f"/artifacts/{d.source['id']}").text
    assert "Production" in page and "Mona Admin" in page and "oncall@example.com" in page and "smoke" in page
    assert 'action="/actions/promote' not in page and "rollback" in page.lower()
    business = [a for a in d.client.get(f"/api/v1/businesses/{d.business}/artifacts").json()]
    assert [a["id"] for a in business] == [d.source["id"]]  # the production twin is not listed as a second tool


def test_the_smoke_check_fills_other_required_inputs_from_the_schema_and_ignores_arguments_the_tool_lacks(prod):
    d = prod
    d.service.settings.production_smoke_arguments = json.dumps(dict(booking_reference=d.booking["alice"]["ref"], order_id="not-an-input-of-this-tool"))
    promo = d.promote()
    assert promo["checklist"]["smoke"]["ok"], promo["checklist"]["smoke"]


def test_a_promoted_tool_cannot_be_promoted_twice_but_can_be_again_after_a_rollback(prod):
    d = prod
    promo = d.promote()
    with pytest.raises(AppError) as exc:
        d.promote()
    assert exc.value.code == "already_promoted"
    d.service.rollback_promotion(promo["id"], "Mona Admin", "sandbox-only")
    again = d.promote()
    assert again["status"] == "active" and again["id"] != promo["id"]
    assert d.service.publication(again["publication_id"])["execution_enabled"]
