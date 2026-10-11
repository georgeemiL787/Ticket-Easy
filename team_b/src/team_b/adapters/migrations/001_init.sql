-- Initial schema: conversation memory, decision log and handoff cases.
-- Primary keys include tenant_id so one business can never read or overwrite another's rows.

CREATE TABLE sessions (
    tenant_id       TEXT    NOT NULL,
    conversation_id TEXT    NOT NULL,
    version         INTEGER NOT NULL,
    state_json      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL,
    PRIMARY KEY (tenant_id, conversation_id)
);

CREATE TABLE traces (
    trace_id          TEXT    NOT NULL,
    tenant_id         TEXT    NOT NULL,
    conversation_id   TEXT    NOT NULL,
    turn_index        INTEGER NOT NULL,
    kind              TEXT    NOT NULL,
    decision          TEXT    NOT NULL,
    escalation_reason TEXT,
    language          TEXT,
    latency_ms        REAL    NOT NULL,
    created_at        TEXT    NOT NULL,
    trace_json        TEXT    NOT NULL,
    PRIMARY KEY (tenant_id, trace_id)
);
CREATE INDEX idx_traces_tenant_created ON traces (tenant_id, created_at);
CREATE INDEX idx_traces_tenant_conversation ON traces (tenant_id, conversation_id);

CREATE TABLE cases (
    case_id         TEXT NOT NULL,
    tenant_id       TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    status          TEXT NOT NULL,
    reason          TEXT NOT NULL,
    priority        TEXT NOT NULL,
    claimed_by      TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    case_json       TEXT NOT NULL,
    PRIMARY KEY (tenant_id, case_id)
);
CREATE INDEX idx_cases_tenant_status ON cases (tenant_id, status);
