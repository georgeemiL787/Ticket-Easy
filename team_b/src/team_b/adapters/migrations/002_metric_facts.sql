-- Summary rows for the dashboard, written together with each trace (one transaction), holding no message text.
-- They outlive the trace: deleting old traces does not change the numbers of past weeks.

CREATE TABLE turn_facts (
    trace_id          TEXT    NOT NULL,
    tenant_id         TEXT    NOT NULL,
    conversation_id   TEXT    NOT NULL,
    created_at        TEXT    NOT NULL,
    decision          TEXT    NOT NULL,
    escalation_reason TEXT,
    language          TEXT,
    intent            TEXT,
    latency_ms        REAL    NOT NULL,
    evidence_empty    INTEGER NOT NULL,
    evidence_count    INTEGER NOT NULL,
    nlu_method        TEXT,
    tool_errors       INTEGER NOT NULL,
    dependency_errors TEXT    NOT NULL,  -- JSON list: the service of every dependency error of the turn
    stage_ms          TEXT    NOT NULL,  -- JSON object: stage -> milliseconds
    PRIMARY KEY (tenant_id, trace_id)
);
-- covers the overview query, so it never has to read the table rows
CREATE INDEX idx_turn_facts_tenant_created ON turn_facts
    (tenant_id, created_at, conversation_id, decision, escalation_reason, latency_ms);
CREATE INDEX idx_turn_facts_conversation ON turn_facts (tenant_id, conversation_id);

CREATE TABLE tool_call_facts (
    trace_id       TEXT NOT NULL,
    tenant_id      TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    tool           TEXT NOT NULL,
    operation_kind TEXT NOT NULL,
    status         TEXT NOT NULL,
    error_code     TEXT,
    latency_ms     REAL NOT NULL
);
CREATE INDEX idx_tool_call_facts_tenant_created ON tool_call_facts (tenant_id, created_at);

CREATE TABLE policy_facts (
    trace_id    TEXT NOT NULL,
    tenant_id   TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    action      TEXT NOT NULL,
    decision    TEXT NOT NULL,
    reason_code TEXT NOT NULL
);
CREATE INDEX idx_policy_facts_tenant_created ON policy_facts (tenant_id, created_at);
