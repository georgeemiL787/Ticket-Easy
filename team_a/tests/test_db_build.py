"""SQLite data layer: build, idempotence, embeddings, read-only guarantees."""

import hashlib
import json
import sqlite3

import numpy as np
import pytest

from team_a.db import connect, loader, sources
from team_a.knowledge.embeddings import EmbeddingUnavailable


class FakeEmbedder:
    model = "fake-8d"

    def __init__(self):
        self.calls = 0

    def embed(self, texts):
        self.calls += len(texts)
        rows = [np.frombuffer(hashlib.sha256(t.encode()).digest()[:8], dtype=np.uint8).astype(np.float32) + 1
                for t in texts]
        m = np.stack(rows)
        return m / np.linalg.norm(m, axis=1, keepdims=True)


class DownEmbedder:
    model = "bge-m3"

    def embed(self, texts):
        raise EmbeddingUnavailable("Ollama is down")


def logical_dump(path) -> list:
    """Every row of every table except FTS5 internals (those depend on rowid history, not content)."""
    conn = sqlite3.connect(path)
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'passages_fts%' ORDER BY name")]
    return [(t, sorted(repr(tuple(r)) for r in conn.execute(f"SELECT * FROM {t}"))) for t in tables]


def test_build_loads_every_tenant(built_db):
    counts = loader.stats(built_db)
    assert sorted(counts) == ["noon_eg", "shop_001"]
    assert counts["noon_eg"]["customers"] == 11 and counts["noon_eg"]["orders"] == 56
    assert counts["noon_eg"]["order_items"] == 57 and counts["noon_eg"]["returns"] == 10
    assert counts["noon_eg"]["policy_passages"] == 32 and counts["noon_eg"]["eval_scenarios"] == 67
    assert counts["noon_eg"]["past_tickets"] == 18
    assert counts["shop_001"]["policy_passages"] == 40 and counts["shop_001"]["past_tickets"] == 20
    assert counts["shop_001"]["orders"] == 16 and counts["shop_001"]["rules_mirror"] == 33
    assert counts["shop_001"]["benchmark_questions"] == 70 + 44


def test_tenant_as_of_comes_from_the_data_not_the_clock(built_db):
    _, rows = loader.query("SELECT tenant_id, as_of FROM tenants ORDER BY tenant_id", db_path=built_db)
    assert rows == [("noon_eg", "2026-10-08"), ("shop_001", "2026-09-28")]


def test_build_is_idempotent_and_deterministic(tmp_path, built_db):
    path = tmp_path / "again.sqlite"
    loader.build(embedder=None, db_path=path)
    first = logical_dump(path)
    loader.build(embedder=None, db_path=path)               # rebuild in place
    assert logical_dump(path) == first
    loader.build(["noon_eg"], embedder=None, db_path=path)  # one tenant only
    assert logical_dump(path) == first
    assert logical_dump(built_db) == first                  # a separate fresh build


def test_fts_index_matches_its_content_table(built_db):
    conn = sqlite3.connect(built_db)
    conn.execute("INSERT INTO passages_fts(passages_fts, rank) VALUES ('integrity-check', 1)")
    hits = conn.execute(
        "SELECT p.passage_id FROM passages_fts f JOIN policy_passages p ON p.rowid = f.rowid "
        "WHERE passages_fts MATCH 'emi' AND f.tenant_id = 'noon_eg'").fetchall()
    assert hits == [("noon_return_policy@v1#s28",)]


def test_embeddings_are_stored_and_reused(tmp_path):
    path, embedder = tmp_path / "emb.sqlite", FakeEmbedder()
    report = loader.build(["noon_eg", "shop_001"], embedder=embedder, db_path=path)
    assert {r["retrieval_mode"] for r in report["tenants"].values()} == {"hybrid"}
    assert embedder.calls == 32 + 40 + 20 + 18
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT DISTINCT model, dim FROM passage_embeddings").fetchall() == [("fake-8d", 8)]
    blob = conn.execute("SELECT vector FROM passage_embeddings LIMIT 1").fetchone()[0]
    assert abs(np.linalg.norm(np.frombuffer(blob, dtype="<f4")) - 1) < 1e-5
    loader.build(embedder=embedder, db_path=path)
    assert embedder.calls == 32 + 40 + 20 + 18  # unchanged texts are not re-embedded


