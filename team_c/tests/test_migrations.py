"""Ordered, recorded schema migrations: fresh creation, upgrades of earlier schemas, idempotency, rollback."""
import sqlite3
import threading
import pytest
from team_c.persistence import MIGRATIONS, Store, applied, migrate
from team_c.persistence.migrations import BASELINE

V1_MARKER = "INSERT OR IGNORE INTO schema_version VALUES(2);"


def schema(db):
    c = sqlite3.connect(db)
    master = sorted(tuple(r) for r in c.execute("SELECT type,name,tbl_name,sql FROM sqlite_master"))
    columns = {t: [tuple(r) for r in c.execute(f"PRAGMA table_info({t})")] for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return master, columns


def recorded(db):
    c = sqlite3.connect(db)
    return [tuple(r) for r in c.execute("SELECT version,name,applied_at FROM schema_migrations ORDER BY version")]


def build(db, sql):
    c = sqlite3.connect(db)
    for statement in sql.split(";\n"):
        if statement.strip():
            c.execute(statement)
    c.commit()
    return c


def v1_era(db):
    """Early database: schema_version 1 only, no requirement/artifact/execution tables, no owner columns."""
    early = [s for s in BASELINE.split(V1_MARKER)[0].split(";\n") if "discovery_cache" not in s and "revision_requests" not in s]
    c = build(db, ";\n".join(early))
    c.execute("INSERT INTO businesses VALUES('b1','Shop','A shop','2025-01-01')")
    c.execute("INSERT INTO specifications VALUES('s1','b1','api.json','abc',X'7B7D',NULL,'{}','2025-01-01')")
    c.execute("INSERT INTO runs(id,business_id,kind,input_hash,status,result,created_at,completed_at) VALUES('r1','b1','generation','h','succeeded','{\"ok\":1}','2025-01-01','2025-01-01')")
    c.execute("INSERT INTO runs(id,business_id,kind,input_hash,status,created_at) VALUES('r2','b1','generation','h','running','2025-01-01')")
    c.execute("INSERT INTO attempts(run_id,provider,model,status,created_at) VALUES('r1','p','m','succeeded','2025-01-01')")
    c.commit()


def recent(db):
    """Recent database: schema_version 1,2 and owner/scenario columns, but no selector_sha256 or gap_dismissals."""
    c = build(db, BASELINE)
    for table, column in (("runs", "owner"), ("executions", "owner"), ("sandbox_tests", "scenario"), ("sandbox_tests", "record_owner")):
        c.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")
    c.execute("INSERT INTO businesses VALUES('b1','Shop','A shop','2025-06-01')")
    c.execute("INSERT INTO runs(id,business_id,kind,input_hash,status,created_at,owner) VALUES('r1','b1','generation','h','succeeded','2025-06-01','o1')")
    c.execute("INSERT INTO executions(id,artifact_id,mode,identity,status,report,created_at,owner) VALUES('e1','a1','sandbox','bob','succeeded','{}','2025-06-01','o1')")
    c.execute("INSERT INTO sandbox_tests(id,artifact_id,execution_id,name,expectation,verdict,created_at,scenario,record_owner) VALUES('t1','a1','e1','case','pass','passed','2025-06-01','sc','alice')")
    c.commit()


def test_migrations_are_contiguous_and_named_once():
    assert [m[0] for m in MIGRATIONS] == list(range(1, len(MIGRATIONS) + 1))
    assert len({m[1] for m in MIGRATIONS}) == len(MIGRATIONS)


def test_fresh_database_gets_every_table_column_and_records_all_migrations(tmp_path):
    db = str(tmp_path / "fresh.sqlite3")
    store = Store(db)
    _, columns = schema(db)
    assert {"gap_dismissals", "question_supersessions", "requirements", "artifacts", "executions", "sandbox_tests", "publications", "schema_version", "schema_migrations"} <= set(columns)
    assert "owner" in [c[1] for c in columns["runs"]] and "owner" in [c[1] for c in columns["executions"]]
    assert {"scenario", "record_owner", "selector_sha256"} <= {c[1] for c in columns["sandbox_tests"]}
    assert [(m["version"], m["name"]) for m in store.migrations()] == [(m[0], m[1]) for m in MIGRATIONS]
    assert [r[0] for r in sqlite3.connect(db).execute("SELECT version FROM schema_version ORDER BY version")] == [1, 2]


@pytest.mark.parametrize("old", [v1_era, recent])
def test_earlier_schemas_upgrade_to_the_fresh_schema_without_data_loss(tmp_path, old):
    fresh, db = str(tmp_path / "fresh.sqlite3"), str(tmp_path / "old.sqlite3")
    old(db)
    before = {t: [tuple(r) for r in sqlite3.connect(db).execute(f"SELECT * FROM {t} ORDER BY rowid")] for t in ("businesses", "runs")}
    Store(fresh)
    store = Store(db)
    assert schema(db) == schema(fresh)
    assert [(m["version"], m["name"]) for m in store.migrations()] == [(m[0], m[1]) for m in MIGRATIONS]
    assert [tuple(r.values()) for r in store.all("SELECT * FROM businesses ORDER BY rowid")] == before["businesses"]
    runs = {r["id"]: r for r in store.all("SELECT * FROM runs")}
    assert len(runs) == len(before["runs"]) and runs["r1"]["status"] == "succeeded"
    if old is v1_era:
        assert runs["r1"]["result"] == '{"ok":1}' and runs["r1"]["owner"] is None
        assert runs["r2"]["status"] == "interrupted"  # ownerless running rows predate ownership tracking
        assert store.one("SELECT * FROM specifications WHERE id='s1'")["raw"] == b"{}"
        assert len(store.all("SELECT * FROM attempts")) == 1
    else:
        assert runs["r1"]["owner"] == "o1"
        assert store.one("SELECT * FROM executions WHERE id='e1'")["status"] == "succeeded"
        t = store.one("SELECT * FROM sandbox_tests WHERE id='t1'")
        assert (t["scenario"], t["record_owner"], t["selector_sha256"]) == ("sc", "alice", None)


def test_reopening_is_idempotent(tmp_path):
    db = str(tmp_path / "again.sqlite3")
    Store(db)
    first = recorded(db)
    Store(db)
    Store(db)
    assert recorded(db) == first and len(first) == len(MIGRATIONS)


def test_a_failing_migration_rolls_back_is_not_recorded_and_stops_later_ones(tmp_path):
    db = str(tmp_path / "broken.sqlite3")
    Store(db)

    def broken(conn):
        conn.execute("CREATE TABLE half_done(id TEXT)")
        conn.execute("ALTER TABLE runs ADD COLUMN half TEXT")
        raise RuntimeError("boom")

    def later(conn):
        conn.execute("CREATE TABLE later(id TEXT)")

    n = len(MIGRATIONS)
    conn = sqlite3.connect(db, isolation_level=None)
    with pytest.raises(RuntimeError):
        migrate(conn, MIGRATIONS + ((n + 1, "broken", broken), (n + 2, "later", later)))
    assert not conn.in_transaction
    assert [m["version"] for m in applied(conn)] == list(range(1, n + 1))
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
    assert "half_done" not in tables and "later" not in tables
    assert "half" not in [r[1] for r in conn.execute("PRAGMA table_info(runs)")]
    migrate(conn, MIGRATIONS + ((n + 1, "fixed", lambda c: None), (n + 2, "later", later)))
    assert [(m["version"], m["name"]) for m in applied(conn)][-2:] == [(n + 1, "fixed"), (n + 2, "later")]


def test_concurrent_starts_apply_each_migration_once(tmp_path):
    db = str(tmp_path / "race.sqlite3")
    errors = []

    def start():
        try:
            conn = sqlite3.connect(db, timeout=15, isolation_level=None)
            migrate(conn)
            conn.close()
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=start) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert [r[0] for r in recorded(db)] == [m[0] for m in MIGRATIONS]
