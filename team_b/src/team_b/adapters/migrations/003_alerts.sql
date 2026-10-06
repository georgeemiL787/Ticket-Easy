-- Alerts for managers: opened by the alert engine when a rule's condition starts, resolved when it clears.
-- The primary key includes tenant_id so one business can never read or acknowledge another's alerts.

CREATE TABLE alerts (
    alert_id        TEXT NOT NULL,
    tenant_id       TEXT NOT NULL,
    rule            TEXT NOT NULL,
    alert_key       TEXT NOT NULL,   -- which one of a rule's alerts (e.g. the service); '' when the rule has one
    severity        TEXT NOT NULL,
    opened_at       TEXT NOT NULL,
    resolved_at     TEXT,
    acknowledged_by TEXT,
    alert_json      TEXT NOT NULL,
    PRIMARY KEY (tenant_id, alert_id)
);
CREATE INDEX idx_alerts_tenant_open ON alerts (tenant_id, resolved_at, opened_at);
-- At most one open alert per rule and key: the engine opens a condition once.
CREATE UNIQUE INDEX idx_alerts_one_open ON alerts (tenant_id, rule, alert_key) WHERE resolved_at IS NULL;