def test_ollama_down_falls_back_to_keyword_only(tmp_path):
    report = loader.build(["noon_eg"], embedder=DownEmbedder(), db_path=tmp_path / "down.sqlite")
    r = report["tenants"]["noon_eg"]
    assert r["retrieval_mode"] == "keyword_only" and r["rows"]["passage_embeddings"] == 0
    assert any("keyword-only" in w for w in r["warnings"])


def test_readonly_connection_rejects_writes(built_db):
    with pytest.raises(sqlite3.OperationalError):
        loader.query("DELETE FROM customers", db_path=built_db)
    conn = connect(built_db)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("UPDATE orders SET order_total = 0")


@pytest.mark.parametrize("sql", [
    "DELETE FROM rules_mirror",
    "UPDATE rules_mirror SET approval_status = 'approved'",
    "INSERT INTO resolutions_mirror VALUES ('shop_001','PREC-0000000000','x','s','r',NULL,'[]','policy_deny','2026-01-01')",
    "DELETE FROM resolutions_mirror",
])
def test_mirrors_reject_writes_even_on_a_writable_connection(tmp_path, built_db, sql):
    path = tmp_path / "copy.sqlite"
    src, dst = sqlite3.connect(built_db), sqlite3.connect(path)
    src.backup(dst)
    with pytest.raises(sqlite3.IntegrityError, match="read-only"):
        dst.execute(sql)


def test_unsafe_resolution_is_not_mirrored(tmp_path, monkeypatch):
    real = loader.load_resolutions

    def with_unsafe(tenant_id):
        rows = real(tenant_id)
        if rows:
            rows.append({**rows[0], "case_id": "PREC-FFFFFFFFFF",
                         "redacted_summary": "Customer Mr. Ahmed called from 01012345678 about a refund."})
        return rows

    monkeypatch.setattr(loader, "load_resolutions", with_unsafe)
    report = loader.build(["shop_001"], embedder=None, db_path=tmp_path / "gate.sqlite")
    r = report["tenants"]["shop_001"]
    assert r["rows"]["resolutions_mirror"] == 7
    assert any("PREC-FFFFFFFFFF" in w and "personal_data" in w for w in r["warnings"])


def test_unmapped_source_field_fails_loudly(tmp_path, monkeypatch):
    import dataclasses

    from team_a import config

    folder = tmp_path / "mock" / "noon_eg"
    folder.mkdir(parents=True)
    for f in config.settings.mock_dir("noon_eg").glob("noon_*.json"):
        (folder / f.name).write_bytes(f.read_bytes())
    orders = json.loads((folder / "noon_orders.json").read_text(encoding="utf-8"))
    orders["orders"][0]["loyalty_points"] = 5
    (folder / "noon_orders.json").write_text(json.dumps(orders), encoding="utf-8")
    patched = dataclasses.replace(config.settings, data_dir=tmp_path)
    monkeypatch.setattr(sources, "settings", patched)
    with pytest.raises(ValueError, match="loyalty_points"):
        sources.read_mock("noon_eg")


def test_failed_build_leaves_previous_data_untouched(tmp_path, monkeypatch):
    path = tmp_path / "atomic.sqlite"
    loader.build(["noon_eg"], embedder=None, db_path=path)
    before = logical_dump(path)
    monkeypatch.setattr(loader, "_insert", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError, match="boom"):
        loader.build(["noon_eg"], embedder=None, db_path=path)
    assert logical_dump(path) == before


def test_schema_version_mismatch_asks_for_reset(tmp_path):
    path = tmp_path / "old.sqlite"
    loader.build(["noon_eg"], embedder=None, db_path=path)
    conn = sqlite3.connect(path)
    conn.execute("UPDATE db_meta SET value = '0' WHERE key = 'schema_version'")
    conn.commit()
    conn.close()
    with pytest.raises(RuntimeError, match="--reset"):
        loader.build(["noon_eg"], embedder=None, db_path=path)


def test_unknown_tenant_is_rejected(tmp_path):
    from team_a.knowledge.index import TenantNotFound

    with pytest.raises(TenantNotFound):
        loader.build(["nobody"], embedder=None, db_path=tmp_path / "x.sqlite")
