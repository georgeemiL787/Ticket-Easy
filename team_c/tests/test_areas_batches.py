"""Business areas and batching.

MOCKED / AUTHORED: area grouping, triage and suggestion outputs come from an AUTHORED TEST SUBSTITUTE that reads
the group keys and operation ids from the payload it receives; all data lives in per-test databases with the
in-process service-desk fixture. No live model is called.
"""
import json
import re
import pytest
from team_c import areas, capabilities
from team_c.discovery import discover
from team_c.models import AreaAssignmentOutput, AreaNamingOutput
from team_c.providers import input_fits, model_messages
from helpers.desk import make_desk, desk_proposal
from helpers.openapi import op
from helpers.tool_requests import RequestDesk, ask, idea, lookup_only, triage


def named(name, audience="customer"):
    return dict(name=name, description=f"TEST-ONLY {name}", audience=audience, reason="TEST-ONLY")


class AreaDesk(RequestDesk):
    """AUTHORED TEST SUBSTITUTE: scripted area names and a scripted (group key, area names) -> area name assigner."""
    def __init__(self, settings, store):
        super().__init__(settings, store)
        self.namings, self.assign = [], None

    def call(self, kind, payload, output_model, run):
        if kind in ("area_naming", "area_assignment"):
            self.payloads.append((kind, payload))
            self.store.attempt(run, "TEST_SUBSTITUTE", "authored-test-only", "succeeded")
            if kind == "area_naming":
                return AreaNamingOutput.model_validate(dict(areas=self.namings.pop(0)))
            names = [a["name"] for a in payload["areas"]]
            return AreaAssignmentOutput.model_validate(dict(assignments={g["key"]: self.assign(g["key"], names) for g in payload["groups"]}))
        return super().call(kind, payload, output_model, run)


@pytest.fixture
def desk(tmp_path, monkeypatch):
    monkeypatch.setattr("helpers.desk.DeskSubstitute", AreaDesk)
    d = make_desk(tmp_path, "base")
    d.service = d.app.state.service
    inv, n = d.spec["inventory"], d.n
    d.lookup, d.create = op(inv, "GET", n["prefix"] + n["lookup"])["id"], op(inv, "POST", n["prefix"] + n["create"])["id"]
    d.health = op(inv, "GET", "/health")["id"]
    d.attach = next(o["id"] for o in inv["operations"] if not o["proposal_eligible"])
    d.bid = d.service.spec(d.spec["id"])["business_id"]
    d.sid = d.spec["id"]
    yield d
    d.client.__exit__(None, None, None)


def document(paths):
    ok = {"200": {"description": "ok", "content": {"application/json": {"schema": {"type": "object", "properties": {"id": {"type": "string"}}}}}}}
    spec = {"openapi": "3.1.0", "info": {"title": "t", "version": "1"}, "paths": {}}
    for path, tags in paths:
        spec["paths"][path] = {"get": dict(responses=ok, **({"tags": tags} if tags else {}))}
    return discover(json.dumps(spec).encode(), "t.json", "biz", 50)[1]


def area_of(view, operation_id):
    return next(a for a in view["areas"] if operation_id in a["operation_ids"])


def test_base_groups_come_from_tags_then_from_the_documents_own_paths():
    inv = document([("/api/v1/patients/{id}", ["patients"]), ("/api/v1/clinics", ["clinics"]), ("/api/v1/devices/{id}", None),
                    ("/api/v1/devices", None), ("/health", None)])
    assert {g["key"] for g in areas.base_groups(inv)} == {"patients", "clinics", "devices", "health"}
    # No prefix is shared by most paths with a split below it: the first segment is the group.
    plain = document([("/items", None), ("/items/{id}", None), ("/items/{id}/notes", None), ("/orders/{id}", None)])
    assert {g["key"] for g in areas.base_groups(plain)} == {"items", "orders"}
    groups = areas.base_groups(inv)
    assert areas.from_groups(groups, "x")[0]["group_keys"] == [groups[0]["key"]]
    assert sorted(i for g in groups for i in g["operation_ids"]) == sorted(o["id"] for o in inv["operations"])


