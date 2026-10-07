# Append-only: never edit or reorder a released migration; databases record each version once.
# schema_version (rows 1, 2) predates schema_migrations and is kept unchanged as a historical marker.
BASELINE = """
CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY);
INSERT OR IGNORE INTO schema_version VALUES(1);
CREATE TABLE IF NOT EXISTS businesses(id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS specifications(id TEXT PRIMARY KEY, business_id TEXT NOT NULL REFERENCES businesses(id), filename TEXT NOT NULL, checksum TEXT NOT NULL, raw BLOB NOT NULL, document TEXT, inventory TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, business_id TEXT NOT NULL REFERENCES businesses(id), kind TEXT NOT NULL, input_hash TEXT NOT NULL, status TEXT NOT NULL, result TEXT, error TEXT, created_at TEXT NOT NULL, completed_at TEXT);
CREATE TABLE IF NOT EXISTS attempts(id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(id), provider TEXT NOT NULL, model TEXT NOT NULL, status TEXT NOT NULL, error TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS proposals(id TEXT PRIMARY KEY, business_id TEXT NOT NULL REFERENCES businesses(id), current_version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS versions(proposal_id TEXT NOT NULL REFERENCES proposals(id), version INTEGER NOT NULL, spec_id TEXT NOT NULL REFERENCES specifications(id), content TEXT NOT NULL, derived TEXT NOT NULL, state TEXT NOT NULL, review_revision INTEGER NOT NULL DEFAULT 0, run_id TEXT NOT NULL REFERENCES runs(id), reconciliation_id TEXT, created_at TEXT NOT NULL, PRIMARY KEY(proposal_id,version));
CREATE TABLE IF NOT EXISTS answers(id INTEGER PRIMARY KEY AUTOINCREMENT, proposal_id TEXT NOT NULL, version INTEGER NOT NULL, question_id TEXT NOT NULL, text TEXT NOT NULL, reviewer TEXT NOT NULL, created_at TEXT NOT NULL, FOREIGN KEY(proposal_id,version) REFERENCES versions(proposal_id,version));
CREATE TABLE IF NOT EXISTS reconciliations(id TEXT PRIMARY KEY, proposal_id TEXT NOT NULL, version INTEGER NOT NULL, snapshot_hash TEXT NOT NULL, result TEXT NOT NULL, successful INTEGER NOT NULL, run_id TEXT NOT NULL REFERENCES runs(id), created_at TEXT NOT NULL, FOREIGN KEY(proposal_id,version) REFERENCES versions(proposal_id,version));
CREATE TABLE IF NOT EXISTS decisions(id TEXT PRIMARY KEY, proposal_id TEXT NOT NULL, version INTEGER NOT NULL, action TEXT NOT NULL, reason TEXT NOT NULL, reviewer TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE, request_hash TEXT NOT NULL, snapshot TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(proposal_id,version), FOREIGN KEY(proposal_id,version) REFERENCES versions(proposal_id,version));
CREATE TABLE IF NOT EXISTS revision_requests(id TEXT PRIMARY KEY, proposal_id TEXT NOT NULL, version INTEGER NOT NULL, instruction TEXT NOT NULL, reviewer TEXT NOT NULL, run_id TEXT NOT NULL REFERENCES runs(id), created_at TEXT NOT NULL, FOREIGN KEY(proposal_id,version) REFERENCES versions(proposal_id,version));
CREATE TABLE IF NOT EXISTS diagnostics(id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(id), stage TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS discovery_cache(cache_key TEXT PRIMARY KEY, spec_id TEXT NOT NULL REFERENCES specifications(id));
INSERT OR IGNORE INTO schema_version VALUES(2);
CREATE TABLE IF NOT EXISTS requirements(id TEXT NOT NULL, proposal_id TEXT NOT NULL REFERENCES proposals(id), kind TEXT NOT NULL, text TEXT NOT NULL, operations TEXT NOT NULL, source_facts TEXT NOT NULL, created_version INTEGER NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(proposal_id,id));
CREATE TABLE IF NOT EXISTS requirement_scope(proposal_id TEXT NOT NULL, version INTEGER NOT NULL, requirement_id TEXT NOT NULL, PRIMARY KEY(proposal_id,version,requirement_id), FOREIGN KEY(proposal_id,version) REFERENCES versions(proposal_id,version), FOREIGN KEY(proposal_id,requirement_id) REFERENCES requirements(proposal_id,id));
CREATE TABLE IF NOT EXISTS requirement_events(id INTEGER PRIMARY KEY AUTOINCREMENT, proposal_id TEXT NOT NULL, version INTEGER NOT NULL, requirement_id TEXT NOT NULL, status TEXT NOT NULL, basis TEXT, answer_id INTEGER REFERENCES answers(id), reconciliation_id TEXT, note TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL, FOREIGN KEY(proposal_id,version) REFERENCES versions(proposal_id,version));
CREATE TABLE IF NOT EXISTS artifacts(id TEXT PRIMARY KEY, proposal_id TEXT NOT NULL, version INTEGER NOT NULL, decision_id TEXT NOT NULL REFERENCES decisions(id), sha256 TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(proposal_id,version,sha256), FOREIGN KEY(proposal_id,version) REFERENCES versions(proposal_id,version));
CREATE TABLE IF NOT EXISTS executions(id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL REFERENCES artifacts(id), mode TEXT NOT NULL, identity TEXT NOT NULL, status TEXT NOT NULL, report TEXT, created_at TEXT NOT NULL, completed_at TEXT);
CREATE TABLE IF NOT EXISTS enforcement_configs(id TEXT PRIMARY KEY, proposal_id TEXT NOT NULL, version INTEGER NOT NULL, connector_id TEXT NOT NULL, content TEXT NOT NULL, sha256 TEXT NOT NULL, submitted_by TEXT NOT NULL, submitted_at TEXT NOT NULL, reviewed_by TEXT, reviewed_at TEXT, review_note TEXT, FOREIGN KEY(proposal_id,version) REFERENCES versions(proposal_id,version));
CREATE TABLE IF NOT EXISTS sandbox_tests(id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL REFERENCES artifacts(id), execution_id TEXT NOT NULL REFERENCES executions(id), name TEXT NOT NULL, expectation TEXT NOT NULL, verdict TEXT NOT NULL, failure_report TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS repairs(id TEXT PRIMARY KEY, proposal_id TEXT NOT NULL REFERENCES proposals(id), test_id TEXT NOT NULL REFERENCES sandbox_tests(id), case_name TEXT NOT NULL, attempt INTEGER NOT NULL, from_version INTEGER NOT NULL, outcome TEXT NOT NULL, detail TEXT NOT NULL, verification TEXT, run_id TEXT REFERENCES runs(id), new_version INTEGER, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS publications(id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL REFERENCES artifacts(id), proposal_id TEXT NOT NULL, version INTEGER NOT NULL, business_id TEXT NOT NULL, artifact_sha256 TEXT NOT NULL, decision_id TEXT NOT NULL REFERENCES decisions(id), enforcement_config_id TEXT, enforcement_sha256 TEXT, evidence TEXT NOT NULL, environment TEXT NOT NULL, tool_name TEXT NOT NULL, note TEXT NOT NULL, publisher TEXT NOT NULL, published_at TEXT NOT NULL, status TEXT NOT NULL, disabled_by TEXT, disabled_at TEXT, disable_note TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS publications_active_tool ON publications(tool_name) WHERE status='published';
CREATE TABLE IF NOT EXISTS tool_requests(id TEXT PRIMARY KEY, business_id TEXT NOT NULL REFERENCES businesses(id), spec_id TEXT NOT NULL REFERENCES specifications(id), source TEXT NOT NULL, suggestion_id TEXT, goal TEXT NOT NULL, examples TEXT NOT NULL, clarifications TEXT NOT NULL, operation_scope TEXT, idempotency_key TEXT NOT NULL UNIQUE, request_hash TEXT NOT NULL, status TEXT NOT NULL, run_ids TEXT NOT NULL, proposal_ids TEXT NOT NULL, existing_proposal_ids TEXT NOT NULL, unresolved TEXT NOT NULL, coverage TEXT, outcome TEXT, error TEXT, requester TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS suggestion_batches(id TEXT PRIMARY KEY, business_id TEXT NOT NULL REFERENCES businesses(id), spec_id TEXT NOT NULL REFERENCES specifications(id), run_id TEXT REFERENCES runs(id), requested INTEGER NOT NULL, coverage TEXT NOT NULL, withheld TEXT NOT NULL, recognized TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS suggestions(id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES suggestion_batches(id), business_id TEXT NOT NULL REFERENCES businesses(id), spec_id TEXT NOT NULL, parent_id TEXT, content TEXT NOT NULL, category TEXT NOT NULL, status TEXT NOT NULL, request_id TEXT, decided_by TEXT, decided_at TEXT, decision_note TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS spec_areas(spec_id TEXT PRIMARY KEY REFERENCES specifications(id), content TEXT NOT NULL, source TEXT NOT NULL, run_id TEXT REFERENCES runs(id), selected TEXT, selected_by TEXT, selected_at TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS generation_batches(run_id TEXT PRIMARY KEY REFERENCES runs(id), spec_id TEXT NOT NULL REFERENCES specifications(id), operation_ids TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS question_supersessions(id TEXT PRIMARY KEY, proposal_id TEXT NOT NULL, version INTEGER NOT NULL, question_id TEXT NOT NULL, question TEXT NOT NULL, reason TEXT NOT NULL, reviewer TEXT NOT NULL, new_version INTEGER NOT NULL, created_at TEXT NOT NULL, FOREIGN KEY(proposal_id,version) REFERENCES versions(proposal_id,version))
"""


