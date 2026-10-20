"""Telecom tenant (telco_001) in the data layer: faithful copy, privacy exclusions, lookups, inert rules."""

import sqlite3

import pytest

from knowledge_service.db import loader
from knowledge_service.db import repository as repo
from knowledge_service.db import telecom
from knowledge_service.policy.check import check_action
from knowledge_service.policy.rules_store import RuleStore
from knowledge_service.schemas import CheckActionRequest, SearchKnowledgeRequest

T = "telco_001"


def test_every_telecom_table_matches_its_source(built_db, telecom_db):
    src = sqlite3.connect(f"file:{telecom_db.as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(built_db)
    for dest, source in telecom.TABLES:
        expected = src.execute(f"SELECT count(*) FROM {source}").fetchone()[0]
        got = dst.execute(f"SELECT count(*) FROM {dest} WHERE tenant_id = ?", (T,)).fetchone()[0]
        assert got == expected, dest
    assert dst.execute("SELECT count(*) FROM tel_customer_profiles WHERE tenant_id = ? AND is_demo = 1",
                       (T,)).fetchone()[0] == 20


def test_sensitive_source_columns_are_never_copied(built_db):
    conn = sqlite3.connect(built_db)
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
    columns = {c[1] for t in tables for c in conn.execute(f"PRAGMA table_info({t})")}
    assert not columns & {"national_id", "birth_date", "gender", "address", "puk", "imsi", "iccid",
                          "old_iccid", "new_iccid"}


def test_line_lookup_by_number(built_db):
    line = repo.get_line(T, msisdn="01012340001", db_path=built_db)  # demo customer TC-D001
    assert line["customer_id"] == "TC-D001" and line["tenant_id"] == T and line["line_type"] == "prepaid"
    assert line["plan"]["plan_id"] == line["plan_id"]
    assert "Net 55" in [b["name_en"] for b in line["active_bundles"]]
    assert repo.get_line(T, line_id=line["line_id"], db_path=built_db)["msisdn"] == "01012340001"
    assert repo.get_line(T, msisdn="01000000000", db_path=built_db) is None


def test_identity_check_needs_number_and_id_digits(built_db):
    assert repo.verify_identity(T, "01012340001", "4821", built_db) == "TC-D001"
    assert repo.verify_identity(T, "01012340001", " 48-21 ", built_db) == "TC-D001"
    assert repo.verify_identity(T, "01012340001", "0000", built_db) is None
    assert repo.verify_identity(T, "01000000000", "4821", built_db) is None
    assert "nid_last4" not in repo.get_customer(T, "TC-D001", built_db)  # the digits are never handed out


def test_account_invoice_transactions_and_complaints(built_db):
    conn = sqlite3.connect(built_db)
    account_id, invoice_id = conn.execute(
        "SELECT account_id, invoice_id FROM tel_invoices WHERE tenant_id = ? ORDER BY invoice_id LIMIT 1",
        (T,)).fetchone()
    account = repo.get_account(T, account_id, built_db)
    assert account["lines"] and all(line["line_id"] for line in account["lines"])
    invoices = repo.list_invoices(T, account_id, db_path=built_db)
    assert [i["issued_at"] for i in invoices] == sorted((i["issued_at"] for i in invoices), reverse=True)
    invoice = repo.get_invoice(T, invoice_id, built_db)
    assert invoice["items"] and invoice["account_id"] == account_id
    line_id = conn.execute("SELECT line_id FROM tel_transactions WHERE tenant_id = ? LIMIT 1", (T,)).fetchone()[0]
    txns = repo.list_transactions(T, line_id, db_path=built_db)
    assert txns and [t["created_at"] for t in txns] == sorted((t["created_at"] for t in txns), reverse=True)
    complaint_id = conn.execute("SELECT complaint_id FROM tel_refunds WHERE tenant_id = ? AND complaint_id "
                                "IS NOT NULL LIMIT 1", (T,)).fetchone()[0]
    assert repo.get_complaint(T, complaint_id, built_db)["refunds"]


@pytest.mark.parametrize("other", ["shop_001", "noon_eg"])
def test_telecom_lookups_never_cross_tenants(built_db, other):
    assert repo.get_line(other, msisdn="01012340001", db_path=built_db) is None
    assert repo.verify_identity(other, "01012340001", "4821", built_db) is None
    assert repo.get_customer(other, "TC-D001", built_db) is None
    assert repo.list_lines_for_customer(other, "TC-D001", built_db) == []


def test_telecom_search_cites_telecom_policies(built_db):
    req = SearchKnowledgeRequest(request_id="t", tenant_id=T, query="باقة اشتركت فيها بالغلط عايز استرد فلوسها")
    citations = [p.citation for p in repo.search_knowledge(req, None, built_db).passages]
    assert "refund_policy@v3#s1" in citations
    assert all(c.split("@")[0].endswith("_policy") for c in citations)


def test_imported_telecom_rules_have_no_effect_until_approved():
    rules = RuleStore(T).all()
    assert len(rules) == 19 and {r.approval_status for r in rules} == {"proposed"}
    req = CheckActionRequest.model_validate({
        "request_id": "t", "tenant_id": T,
        "tool": {"name": "refund_bundle", "operation_kind": "create", "risk": "medium"},
        "facts": {"hours_since_subscription": 1, "quota_used_percent": 1},
        "identity": {"verified": True, "customer_id": "TC-D001", "method": "line+nid_last4"},
        "as_of": "2026-10-08",
    })
    decision = check_action(req, RuleStore(T))
    assert decision.decision == "require_human" and decision.reason_code == "NO_RULE_SIDE_EFFECT"


def test_missing_telecom_source_says_how_to_generate_it(tmp_path):
    with pytest.raises(telecom.TelecomSourceMissing, match="generate.py"):
        loader.build([T], embedder=None, db_path=tmp_path / "x.sqlite",
                     telecom_dbs={T: tmp_path / "missing.db"})