def test_ai_grouping_is_validated_and_falls_back_to_the_api_groups(desk):
    d = desk
    base = d.client.get(f"/api/v1/specifications/{d.sid}/areas").json()
    assert base["source"] == "api" and base["selected"] is None and sum(len(a["operation_ids"]) for a in base["areas"]) == 4
    organize = lambda: d.client.post(f"/api/v1/specifications/{d.sid}/areas/organize").json()
    # Duplicate area names and assignments to an unnamed area are rejected; the API's own groups stay and the failure is visible.
    d.provider.namings.append([named("Same"), named("same")])
    failed = organize()
    assert failed["source"] == "api" and failed["error"]["code"] == "invalid_area_grouping" and len(failed["areas"]) == len(base["areas"])
    assert d.service.store.one("SELECT status FROM runs WHERE id=?", (failed["run_id"],))["status"] == "failed"
    d.provider.namings.append([named("Customer service")])
    d.provider.assign = lambda key, names: "Invented area"
    failed = organize()
    assert failed["error"]["code"] == "invalid_area_grouping" and dict(d.service.store.one("SELECT kind,status FROM runs WHERE id=?", (failed["run_id"],))) == dict(kind="area_assignment", status="failed")
    # Areas keep the model's priority order; an area that receives no group is dropped.
    d.provider.namings.append([named("Customer service"), named("Unused"), named("Technical", "internal")])
    d.provider.assign = lambda key, names: "Technical" if key == "health" else "Customer service"
    good = organize()
    assert good["source"] == "ai" and good["error"] is None and [a["name"] for a in good["areas"]] == ["Customer service", "Technical"]
    assert [a["id"] for a in good["areas"]] == ["a1", "a2"] and set(area_of(good, d.health)["group_keys"]) == {"health"}
    naming, assigning = [p for k, p in d.provider.payloads if k == "area_naming"][-1], [p for k, p in d.provider.payloads if k == "area_assignment"][-1]
    assert all("samples" not in g for g in naming["groups"]) and all("samples" in g for g in assigning["groups"])
    assert naming["business"]["name"].startswith("Service desk") and [a["name"] for a in assigning["areas"]] == ["Customer service", "Unused", "Technical"]
    assert good["suggested"] and good["suggested"][0] == "a1"
    # Selecting then reorganizing clears the selection because area ids change.
    d.client.post(f"/api/v1/specifications/{d.sid}/areas", json=dict(area_ids=["a1"]))
    d.provider.namings.append([named("All", "mixed")])
    d.provider.assign = lambda key, names: "All"
    assert organize()["selected"] is None


def test_groups_are_assigned_in_batches_and_organizing_runs_once_at_a_time(desk, monkeypatch):
    d = desk
    monkeypatch.setattr(areas, "ASSIGN_BATCH", 1)
    d.provider.namings.append([named("Everything", "mixed")])
    d.provider.assign = lambda key, names: "Everything"
    view = d.client.post(f"/api/v1/specifications/{d.sid}/areas/organize").json()
    batches = [p for k, p in d.provider.payloads if k == "area_assignment"]
    assert len(batches) == len(areas.base_groups(d.spec["inventory"])) and all(len(p["groups"]) == 1 for p in batches)
    assert [a["name"] for a in view["areas"]] == ["Everything"] and len(view["areas"][0]["operation_ids"]) == 4
    d.service.organizing.acquire()
    try:
        busy = d.client.post(f"/api/v1/specifications/{d.sid}/areas/organize")
        assert busy.status_code == 409 and busy.json()["code"] == "areas_busy"
    finally:
        d.service.organizing.release()


