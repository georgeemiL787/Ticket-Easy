"""Build the SQLite database from the files under data/. Idempotent and deterministic.

Each tenant is rebuilt in one transaction: its old rows are deleted and everything is re-inserted in a
fixed order, so building twice gives the same content. Parsing, normalization, ticket and resolution
loading all reuse the existing knowledge/policy code; this module only maps their output to tables.
Embeddings are computed before the transaction opens, and a vector is reused when the embedded text and
model are unchanged. If Ollama is unreachable the tenant is stored keyword-only, as `ingest` does.
"""

import hashlib
import json
import sqlite3
from pathlib import Path

import numpy as np

from team_a.config import ROOT, settings
from team_a.db import SCHEMA_FILE, SCHEMA_VERSION, connect
from team_a.db.sources import MockData, read_mock
from team_a.knowledge.embeddings import Embedder, EmbeddingUnavailable
from team_a.knowledge.index import (
    TenantNotFound,
    build_passages,
    load_manifest,
    load_resolutions,
    load_tickets,
    passage_embed_text,
    ticket_embed_text,
)
from team_a.knowledge.resolutions import is_safe_to_persist
from team_a.policy.rules_store import RuleStore
from team_a.schemas import ResolvedEscalationRequest
from team_a.text import normalize, tokenize

# Children before parents, so deleting a tenant never trips a foreign key.
TENANT_TABLES = (
    "eval_scenario_sections", "eval_scenarios", "benchmark_questions",
    "passage_embeddings", "policy_passages", "policy_documents",
    "ticket_embeddings", "past_tickets", "rules_mirror", "resolutions_mirror",
    "returns", "order_items", "orders", "customers", "db_sources", "tenants",
)
MIRRORS = ("rules_mirror", "resolutions_mirror")
GLOBAL_SOURCES = ("synonyms/arabizi.json", "risk/keywords.json")
_TEXT_SUFFIXES = {".md", ".markdown", ".json", ".jsonl", ".py", ".txt", ".csv"}
_SCENARIO_KEYS = {"scenario_id", "type", "language", "customer_id", "order_id", "return_id", "customer_message",
                  "check_action_input", "expected", "days_since_delivery", "test_purpose"}


def file_sha(path: Path) -> str:
    """sha256 of a file; text files are hashed with CRLF normalized to LF so the hash is checkout-independent."""
    data = path.read_bytes()
    if path.suffix.lower() in _TEXT_SUFFIXES:
        data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def _text_sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _rel(path: Path) -> str:
    return path.resolve().relative_to(ROOT).as_posix()


def _json(value) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False)


def discover_tenants() -> list[str]:
    root = settings.data_dir / "corpus"
    return sorted(p.parent.name for p in root.glob("*/manifest.json"))


def _insert(conn: sqlite3.Connection, table: str, row: dict) -> None:
    cols = list(row)
    conn.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                 [row[c] for c in cols])


# ------------------------------------------------------------------ schema

