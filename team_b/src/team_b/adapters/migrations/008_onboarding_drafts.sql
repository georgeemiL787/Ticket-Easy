-- Where the onboarding wizard stands for each business that is being set up (domain/onboarding.py). Ids and statuses only.

CREATE TABLE onboarding_drafts (
    tenant_id  TEXT NOT NULL PRIMARY KEY,
    step       TEXT NOT NULL,
    data_json  TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL
);