def test_selected_areas_limit_what_the_model_sees(desk):
    d = desk
    view = d.service.areas(d.sid)
    booking = area_of(view, d.lookup)
    health = area_of(view, d.health)
    assert booking["id"] != health["id"]
    bad = d.client.post(f"/api/v1/specifications/{d.sid}/areas", json=dict(area_ids=["nope"]))
    assert bad.status_code == 422 and bad.json()["code"] == "unknown_area"
    chosen = d.client.post(f"/api/v1/specifications/{d.sid}/areas", json=dict(area_ids=[booking["id"]])).json()
    assert chosen["selected"] == [booking["id"]] and set(chosen["included_ids"]) == set(booking["operation_ids"])
    d.provider.triage.append(triage("unavailable", missing=[("absent_operation", "TEST-ONLY: no such operation", ())]))
    d.client.post(f"/api/v1/businesses/{d.bid}/tool-requests", json=dict(goal="TEST-ONLY: show the service status", idempotency_key="areas-key-1"))
    sent = d.provider.payloads[-1][1]["operation_index"]
    assert {o["id"] for o in sent["operations"]} <= set(booking["operation_ids"]) and d.health not in {o["id"] for o in sent["operations"]}
    assert sent["coverage"]["area_scoped"] is True


def test_capability_outside_the_first_slice_is_found_by_expanding_retrieval(desk):
    d = desk
    inv, goal = d.spec["inventory"], "TEST-ONLY: health health reference"
    ranked = capabilities.operation_index(inv, 10 ** 9, None, (), goal)[1]["considered_ids"]
    # The capability the owner asks for (the lookup) is deliberately ranked outside the first slice.
    assert ranked[:2] == [d.health, d.lookup]
    sizes = {o["id"]: len(json.dumps(capabilities.entry(o), ensure_ascii=False)) for o in inv["operations"]}
    d.service.settings.capability_index_chars = sizes[d.health] + sizes[d.lookup] - 1
    d.provider.triage.append(triage("unavailable", missing=[("absent_operation", "TEST-ONLY: not in this slice", ())]))
    d.provider.triage.append(triage("feasible", [d.lookup]))
    d.provider.generations.append(lookup_only)
    req = ask(d, "expand-1", goal=goal).json()
    assert req["status"] == "proposed" and req["outcome"]["triage"] == "feasible"
    assert req["outcome"]["search"]["retrieval_rounds"] == 2
    # The second slice — the one the first slice cut off — is what the proposal was built from.
    assert set(req["coverage"]["all_considered_ids"]) == {d.health, d.lookup}
    slices = [{o["id"] for o in payload["operation_index"]["operations"]} for kind, payload in d.provider.payloads if kind == "request_triage"]
    assert slices[:2] == [{d.health}, {d.lookup}]


def test_absent_capability_searches_the_whole_catalog_before_declaring_absence(desk):
    d = desk
    inv, goal = d.spec["inventory"], "TEST-ONLY: show the service status"
    sizes = [len(json.dumps(capabilities.entry(o), ensure_ascii=False)) for o in inv["operations"]]
    d.service.settings.capability_index_chars = max(sizes) + 1      # one operation per retrieval round
    # Every scripted round claims absence, so retrieval has to keep fetching slices until nothing is
    # left unsearched: one slice is a statement about the slice, not about the API.
    for _ in range(8):
        d.provider.triage.append(triage("unavailable", missing=[("absent_operation", "TEST-ONLY: not in this slice", ())]))
    first = d.client.post(f"/api/v1/businesses/{d.bid}/tool-requests",
                          json=dict(goal=goal, idempotency_key="batch-key-1")).json()
    cov, search = first["coverage"], first["outcome"]["search"]
    assert first["status"] == "unavailable"
    assert cov["retrieval_rounds"] == 4 and cov["searched_operations"] == cov["total_operations"] == 4
    assert cov["partial_search"] is False and not cov["omitted_ids"]
    assert set(cov["all_considered_ids"]) == {d.lookup, d.create, d.health, d.attach}
    assert search == dict(total_operations=4, searched_operations=4, retrieval_rounds=4, partial_search=False)
    assert len(first["run_ids"]) == 4                       # every round stays auditable
    page = d.client.get(f"/businesses/{d.bid}").text
    assert "4 of 4 operations searched across 4 retrieval round(s)" in page
    assert "not found in the searched subset" not in page and "Look in the next batch" not in page
    done = d.client.post(f'/api/v1/tool-requests/{first["id"]}/next-batch')
    assert done.status_code == 409 and done.json()["code"] == "no_next_batch"