def _ensure_schema(conn: sqlite3.Connection) -> None:
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='db_meta'").fetchone()
    if not exists:
        conn.executescript(SCHEMA_FILE.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO db_meta (key, value) VALUES ('schema_version', ?)", (SCHEMA_VERSION,))
        return
    row = conn.execute("SELECT value FROM db_meta WHERE key='schema_version'").fetchone()
    if row is None or row[0] != SCHEMA_VERSION:
        raise RuntimeError(f"Database schema version {row[0] if row else None} != {SCHEMA_VERSION}; "
                           "rebuild with: python -m team_a db build --reset")


# ------------------------------------------------------------------ embeddings

def _embed(conn, table: str, key: str, tenant_id: str, embedder: Embedder,
           items: list[tuple[str, str]]) -> dict[str, tuple[str, bytes, int]]:
    """Vectors for (id, text) pairs, reusing stored ones whose text and model are unchanged."""
    cached = {
        r[0]: (r[1], r[2], r[3]) for r in conn.execute(
            f"SELECT {key}, text_sha, vector, dim FROM {table} WHERE tenant_id = ? AND model = ?",
            (tenant_id, embedder.model))
    }
    out, todo = {}, []
    for item_id, text in items:
        sha = _text_sha(text)
        hit = cached.get(item_id)
        if hit and hit[0] == sha:
            out[item_id] = hit
        else:
            todo.append((item_id, text, sha))
    if todo:
        vectors = embedder.embed([text for _, text, _ in todo])
        for (item_id, _, sha), vec in zip(todo, vectors):
            vec = np.asarray(vec, dtype="<f4")
            out[item_id] = (sha, vec.tobytes(), int(vec.shape[0]))
    return out


# ------------------------------------------------------------------ per-tenant build

def _resolution_rows(tenant_id: str) -> tuple[list[dict], list[str]]:
    """Seed precedents, each re-checked by the same is_safe_to_persist gate that guards live writes."""
    rows, warnings = [], []
    for r in load_resolutions(tenant_id):
        req = ResolvedEscalationRequest(
            request_id=f"db-import-{r['case_id']}", tenant_id=tenant_id, category=r["category"],
            redacted_summary=r["redacted_summary"], resolution=r["resolution"],
            cited_rule_id=r.get("cited_rule_id"), tags=r.get("tags", []),
            escalation_reason=r["escalation_reason"], risk_categories=[],
        )
        verdict = is_safe_to_persist(req)
        if not verdict.safe:
            warnings.append(f"resolution {r['case_id']} not mirrored: {', '.join(verdict.reasons)}")
            continue
        rows.append(r)
    return rows, warnings


def _scenario_rows(tenant_id: str, mock: MockData, passages: list[dict]) -> tuple[list[dict], list[dict]]:
    by_title: dict[str, list[str]] = {}
    for p in passages:
        if p["current"]:
            by_title.setdefault(p["section"], []).append(p["passage_id"])
    scenarios, sections = [], []
    for s in mock.scenarios:
        extra = set(s) - _SCENARIO_KEYS
        if extra:
            raise ValueError(f"Unmapped scenario field(s) {sorted(extra)} in {s['scenario_id']}")
        cai = s.get("check_action_input") or {}
        if cai and cai.get("tenant_id") != tenant_id:
            raise ValueError(f"Scenario {s['scenario_id']} check_action_input is for tenant {cai.get('tenant_id')!r}")
        expected = s["expected"]
        scenarios.append({
            "tenant_id": tenant_id, "scenario_id": s["scenario_id"], "type": s["type"],
            "language": s["language"], "customer_id": s.get("customer_id"), "order_id": s.get("order_id"),
            "item_id": (cai.get("arguments") or {}).get("item_id"), "return_id": s.get("return_id"),
            "customer_message": s["customer_message"], "tool": cai.get("tool"),
            "customer_verified": None if not cai else int(bool(cai.get("customer_verified"))),
            "facts": _json(cai.get("facts")), "arguments": _json(cai.get("arguments")),
            "expected_decision": expected["decision"], "expected_reason_code": expected.get("reason_code"),
            "answer_points": _json(expected.get("answer_points")),
            "days_since_delivery": s.get("days_since_delivery"), "test_purpose": s.get("test_purpose"),
        })
        for title in expected.get("policy_sections", []):
            matches = by_title.get(title, [])
            if len(matches) != 1:
                raise ValueError(f"Scenario {s['scenario_id']}: policy section {title!r} matches "
                                 f"{len(matches)} current passages, expected exactly 1")
            sections.append({"tenant_id": tenant_id, "scenario_id": s["scenario_id"],
                             "section_title": title, "passage_id": matches[0]})
    return scenarios, sections


def _benchmark_rows(tenant_id: str) -> tuple[list[dict], list[tuple[Path, int]]]:
    folder = settings.data_dir / "benchmark"
    rows, files = [], []
    for path in sorted(folder.glob(f"retrieval_{tenant_id}.*.jsonl")):
        split = path.name.removesuffix(".jsonl").rsplit(".", 1)[1]
        cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        files.append((path, len(cases)))
        rows += [{"tenant_id": tenant_id, "suite": "retrieval", "question_id": c["id"], "split": split,
                  "style": c["style"], "question": c["question"], "expected": _json(c["expected"])} for c in cases]
    path = folder / f"guardrails_{tenant_id}.jsonl"
    if path.exists():
        cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        files.append((path, len(cases)))
        rows += [{"tenant_id": tenant_id, "suite": "guardrail", "question_id": c["id"], "question": c["title"],
                  "expected": _json(c["expect"]), "request": _json({"message": c["message"], "request": c["request"]}),
                  "as_of": c["as_of"]} for c in cases]
    return rows, files


def _build_tenant(conn: sqlite3.Connection, tenant_id: str, embedder: Embedder | None) -> dict:
    # ---- read and validate everything first; nothing is written if any source is bad
    manifest = load_manifest(tenant_id)
    passages, _ = build_passages(tenant_id)
    tickets = load_tickets(tenant_id)
    rules_path = settings.rules_file(tenant_id)
    raw_rules = json.loads(rules_path.read_text(encoding="utf-8"))["rules"] if rules_path.exists() else []
    rules = RuleStore(tenant_id).all()  # same validation and tenant check as the live rule store
    resolutions, warnings = _resolution_rows(tenant_id)
    mock = read_mock(tenant_id)
    scenarios, scenario_sections = _scenario_rows(tenant_id, mock, passages)
    benchmarks, benchmark_files = _benchmark_rows(tenant_id)

    mode, pvecs, tvecs = "keyword_only", {}, {}
    if embedder is None:
        warnings.append("built with --no-embeddings: keyword-only retrieval")
    else:
        try:
            pvecs = _embed(conn, "passage_embeddings", "passage_id", tenant_id, embedder,
                           [(p["passage_id"], passage_embed_text(p)) for p in passages])
            tvecs = _embed(conn, "ticket_embeddings", "ticket_id", tenant_id, embedder,
                           [(t["ticket_id"], ticket_embed_text(t)) for t in tickets])
            mode = "hybrid"
        except EmbeddingUnavailable as exc:
            pvecs, tvecs = {}, {}
            warnings.append(f"Ollama unavailable, stored keyword-only (same as ingest --no-embeddings): {exc}")

    corpus = settings.corpus_dir(tenant_id)
    sources: list[tuple[Path, int]] = [(corpus / "manifest.json", len(manifest["documents"]))]
    for doc in manifest["documents"]:
        n = sum(p["document_id"] == doc["document_id"] and p["version"] == doc["version"] for p in passages)
        sources.append((corpus / doc["file"], n))
    sources += [(settings.tickets_file(tenant_id), len(tickets)), (rules_path, len(raw_rules)),
                (settings.resolutions_file(tenant_id), len(resolutions))]
    # A mock file is counted by the rows it is named after (noon_orders.json -> orders); backend.json by orders.
    mock_counts = {"customers": len(mock.customers), "orders": len(mock.orders), "returns": len(mock.returns),
                   "scenarios": len(mock.scenarios)}
    sources += [(f, mock_counts.get(f.stem.rsplit("_", 1)[-1], len(mock.orders))) for f in mock.files]
    sources += benchmark_files

    # ---- write: one transaction per tenant
    triggers = [r[0] for r in conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='trigger' AND tbl_name IN (?, ?) ORDER BY name", MIRRORS)]
    conn.execute("BEGIN")
    try:
        for name in [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name IN (?, ?)", MIRRORS)]:
            conn.execute(f"DROP TRIGGER {name}")
        for table in TENANT_TABLES:
            conn.execute(f"DELETE FROM {table} WHERE tenant_id = ?", (tenant_id,))

        _insert(conn, "tenants", {
            "tenant_id": tenant_id, "name": manifest.get("tenant_name", tenant_id), "currency": mock.currency,
            "as_of": mock.as_of, "fictional": 1, "retrieval_mode": mode,
        })
        for path, n in sources:
            if path.exists():
                _insert(conn, "db_sources", {"tenant_id": tenant_id, "path": _rel(path),
                                             "sha256": file_sha(path), "row_count": n})

        for c in mock.customers:
            _insert(conn, "customers", {"tenant_id": tenant_id, **c})
        for o in mock.orders:
            _insert(conn, "orders", {"tenant_id": tenant_id, **o})
        for i in mock.items:
            _insert(conn, "order_items", {"tenant_id": tenant_id, **i})
        for r in mock.returns:
            _insert(conn, "returns", {"tenant_id": tenant_id, **r})

        for doc in manifest["documents"]:
            _insert(conn, "policy_documents", {
                "tenant_id": tenant_id, "document_id": doc["document_id"], "version": doc["version"],
                "file": doc["file"], "effective_date": doc.get("effective_date"),
                "current": int(bool(doc.get("current"))), "sha256": file_sha(corpus / doc["file"]),
            })
        for ordinal, p in enumerate(passages):
            text = passage_embed_text(p)
            _insert(conn, "policy_passages", {
                "tenant_id": tenant_id, "passage_id": p["passage_id"], "document_id": p["document_id"],
                "version": p["version"], "ordinal": ordinal, "section": p["section"], "language": p["language"],
                "current": int(p["current"]), "effective_date": p["effective_date"], "text": p["text"],
                "text_norm": normalize(text), "tokens": " ".join(tokenize(text)),
            })
            if p["passage_id"] in pvecs:
                sha, blob, dim = pvecs[p["passage_id"]]
                _insert(conn, "passage_embeddings", {"tenant_id": tenant_id, "passage_id": p["passage_id"],
                                                     "model": embedder.model, "dim": dim, "text_sha": sha,
                                                     "vector": blob})
        for ordinal, t in enumerate(tickets):
            _insert(conn, "past_tickets", {
                "tenant_id": tenant_id, "ticket_id": t["ticket_id"], "ordinal": ordinal, "category": t["category"],
                "customer_message": t["customer_message"], "resolution": t["resolution"],
                "created_at": t["created_at"], "tokens": " ".join(tokenize(ticket_embed_text(t))),
            })
            if t["ticket_id"] in tvecs:
                sha, blob, dim = tvecs[t["ticket_id"]]
                _insert(conn, "ticket_embeddings", {"tenant_id": tenant_id, "ticket_id": t["ticket_id"],
                                                    "model": embedder.model, "dim": dim, "text_sha": sha,
                                                    "vector": blob})

        for raw, rule in zip(raw_rules, rules, strict=True):
            _insert(conn, "rules_mirror", {
                "tenant_id": tenant_id, "rule_id": rule.rule_id, "action": rule.action,
                "approval_status": rule.approval_status, "effect": rule.effect, "else_effect": rule.else_effect,
                "effective_date": rule.effective_date.isoformat(), "approved_by": rule.approved_by,
                "approved_at": raw.get("approved_at"), "source_citation": rule.source.citation,
                "rule_json": json.dumps(raw, ensure_ascii=False),
            })
        for r in resolutions:
            _insert(conn, "resolutions_mirror", {
                "tenant_id": tenant_id, "case_id": r["case_id"], "category": r["category"],
                "redacted_summary": r["redacted_summary"], "resolution": r["resolution"],
                "cited_rule_id": r.get("cited_rule_id"), "tags": _json(r.get("tags", [])),
                "escalation_reason": r["escalation_reason"], "created_at": r["created_at"],
            })

        for s in scenarios:
            _insert(conn, "eval_scenarios", s)
        for s in scenario_sections:
            _insert(conn, "eval_scenario_sections", s)
        for b in benchmarks:
            _insert(conn, "benchmark_questions", b)

        conn.execute("INSERT INTO passages_fts(passages_fts) VALUES ('rebuild')")
        for path in GLOBAL_SOURCES:
            conn.execute("INSERT OR REPLACE INTO db_meta (key, value) VALUES (?, ?)",
                         (f"source:data/{path}", file_sha(settings.data_dir / path)))
        for sql in triggers:
            conn.execute(sql)
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise sqlite3.IntegrityError(f"Foreign key violations: {[tuple(v) for v in violations[:5]]}")
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise

    return {"retrieval_mode": mode, "rows": tenant_counts(conn, tenant_id), "warnings": warnings}


# ------------------------------------------------------------------ public API

def tenant_counts(conn: sqlite3.Connection, tenant_id: str) -> dict[str, int]:
    return {t: conn.execute(f"SELECT count(*) FROM {t} WHERE tenant_id = ?", (tenant_id,)).fetchone()[0]
            for t in reversed(TENANT_TABLES) if t not in ("tenants", "db_sources")}


def build(tenant_ids: list[str] | None = None, embedder: Embedder | None = None,
          reset: bool = False, db_path: Path | None = None) -> dict:
    path = Path(db_path or settings.db_path)
    known = discover_tenants()
    tenant_ids = tenant_ids or known
    unknown = [t for t in tenant_ids if t not in known]
    if unknown:
        raise TenantNotFound(f"Unknown tenant(s) {unknown}; known: {known}")
    if reset:
        for suffix in ("", "-journal", "-wal", "-shm"):
            Path(f"{path}{suffix}").unlink(missing_ok=True)
    conn = connect(path, readonly=False)
    try:
        _ensure_schema(conn)
        report = {"db_path": str(path), "schema_version": SCHEMA_VERSION, "tenants": {}}
        for tenant_id in tenant_ids:
            report["tenants"][tenant_id] = _build_tenant(conn, tenant_id, embedder)
        return report
    finally:
        conn.close()


def stats(db_path: Path | None = None) -> dict[str, dict[str, int]]:
    """Rows per table per tenant."""
    conn = connect(db_path, readonly=True)
    try:
        tenants = [r[0] for r in conn.execute("SELECT tenant_id FROM tenants ORDER BY tenant_id")]
        return {t: tenant_counts(conn, t) for t in tenants}
    finally:
        conn.close()


def query(sql: str, db_path: Path | None = None, limit: int = 200) -> tuple[list[str], list[tuple]]:
    """Run one read-only statement. The connection is opened read-only, so writes fail."""
    conn = connect(db_path, readonly=True)
    try:
        cur = conn.execute(sql)
        columns = [d[0] for d in cur.description or []]
        return columns, [tuple(r) for r in cur.fetchmany(limit)]
    finally:
        conn.close()
