-- Versioned tenant configuration bundles (domain/bundle.py). A published bundle never changes its content, only its status.
-- The active bundle of a business replaces the config/tenants/<tenant>.json file for that business.

CREATE TABLE tenant_bundles (
    tenant_id      TEXT NOT NULL,
    version        INTEGER NOT NULL,
    status         TEXT NOT NULL CHECK (status IN ('draft', 'published', 'active', 'retired')),
    content_json   TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    created_by     TEXT NOT NULL,
    parent_version INTEGER,
    PRIMARY KEY (tenant_id, version)
);

CREATE TABLE tenant_bundle_active (
    tenant_id    TEXT NOT NULL PRIMARY KEY,
    version      INTEGER NOT NULL,
    activated_at TEXT NOT NULL,
    activated_by TEXT NOT NULL
);