def test_retrieval_cap_reports_a_partial_search_rather_than_an_api_verdict(desk, monkeypatch):
    d = desk
    inv, goal = d.spec["inventory"], "TEST-ONLY: show the service status"
    sizes = [len(json.dumps(capabilities.entry(o), ensure_ascii=False)) for o in inv["operations"]]
    d.service.settings.capability_index_chars = max(sizes) + 1
    monkeypatch.setattr(capabilities, "RETRIEVAL_ROUNDS_CAP", 1)
    for _ in range(8):
        d.provider.triage.append(triage("unavailable", missing=[("absent_operation", "TEST-ONLY: not in this slice", ())]))
    first = d.client.post(f"/api/v1/businesses/{d.bid}/tool-requests",
                          json=dict(goal=goal, idempotency_key="batch-key-2")).json()
    cov = first["coverage"]
    assert first["status"] == "unavailable" and cov["retrieval_rounds"] == 1
    assert cov["partial_search"] is True and cov["searched_operations"] < cov["total_operations"] == 4
    page = d.client.get(f"/businesses/{d.bid}").text
    assert "not found in the searched subset" in page and "Look in the next batch" in page
    # The owner can still search what the cap left out; the cap never becomes the verdict.
    while cov["omitted_ids"]:
        cov = d.client.post(f'/api/v1/tool-requests/{first["id"]}/next-batch').json()["coverage"]
    assert set(cov["all_considered_ids"]) == {d.lookup, d.create, d.health, d.attach}
    assert cov["partial_search"] is False and cov["searched_operations"] == 4


def test_suggest_next_batch_continues_where_the_last_run_stopped(desk):
    d = desk
    d.service.settings.capability_index_chars = 250
    d.provider.ideas.append([])
    first = d.client.post(f"/api/v1/businesses/{d.bid}/suggestion-runs", json={}).json()
    considered = set(first["coverage"]["considered_ids"])
    assert first["coverage"]["omitted_ids"]
    assert "Suggest from the next batch" in d.client.get(f"/businesses/{d.bid}").text
    while True:
        d.provider.ideas.append([])
        nxt = d.client.post(f"/api/v1/businesses/{d.bid}/suggestion-runs", json=dict(next_batch=True)).json()
        batch_ids = set(nxt["coverage"]["considered_ids"])
        assert batch_ids and not batch_ids & considered
        considered |= batch_ids
        if not nxt["coverage"]["omitted_ids"]:
            break
    assert considered == {d.lookup, d.create, d.health, d.attach}
    assert "All 4 operations have been reviewed" in d.client.get(f"/businesses/{d.bid}").text
    done = d.client.post(f"/api/v1/businesses/{d.bid}/suggestion-runs", json=dict(next_batch=True))
    assert done.status_code == 409 and done.json()["code"] == "no_next_batch"


