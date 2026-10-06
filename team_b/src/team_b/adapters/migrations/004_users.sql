-- People who may sign in to the inbox and the dashboard, and the businesses each may see.
-- Passwords are stored only as argon2 hashes. Display names are unique because the inbox records them as the actor.

CREATE TABLE users (
    user_id       TEXT NOT NULL PRIMARY KEY,
    email         TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    display_name  TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('admin', 'manager', 'agent')),
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL
);
CREATE UNIQUE INDEX idx_users_email ON users (lower(email));
CREATE UNIQUE INDEX idx_users_display_name ON users (lower(display_name));

CREATE TABLE user_tenants (
    user_id   TEXT NOT NULL REFERENCES users (user_id) ON DELETE CASCADE,
    tenant_id TEXT NOT NULL,
    PRIMARY KEY (user_id, tenant_id)
);