def script(sql):
    def apply(conn):
        for statement in sql.split(";\n"):
            if statement.strip():
                conn.execute(statement)
    return apply


def columns(*pairs):
    def apply(conn):
        for table, column in pairs:
            if column not in {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")
    return apply


MIGRATIONS = (
    (1, "baseline", script(BASELINE)),
    (2, "run_and_execution_owner", columns(("runs", "owner"), ("executions", "owner"))),
    (3, "sandbox_test_scenario_owner", columns(("sandbox_tests", "scenario"), ("sandbox_tests", "record_owner"))),
    (4, "sandbox_test_selector_sha256", columns(("sandbox_tests", "selector_sha256"))),
    (5, "gap_dismissals", script("CREATE TABLE IF NOT EXISTS gap_dismissals(id TEXT PRIMARY KEY, reconciliation_id TEXT NOT NULL REFERENCES reconciliations(id), gap_index INTEGER NOT NULL, requested_capability TEXT NOT NULL, reason TEXT NOT NULL, reviewer TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(reconciliation_id,gap_index))")),
    (6, "capability_policies", script("""CREATE TABLE capability_policies(sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE, proposal_id TEXT NOT NULL, version INTEGER NOT NULL, content TEXT NOT NULL, sha256 TEXT NOT NULL, accepted_by TEXT NOT NULL, created_at TEXT NOT NULL, FOREIGN KEY(proposal_id,version) REFERENCES versions(proposal_id,version));
CREATE INDEX capability_policy_version ON capability_policies(proposal_id,version,sequence);
CREATE TABLE policy_test_runs(id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL REFERENCES artifacts(id), artifact_sha256 TEXT NOT NULL, report TEXT NOT NULL, created_at TEXT NOT NULL)""")),
    (7, "sandbox_connectors", script("CREATE TABLE sandbox_connectors(id TEXT PRIMARY KEY, business_id TEXT NOT NULL REFERENCES businesses(id), content TEXT NOT NULL, created_at TEXT NOT NULL)")),
)