def test_generation_next_batch_fits_the_model_and_skips_used_operations(desk):
    d = desk
    first = d.client.get(f"/api/v1/specifications/{d.sid}/generation-batch").json()
    # The batch is whatever the provider's input allowance admits, and the shared system prompt is
    # part of that request, so its size moves whenever the prompt grows. Assert the batch covers the
    # inventory rather than a fixed count, and that every chosen operation really fits.
    assert first["operation_ids"] and set(first["operation_ids"]) <= {d.lookup, d.create, d.health} and first["used"] == 0
    business = d.service.store.one("SELECT * FROM businesses WHERE id=?", (d.bid,))
    fits = lambda ids: input_fits(d.service.settings, "generation", dict(business=business, inventory=d.service.scoped(d.spec["inventory"], ids), contract_version="1"))
    assert fits(first["operation_ids"])
    # A model limit that fits only one operation makes single-operation batches; each passes the provider's own check.
    size = lambda ids: len(model_messages("generation", dict(business=business, inventory=d.service.scoped(d.spec["inventory"], ids), contract_version="1"))[0])
    d.service.settings.max_model_chars = max(size([i]) for i in first["operation_ids"]) + 1
    small = d.service.next_generation_batch(d.sid)
    assert len(small["operation_ids"]) == 1 and fits(small["operation_ids"]) and small["remaining"] == 2
    d.service.settings.max_model_chars = 100_000
    d.provider.generations.append(lambda inv, n: dict(proposals=[desk_proposal(inv, n)], capability_gaps=[]))
    d.client.post(f"/api/v1/inventories/{d.sid}/proposal-runs", json=dict(operation_ids=[d.lookup, d.create]))
    after = d.service.next_generation_batch(d.sid)
    assert after["operation_ids"] == [d.health] and after["used"] == 2


def test_inventory_page_shows_areas_reasons_and_unticked_batches(desk):
    d = desk
    page = d.client.get(f"/specifications/{d.sid}").text
    assert "Business areas" in page and "Operations by area" in page and "Use these areas" in page and "Organize by business area (AI)" in page
    assert "Why: " in page and capabilities.reason(op(d.spec["inventory"], "GET", "/health")) is None
    advanced = page.split('id="advanced"')[1]
    assert 'name="operation_ids"' in advanced and " checked>" not in advanced and "Select the next batch that fits" in advanced
    picked = d.client.get(f"/specifications/{d.sid}?batch=next").text.split('id="advanced"')[1]
    ticked = set(re.findall(r'name="operation_ids" value="([^"]+)" checked', picked))
    # The pre-ticked box must be exactly the batch that fits the provider allowance.
    assert ticked == set(d.service.next_generation_batch(d.sid)["operation_ids"]) and ticked
    csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
    view = d.service.areas(d.sid)
    area = area_of(view, d.lookup)
    # Saving areas of the description that requests use moves the owner on to Get tools on the business page.
    r = d.client.post("/actions/areas_select", data=dict(csrf=csrf, spec_id=d.sid, area_ids=[area["id"]]))
    assert r.status_code == 200 and r.url.path == f"/businesses/{d.bid}" and r.url.query == b"saved=areas"
    assert "Areas saved. Now describe a tool" in r.text and f"business areas: {area['name']} (" in r.text
    inventory = d.client.get(f"/specifications/{d.sid}").text
    assert "Using 1 of" in inventory and "not used" in inventory
    both = [area, area_of(view, d.health)]
    r = d.client.post("/actions/areas_select", data=dict(csrf=csrf, spec_id=d.sid, area_ids=[a["id"] for a in both]))
    assert "business areas: " + ", ".join(a["name"] for a in view["areas"] if a in both) + " (" in r.text


def test_inline_reasons_name_file_uploads_form_logins_and_restriction_signals():
    upload = dict(id="u", supported=False, proposal_eligible=False, exposure=dict(classification="technically_unsupported", signals=[]),
                  binding=dict(errors=["unsupported_media: A nonempty payload must offer application/json"]),
                  original={"requestBody": {"content": {"multipart/form-data": {}}}})
    login = dict(upload, original={"requestBody": {"content": {"application/x-www-form-urlencoded": {}}}})
    restricted = dict(id="r", supported=True, proposal_eligible=False, exposure=dict(classification="restricted", signals=[dict(effect="restricts", detail="Path/operationId/tags mention login")]))
    assert capabilities.reason(upload) == "file upload (multipart/form-data)"
    assert capabilities.reason(login) == "form data (application/x-www-form-urlencoded)"
    assert capabilities.reason(restricted) == "Path/operationId/tags mention login"
    assert capabilities.reason(dict(id="e", supported=True, proposal_eligible=True)) is None
