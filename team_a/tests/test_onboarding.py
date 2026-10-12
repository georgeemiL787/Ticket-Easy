"""A business added through the admin API: documents, status, atomic re-index, tenant isolation, rules and their history.

Everything runs in a temp folder (no Ollama: indexes are keyword-only) and never touches the shipped tenants."""

import base64
import dataclasses
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from team_a import config, jobs, service
from team_a.knowledge import documents as documents_module
from team_a.knowledge import index as index_module
from team_a.knowledge import retrieval as retrieval_module
from team_a.knowledge.parsers import parse
from team_a.policy import extract as extract_module
from team_a.policy import rules_store as rules_module

ADMIN = {"X-Admin-Key": "test-admin-key"}
T = "new_shop"
POLICY_MD = (
    "# Returns policy\n\n## 1. Return window\nYou may return an item within 14 days of delivery for a full refund.\n\n"
    "## 2. Damaged items\nReport a damaged item within 48 hours of delivery with a photo.\n"
)
POLICY_V2 = "# Returns policy\n\n## 1. Return window\nYou may return an item within 30 days of delivery for a full refund.\n"


@pytest.fixture
def api(tmp_path, monkeypatch):
    patched = dataclasses.replace(
        config.settings, onboarded_dir=tmp_path / "tenants", index_dir=tmp_path / "index", admin_api_key=ADMIN["X-Admin-Key"],
        service_api_key="", service_api_keys="", min_bm25=0.001,
    )
    for module in (config, documents_module, index_module, rules_module, retrieval_module):
        monkeypatch.setattr(module, "settings", patched)
    service._index.cache_clear()
    yield TestClient(service.app)
    service._index.cache_clear()


def upload(client, name, content, **extra):
    data = content if isinstance(content, bytes) else content.encode("utf-8")
    return client.post(f"/v1/admin/tenants/{T}/documents", headers=ADMIN, json={"filename": name, "content_base64": base64.b64encode(data).decode(), **extra})


