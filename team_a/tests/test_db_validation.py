"""Data-layer validation: integrity, scenario facts, tenant isolation, mirrors equal their source files."""

import json
import sqlite3

import pytest

from team_a.config import settings
from team_a.db import connect
from team_a.db import repository as repo
from team_a.schemas import SearchKnowledgeRequest, SearchPastTicketsRequest

TENANTS = ("noon_eg", "shop_001")


def _scenarios() -> list[dict]:
    path = settings.mock_dir("noon_eg") / "noon_scenarios.json"
    return json.loads(path.read_text(encoding="utf-8"))["scenarios"]


def _rows(db, sql, *params):
    conn = connect(db)
    try:
        return [tuple(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()


# ------------------------------------------------------------------ integrity

def test_every_foreign_key_resolves(built_db):
    assert _rows(built_db, "PRAGMA foreign_key_check") == []


def test_order_totals_equal_items_plus_fees(built_db):
    mismatches = _rows(built_db, """
        SELECT o.tenant_id, o.order_id, o.order_total, sum(i.qty * i.unit_price) + o.shipping_fee + o.cod_fee
        FROM orders o JOIN order_items i ON i.tenant_id = o.tenant_id AND i.order_id = o.order_id
        GROUP BY o.tenant_id, o.order_id HAVING abs(o.order_total - (sum(i.qty * i.unit_price) + o.shipping_fee + o.cod_fee)) > 0.001""")
    assert mismatches == []
    assert _rows(built_db, """SELECT count(*) FROM orders o WHERE NOT EXISTS
        (SELECT 1 FROM order_items i WHERE i.tenant_id = o.tenant_id AND i.order_id = o.order_id)""") == [(0,)]


def test_returns_belong_to_the_customer_who_placed_the_order(built_db):
    assert _rows(built_db, """SELECT r.return_id FROM returns r JOIN orders o
        ON o.tenant_id = r.tenant_id AND o.order_id = r.order_id WHERE o.customer_id != r.customer_id""") == []


def test_every_rule_citation_points_at_a_stored_passage(built_db):
    assert _rows(built_db, """SELECT r.rule_id, r.source_citation FROM rules_mirror r WHERE NOT EXISTS
        (SELECT 1 FROM policy_passages p WHERE p.tenant_id = r.tenant_id AND p.passage_id = r.source_citation)""") == []


# ------------------------------------------------------------------ noon scenarios

def test_every_policy_section_resolves_to_exactly_one_passage(built_db):
    for s in _scenarios():
        titles = s["expected"]["policy_sections"]
        stored = _rows(built_db, """SELECT es.section_title, es.passage_id, p.section FROM eval_scenario_sections es
            JOIN policy_passages p ON p.tenant_id = es.tenant_id AND p.passage_id = es.passage_id
            WHERE es.tenant_id = 'noon_eg' AND es.scenario_id = ?""", s["scenario_id"])
        assert sorted(t for t, _, _ in stored) == sorted(titles), s["scenario_id"]
        assert all(title == section and pid.startswith("noon_return_policy@v1#") for title, pid, section in stored)
    distinct = _rows(built_db, "SELECT section_title, count(DISTINCT passage_id) FROM eval_scenario_sections "
                               "WHERE tenant_id = 'noon_eg' GROUP BY section_title")
    assert len(distinct) == 21 and all(n == 1 for _, n in distinct)


CREATE_RETURN = [s for s in _scenarios() if s["type"] == "create_return"]


def test_there_are_25_create_return_scenarios():
    assert len(CREATE_RETURN) == 25


@pytest.mark.parametrize("scenario", CREATE_RETURN, ids=[s["scenario_id"] for s in CREATE_RETURN])
def test_facts_rebuilt_from_the_db_equal_the_scenario(built_db, scenario):
    cai = scenario["check_action_input"]
    facts = repo.build_check_action_facts("noon_eg", cai["arguments"]["order_id"], cai["arguments"]["item_id"],
                                          db_path=built_db)
    assert facts == cai["facts"]
    derived = repo.build_check_action_facts("noon_eg", cai["arguments"]["order_id"], cai["arguments"]["item_id"],
                                            include_derived=True, db_path=built_db)
    assert derived["days_since_delivery"] == scenario["days_since_delivery"]
    assert repo.get_customer("noon_eg", scenario["customer_id"], db_path=built_db)["verified"] == cai["customer_verified"]


# ------------------------------------------------------------------ tenant isolation

def _ids(db, table, key, tenant):
    return [r[0] for r in _rows(db, f"SELECT {key} FROM {table} WHERE tenant_id = ?", tenant)]


@pytest.mark.parametrize("tenant,other", [("noon_eg", "shop_001"), ("shop_001", "noon_eg")])
def test_lookups_never_return_another_tenants_rows(built_db, tenant, other):
    for cid in _ids(built_db, "customers", "customer_id", other):
        assert repo.get_customer(tenant, cid, db_path=built_db) is None
        assert repo.list_orders_for_customer(tenant, cid, db_path=built_db) == []
    for oid in _ids(built_db, "orders", "order_id", other):
        assert repo.get_order(tenant, oid, db_path=built_db) is None
    for oid, iid in _rows(built_db, "SELECT order_id, item_id FROM order_items WHERE tenant_id = ?", other):
        assert repo.build_check_action_facts(tenant, oid, iid, db_path=built_db) is None
    for rid in _ids(built_db, "returns", "return_id", other):
        assert repo.get_return(tenant, rid, db_path=built_db) is None
    for pid in _ids(built_db, "policy_passages", "passage_id", other):
        assert repo.get_passage(tenant, pid, db_path=built_db) is None


@pytest.mark.parametrize("tenant,other", [("noon_eg", "shop_001"), ("shop_001", "noon_eg")])
def test_own_lookups_only_return_own_rows(built_db, tenant, other):
    for cid in _ids(built_db, "customers", "customer_id", tenant):
        assert repo.get_customer(tenant, cid, db_path=built_db)["tenant_id"] == tenant
    for oid in _ids(built_db, "orders", "order_id", tenant):
        assert repo.get_order(tenant, oid, db_path=built_db)["tenant_id"] == tenant
    for rid in _ids(built_db, "returns", "return_id", tenant):
        assert repo.get_return(tenant, rid, db_path=built_db)["tenant_id"] == tenant


@pytest.mark.parametrize("tenant,other", [("noon_eg", "shop_001"), ("shop_001", "noon_eg")])
def test_search_never_crosses_tenants(built_db, tenant, other):
    own = set(_ids(built_db, "policy_passages", "passage_id", tenant))
    own_tickets = set(_ids(built_db, "past_tickets", "ticket_id", tenant))
    # Query with the other tenant's own wording: the best possible bait for a leak.
    texts = [r[0] for r in _rows(built_db, "SELECT text FROM policy_passages WHERE tenant_id = ?", other)]
    texts += [r[0] for r in _rows(built_db, "SELECT customer_message FROM past_tickets WHERE tenant_id = ?", other)]
    for text in texts:
        q = text[:500]
        result = repo.search_knowledge(SearchKnowledgeRequest(request_id="iso", tenant_id=tenant, query=q,
                                                              top_k=20, include_superseded=True), None, built_db)
        assert result.tenant_id == tenant and {p.citation for p in result.passages} <= own
        tickets = repo.search_past_tickets(SearchPastTicketsRequest(request_id="iso", tenant_id=tenant, query=q),
                                           None, built_db)
        assert {t.ticket_id for t in tickets.tickets} <= own_tickets


def test_same_id_in_two_tenants_resolves_per_tenant(tmp_path, built_db):
    """Ids don't collide in today's data, so plant a collision and check scoping still holds."""
    path = tmp_path / "collide.sqlite"
    with sqlite3.connect(built_db) as src, sqlite3.connect(path) as dst:
        src.backup(dst)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("INSERT INTO customers (tenant_id, customer_id, name) VALUES ('shop_001', 'C-1001', 'Shop twin')")
    conn.execute("INSERT INTO orders (tenant_id, order_id, customer_id, order_status, order_total) "
                 "VALUES ('shop_001', 'ORD-30001', 'C-1001', 'pending', 1)")
    conn.commit()
    conn.close()
    assert repo.get_customer("noon_eg", "C-1001", db_path=path)["name"] == "Nour Hassan"
    assert repo.get_customer("shop_001", "C-1001", db_path=path)["name"] == "Shop twin"
    assert repo.get_order("noon_eg", "ORD-30001", db_path=path)["order_status"] == "delivered"
    assert repo.get_order("shop_001", "ORD-30001", db_path=path)["items"] == []
    assert [o["order_id"] for o in repo.list_orders_for_customer("shop_001", "C-1001", db_path=path)] == ["ORD-30001"]


def test_schema_rejects_cross_tenant_references(tmp_path, built_db):
    path = tmp_path / "fk.sqlite"
    with sqlite3.connect(built_db) as src, sqlite3.connect(path) as dst:
        src.backup(dst)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    with pytest.raises(sqlite3.IntegrityError):  # a shop_001 order for a noon_eg customer
        conn.execute("INSERT INTO orders (tenant_id, order_id, customer_id, order_status, order_total) "
                     "VALUES ('shop_001', 'X-1', 'C-1001', 'pending', 1)")
    with pytest.raises(sqlite3.IntegrityError):  # a noon_eg item on a shop_001 order
        conn.execute("INSERT INTO order_items (tenant_id, item_id, order_id, line_no, name, product_category, qty, "
                     "unit_price) VALUES ('noon_eg', 'X-I1', 'NS-20877', 1, 'x', 'clothing', 1, 1)")


# ------------------------------------------------------------------ mirrors equal the source of truth

@pytest.mark.parametrize("tenant", TENANTS)
def test_rules_mirror_equals_the_json_file(built_db, tenant):
    path = settings.rules_file(tenant)
    expected = json.loads(path.read_text(encoding="utf-8"))["rules"] if path.exists() else []
    stored = [json.loads(r[0]) for r in _rows(built_db, "SELECT rule_json FROM rules_mirror WHERE tenant_id = ? "
                                                         "ORDER BY rowid", tenant)]
    assert stored == expected
    columns = _rows(built_db, "SELECT rule_id, action, approval_status, effect, effective_date, source_citation "
                              "FROM rules_mirror WHERE tenant_id = ? ORDER BY rowid", tenant)
    assert columns == [(r["rule_id"], r["action"], r["approval_status"], r["effect"], r["effective_date"],
                        r["source"]["citation"]) for r in expected]


@pytest.mark.parametrize("tenant", TENANTS)
def test_resolutions_mirror_equals_the_jsonl_file(built_db, tenant):
    path = settings.resolutions_file(tenant)
    expected = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] \
        if path.exists() else []
    stored = [dict(zip(("case_id", "tenant_id", "category", "redacted_summary", "resolution", "cited_rule_id",
                        "tags", "escalation_reason", "created_at"), r))
              for r in _rows(built_db, "SELECT case_id, tenant_id, category, redacted_summary, resolution, "
                                       "cited_rule_id, tags, escalation_reason, created_at FROM resolutions_mirror "
                                       "WHERE tenant_id = ? ORDER BY rowid", tenant)]
    for s in stored:
        s["tags"] = json.loads(s["tags"])
    assert stored == expected


def test_check_action_never_reads_the_database(monkeypatch, tmp_path):
    """Guardrail cases still pass with every SQLite connection refused, and the policy code has no db import."""
    import team_a.policy.check as check
    import team_a.policy.rules_store as rules_store
    from team_a.policy.guardrails import load_cases, run_cases

    for module in (check, rules_store):
        source = open(module.__file__, encoding="utf-8").read()
        assert "team_a.db" not in source and "sqlite" not in source

    def refuse(*_a, **_kw):
        raise AssertionError("check_action opened a database")

    monkeypatch.setattr(sqlite3, "connect", refuse)
    cases = load_cases("shop_001")
    failures, unsafe = run_cases(rules_store.RuleStore("shop_001"), cases)
    assert failures == [] and unsafe == [] and len(cases) == 44