def wait(client, job_id, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        job = client.get(f"/v1/admin/jobs/{job_id}", headers=ADMIN).json()
        if job["status"] != "running":
            return job
        time.sleep(0.1)
    raise AssertionError("the job did not finish")


def reindex(client):
    started = client.post(f"/v1/admin/tenants/{T}/reindex", headers=ADMIN, params={"embeddings": "false"})
    assert started.status_code == 202, started.text
    return wait(client, started.json()["job_id"])


def search(client, query, headers=None):
    return client.post("/v1/knowledge/search", headers=headers or {}, json={"request_id": "r1", "tenant_id": T, "query": query, "top_k": 3})


# ---- parsers ----

def test_text_and_html_files_become_sections(tmp_path):
    text = tmp_path / "terms.txt"
    text.write_text("1. Returns\nItems can be returned in 14 days.\n\n2. Refunds\nRefunds take 5 days.\n", encoding="utf-8")
    assert [(s.key, s.title) for s in parse(text)] == [("s1", "1. Returns"), ("s2", "2. Refunds")]
    plain = tmp_path / "faq.txt"
    plain.write_text("How do I return an item?\nWithin 14 days.\n\nWhere is my order?\nCheck the tracking link.\n", encoding="utf-8")
    assert len(parse(plain)) == 2
    page = tmp_path / "policy.html"
    page.write_text("<html><head><style>p{}</style></head><body><h1>Shipping</h1><h2>Delivery times</h2><p>Cairo: 2 days.</p>"
                    "<script>alert(1)</script><h2>Fees</h2><ul><li>Free over 500 EGP</li></ul></body></html>", encoding="utf-8")
    sections = parse(page)
    assert [s.title for s in sections] == ["Delivery times", "Fees"] or [s.title for s in sections][-2:] == ["Delivery times", "Fees"]
    assert "alert" not in " ".join(s.text for s in sections) and "Cairo: 2 days." in sections[0].text


def test_docx_xlsx_and_pdf_files_are_read(tmp_path):
    from docx import Document
    from fpdf import FPDF
    from openpyxl import Workbook

    doc = Document()
    doc.add_heading("Warranty", level=1)
    doc.add_heading("1. Cover", level=2)
    doc.add_paragraph("Two years for electronics.")
    doc.save(tmp_path / "w.docx")
    assert "Two years" in parse(tmp_path / "w.docx")[0].text
    book = Workbook()
    sheet = book.active
    sheet.append(["id", "question_en", "answer_en"])
    sheet.append(["faq1", "Do you ship abroad?", "No, Egypt only."])
    book.save(tmp_path / "f.xlsx")
    assert parse(tmp_path / "f.xlsx")[0].key == "faq1"
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.cell(0, 10, "1. Returns", new_x="LMARGIN", new_y="NEXT")
    pdf.multi_cell(0, 8, "Items may be returned within 14 days.")
    pdf.output(str(tmp_path / "p.pdf"))
    assert "14 days" in " ".join(s.text for s in parse(tmp_path / "p.pdf"))


# ---- documents, status and the re-index ----

def test_upload_status_reindex_and_search(api):
    first = upload(api, "Returns Policy.md", POLICY_MD)
    assert first.status_code == 201, first.text
    assert first.json()["status"] == "uploaded" and first.json()["sections"] >= 2 and first.json()["document_id"] == "returns_policy"
    listed = api.get(f"/v1/admin/tenants/{T}/documents", headers=ADMIN).json()
    assert listed["managed"] and [(d["document_id"], d["status"]) for d in listed["documents"]] == [("returns_policy", "uploaded")]
    assert search(api, "return window").status_code in (404, 503)  # not in the search before the re-index
    job = reindex(api)
    assert job["status"] == "succeeded" and job["result"]["current_passages"] >= 2 and job["result"]["embeddings"] is False
    found = search(api, "How many days to return an item?").json()
    assert found["passages"] and found["passages"][0]["citation"].startswith("returns_policy@v1#")
    assert api.get(f"/v1/admin/tenants/{T}/documents", headers=ADMIN).json()["documents"][0]["status"] == "indexed"


def test_a_new_version_supersedes_the_old_and_the_search_cites_the_current_one(api):
    upload(api, "returns.md", POLICY_MD)
    reindex(api)
    second = upload(api, "returns.md", POLICY_V2)
    assert second.json()["version"] == "v2"
    reindex(api)
    manifest = json.loads((config.settings.onboarded_dir / T / "corpus" / "manifest.json").read_text(encoding="utf-8"))
    assert [(d["version"], d["current"]) for d in manifest["documents"]] == [("v1", False), ("v2", True)]
    top = search(api, "return window days").json()["passages"][0]
    assert top["citation"].startswith("returns@v2#") and "30 days" in top["text"]


def test_an_unreadable_file_is_reported_and_does_not_stop_the_others(api):
    broken = upload(api, "scan.pdf", b"%PDF-1.4 this is not a real pdf")
    assert broken.status_code == 201 and broken.json()["status"] == "failed" and broken.json()["error"]
    empty = upload(api, "blank.txt", "   \n\n  ")
    assert empty.json()["status"] == "failed" and "no readable text" in empty.json()["error"]
    upload(api, "returns.md", POLICY_MD)
    job = reindex(api)
    assert job["status"] == "succeeded"
    statuses = {d["filename"]: d["status"] for d in api.get(f"/v1/admin/tenants/{T}/documents", headers=ADMIN).json()["documents"]}
    assert statuses == {"scan.pdf": "failed", "blank.txt": "failed", "returns.md": "indexed"}


@pytest.mark.parametrize(("name", "payload", "message"), [
    ("macro.exe", base64.b64encode(b"MZ").decode(), "unsupported file type"),
    ("what?.md", base64.b64encode(b"# x\n## 1. a\nb").decode(), "plain file name"),
    ("empty.md", "", "content_base64"),
    ("notbase64.md", "***", "not valid base64"),
])
def test_bad_uploads_are_refused(api, name, payload, message):
    reply = api.post(f"/v1/admin/tenants/{T}/documents", headers=ADMIN, json={"filename": name, "content_base64": payload})
    assert reply.status_code == 422 and message in reply.json()["error"]["message"]


def test_a_path_in_the_file_name_cannot_leave_the_corpus_folder(api):
    reply = upload(api, "../../evil.md", "# x\n\n## 1. a\nsome text here\n")
    assert reply.status_code == 201 and reply.json()["filename"] == "evil.md"
    root = config.settings.onboarded_dir / T
    assert sorted(p.name for p in (root / "corpus").iterdir() if p.suffix == ".md") == ["evil.v1.md"]
    assert not list(root.parent.glob("evil*")) and not list(root.glob("evil*"))


def test_an_oversized_upload_is_refused(api, monkeypatch):
    small = dataclasses.replace(config.settings, max_upload_bytes=100)
    for module in (config, documents_module):
        monkeypatch.setattr(module, "settings", small)
    reply = upload(api, "big.md", "# t\n## 1. a\n" + "x" * 500)
    assert reply.status_code == 413


def test_removing_a_document_takes_it_out_after_a_reindex(api):
    upload(api, "returns.md", POLICY_MD)
    upload(api, "shipping.md", "# Shipping\n\n## 1. Delivery\nDelivery takes 2 days in Cairo.\n")
    reindex(api)
    assert api.delete(f"/v1/admin/tenants/{T}/documents/shipping", headers=ADMIN).json()["reindex_needed"] is True
    reindex(api)
    citations = {p["citation"].split("@")[0] for p in search(api, "delivery days cairo").json()["passages"]}
    assert "shipping" not in citations
    assert api.delete(f"/v1/admin/tenants/{T}/documents/never_uploaded", headers=ADMIN).status_code == 404


def test_a_failed_rebuild_leaves_the_live_index_untouched(api, monkeypatch):
    upload(api, "returns.md", POLICY_MD)
    reindex(api)
    before = search(api, "return window").json()
    upload(api, "more.md", "# More\n\n## 1. Extra\nSomething else entirely.\n")

    def boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(documents_module, "build_index", boom)
    job = reindex(api)
    assert job["status"] == "failed" and "disk full" in job["error"]
    assert search(api, "return window").json() == before  # searches still use the old index
    assert not (config.settings.index_dir / f"{T}.building").exists()
    statuses = {d["filename"]: d["status"] for d in api.get(f"/v1/admin/tenants/{T}/documents", headers=ADMIN).json()["documents"]}
    assert statuses["more.md"] == "uploaded"  # still waiting for a successful re-index


def test_two_reindexes_of_one_business_do_not_run_together(api, monkeypatch):
    upload(api, "returns.md", POLICY_MD)
    gate = {"open": False}
    real = documents_module.build_index

    def slow(*args, **kwargs):
        while not gate["open"]:
            time.sleep(0.05)
        return real(*args, **kwargs)

    monkeypatch.setattr(documents_module, "build_index", slow)
    first = api.post(f"/v1/admin/tenants/{T}/reindex", headers=ADMIN, params={"embeddings": "false"})
    assert first.status_code == 202
    second = api.post(f"/v1/admin/tenants/{T}/reindex", headers=ADMIN, params={"embeddings": "false"})
    assert second.status_code == 409
    gate["open"] = True
    assert wait(api, first.json()["job_id"])["status"] == "succeeded"


def test_the_admin_endpoints_need_the_admin_key_and_shipped_businesses_are_read_only(api):
    assert api.get(f"/v1/admin/tenants/{T}/documents").status_code == 401
    assert api.post(f"/v1/admin/tenants/{T}/documents", json={"filename": "a.md", "content_base64": "eA=="}).status_code == 401
    assert api.post(f"/v1/admin/tenants/{T}/reindex").status_code == 401
    shipped = api.post("/v1/admin/tenants/shop_001/documents", headers=ADMIN, json={"filename": "a.md", "content_base64": "eA=="})
    assert shipped.status_code == 409
    assert api.post("/v1/admin/tenants/shop_001/reindex", headers=ADMIN).status_code == 409
    assert api.get("/v1/admin/tenants/shop_001/documents", headers=ADMIN).json()["managed"] is False
    assert api.get("/v1/admin/tenants/ghost/documents", headers=ADMIN).status_code == 404


# ---- tenant isolation ----

def test_a_service_key_reads_only_the_businesses_it_is_bound_to(api, monkeypatch):
    upload(api, "returns.md", POLICY_MD)
    reindex(api)
    keyed = dataclasses.replace(config.settings, service_api_keys="key-a=new_shop,key-b=shop_001,key-all=*")
    for module in (config, documents_module, index_module, rules_module, retrieval_module):
        monkeypatch.setattr(module, "settings", keyed)
    assert search(api, "return window").status_code == 401  # a key is now required
    assert search(api, "return window", {"X-Service-Key": "nope"}).status_code == 401
    assert search(api, "return window", {"X-Service-Key": "key-a"}).status_code == 200
    other = search(api, "return window", {"X-Service-Key": "key-b"})  # a key for another business
    assert other.status_code == 403 and other.json()["error"]["code"] == "FORBIDDEN"
    assert search(api, "return window", {"X-Service-Key": "key-all"}).status_code == 200
    passages = api.get("/v1/knowledge/passages", params={"tenant_id": T, "citation": "returns@v1#s1"}, headers={"X-Service-Key": "key-b"})
    assert passages.status_code == 403
    assert api.post("/v1/knowledge/past-tickets/search", headers={"X-Service-Key": "key-b"}, json={"request_id": "r", "tenant_id": T, "query": "x"}).status_code == 403
    assert api.post("/v1/policy/classify-risk", headers={"X-Service-Key": "key-b"}, json={"request_id": "r", "tenant_id": T, "message": "hi", "use_llm": False}).status_code == 403
    assert api.get("/v1/policy/rules/ruleset", params={"tenant_id": T}, headers={"X-Service-Key": "key-b"}).status_code == 403
    assert api.get("/v1/health" if False else "/health").status_code == 200  # health stays open


# ---- rules: proposed, approved, stale, history, ruleset version ----

RULE_JSON = {"rules": [{
    "action": "create_refund", "applies_if": [], "conditions": [{"field": "days_since_delivery", "op": "<=", "value": 14, "from": "facts"}],
    "effect": "allow", "else_effect": "deny", "quote": "You may return an item within 14 days of delivery for a full refund.",
    "user_message": {"ar": "يمكنك الاسترجاع خلال 14 يوماً", "en": "Returns are accepted within 14 days."},
}]}


def test_extraction_only_proposes_and_a_person_approves(api, monkeypatch):
    upload(api, "returns.md", POLICY_MD)
    reindex(api)
    monkeypatch.setattr(extract_module.llm, "complete_json", lambda system, prompt: RULE_JSON if "14 days" in prompt else {"rules": []})
    started = api.post(f"/v1/admin/tenants/{T}/rules/extract", headers=ADMIN, json={})
    job = wait(api, started.json()["job_id"])
    assert job["status"] == "succeeded" and job["result"]["added"] == 1
    listing = api.get(f"/v1/admin/tenants/{T}/rules", headers=ADMIN).json()
    [rule] = listing["rules"]
    assert rule["approval_status"] == "proposed" and rule["stale"] is False
    assert listing["ruleset"]["count"] == 0  # a proposed rule is not enforced
    empty_version = listing["ruleset"]["ruleset_version"]
    decision = api.post("/v1/policy/check-action", json={
        "request_id": "r", "tenant_id": T, "as_of": "2030-01-01", "tool": {"name": "create_refund", "operation_kind": "create", "risk": "high"},
        "facts": {"order_status": "delivered", "delivered_at": "2029-11-01"}, "arguments": {"amount": 100}, "identity": {"verified": True, "customer_id": "C1"}}).json()
    assert not decision["rule_outcomes"]  # nothing evaluated: the rule is only proposed
    approved = api.post(f"/v1/policy/rules/{rule['rule_id']}/approve", headers=ADMIN, json={"tenant_id": T, "reviewer": "Mona"})
    assert approved.status_code == 200, approved.text
    after = api.get(f"/v1/admin/tenants/{T}/rules", headers=ADMIN).json()
    assert after["ruleset"]["count"] == 1 and after["ruleset"]["ruleset_version"] != empty_version
    events = [h["event"] for h in after["rules"][0]["history"]]
    assert events == ["proposed", "approved"] and after["rules"][0]["history"][1]["by"] == "Mona"
    enforced = api.post("/v1/policy/check-action", json={
        "request_id": "r", "tenant_id": T, "as_of": "2030-01-01", "tool": {"name": "create_refund", "operation_kind": "create", "risk": "high"},
        "facts": {"order_status": "delivered", "delivered_at": "2029-11-01", "days_since_delivery": 58}, "arguments": {"amount": 100},
        "identity": {"verified": True, "customer_id": "C1"}}).json()
    assert enforced["rule_outcomes"] and enforced["rule_outcomes"][0]["rule_id"] == rule["rule_id"], enforced
    assert enforced["decision"] in ("deny", "require_human")  # the approved rule now takes part in the decision


def test_a_rule_whose_document_version_is_replaced_becomes_stale_and_is_not_enforced(api, monkeypatch):
    upload(api, "returns.md", POLICY_MD)
    reindex(api)
    monkeypatch.setattr(extract_module.llm, "complete_json", lambda system, prompt: RULE_JSON if "14 days" in prompt else {"rules": []})
    wait(api, api.post(f"/v1/admin/tenants/{T}/rules/extract", headers=ADMIN, json={}).json()["job_id"])
    rule_id = api.get(f"/v1/admin/tenants/{T}/rules", headers=ADMIN).json()["rules"][0]["rule_id"]
    api.post(f"/v1/policy/rules/{rule_id}/approve", headers=ADMIN, json={"tenant_id": T, "reviewer": "Mona"})
    version_before = api.get("/v1/policy/rules/ruleset", params={"tenant_id": T}).json()
    assert version_before["approved_rule_ids"] == [rule_id]
    upload(api, "returns.md", POLICY_V2)  # a new version of the same document: the 14-day text is no longer current
    listing = api.get(f"/v1/admin/tenants/{T}/rules", headers=ADMIN).json()
    assert listing["rules"][0]["stale"] is True and listing["rules"][0]["approval_status"] == "approved"
    after = api.get("/v1/policy/rules/ruleset", params={"tenant_id": T}).json()
    assert after["approved_rule_ids"] == [] and after["ruleset_version"] != version_before["ruleset_version"]


def test_the_ruleset_version_is_stable_and_tied_to_the_approved_rules(rule_store):
    first = rule_store.ruleset()
    assert first == rule_store.ruleset() and len(first["ruleset_version"]) == 16 and first["count"] == len(first["approved_rule_ids"])
    proposed = next(r for r in rule_store.all() if r.approval_status == "proposed")
    rule_store.reject(proposed.rule_id, "Ana")
    assert rule_store.ruleset() == first  # a rule that is not approved does not change what is enforced
    history = rule_store.history(proposed.rule_id)
    assert history and history[-1]["event"] == "rejected" and history[-1]["by"] == "Ana"
    approved = next(r for r in rule_store.all() if r.approval_status == "approved")
    rule_store.edit(approved.rule_id, {"user_message": {"ar": "x", "en": "changed"}})
    changed = rule_store.ruleset()
    assert changed["ruleset_version"] != first["ruleset_version"] and approved.rule_id not in changed["approved_rule_ids"]


@pytest.fixture(autouse=True)
def _clean_jobs():
    jobs._jobs.clear()
    yield
    jobs._jobs.clear()
