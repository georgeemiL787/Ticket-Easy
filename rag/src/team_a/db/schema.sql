-- Team A unified data layer (SQLite). Built by: python -m team_a db build
--
-- Every tenant-owned table has tenant_id in its primary key, and every foreign key includes
-- tenant_id, so a row can only ever reference rows of its own tenant.
-- Dates are ISO-8601 text (YYYY-MM-DD). Money is in the tenant's currency (tenants.currency).
-- rules_mirror and resolutions_mirror are READ-ONLY copies for joins/reporting: the JSON/JSONL
-- files stay the source of truth, and check_action never reads this database.

PRAGMA foreign_keys = ON;

-- ------------------------------------------------------------------ build metadata

CREATE TABLE db_meta (
    key   TEXT PRIMARY KEY,          -- schema_version, embed_model
    value TEXT NOT NULL
);

CREATE TABLE tenants (
    tenant_id     TEXT PRIMARY KEY,
    category      TEXT NOT NULL CHECK (category IN ('e-commerce', 'telecom')),
    name          TEXT NOT NULL,
    currency      TEXT NOT NULL DEFAULT 'EGP',
    as_of         TEXT,              -- fixed "today" for mock data; NULL = use the caller's date
    fictional     INTEGER NOT NULL CHECK (fictional IN (0, 1)),
    retrieval_mode TEXT NOT NULL CHECK (retrieval_mode IN ('hybrid', 'keyword_only'))
);

CREATE TABLE db_sources (
    tenant_id  TEXT NOT NULL REFERENCES tenants(tenant_id),
    path       TEXT NOT NULL,        -- relative to rag/, forward slashes
    sha256     TEXT NOT NULL,        -- of the file with CRLF normalized to LF
    row_count  INTEGER NOT NULL,
    PRIMARY KEY (tenant_id, path)
);

-- ------------------------------------------------------------------ structured backend data

CREATE TABLE customers (
    tenant_id          TEXT NOT NULL REFERENCES tenants(tenant_id),
    customer_id        TEXT NOT NULL,
    name               TEXT NOT NULL,
    name_ar            TEXT,
    phone              TEXT,
    email              TEXT,
    city               TEXT,
    preferred_language TEXT,
    verified           INTEGER CHECK (verified IN (0, 1)),   -- NULL when the source has no flag
    verified_via       TEXT,
    wallet_balance     REAL,
    member_since       TEXT,
    PRIMARY KEY (tenant_id, customer_id)
);

CREATE TABLE orders (
    tenant_id              TEXT NOT NULL,
    order_id               TEXT NOT NULL,
    customer_id            TEXT NOT NULL,
    order_section          TEXT,     -- noon_eg: 'noon' | 'minutes'
    order_status           TEXT NOT NULL,
    payment_method         TEXT,     -- tenant's own values, not normalized across tenants
    payment_status         TEXT,
    placed_at              TEXT,
    shipped_at             TEXT,
    expected_delivery_date TEXT,
    delivered_at           TEXT,
    cancelled_at           TEXT,
    returned_at            TEXT,
    shipping_fee           REAL NOT NULL DEFAULT 0,
    cod_fee                REAL NOT NULL DEFAULT 0,
    order_total            REAL NOT NULL CHECK (order_total >= 0),
    tracking_number        TEXT,
    delivery_address       TEXT,     -- JSON object, fictional
    PRIMARY KEY (tenant_id, order_id),
    FOREIGN KEY (tenant_id, customer_id) REFERENCES customers(tenant_id, customer_id)
);
CREATE INDEX orders_by_customer ON orders(tenant_id, customer_id, placed_at);
CREATE INDEX orders_by_status   ON orders(tenant_id, order_status);

CREATE TABLE order_items (
    tenant_id           TEXT NOT NULL,
    item_id             TEXT NOT NULL,
    order_id            TEXT NOT NULL,
    line_no             INTEGER NOT NULL,
    sku                 TEXT,
    name                TEXT NOT NULL,
    product_category    TEXT NOT NULL,
    product_subcategory TEXT,
    size                TEXT,
    qty                 INTEGER NOT NULL CHECK (qty > 0),
    unit_price          REAL NOT NULL CHECK (unit_price >= 0),
    item_condition      TEXT,
    is_clearance        INTEGER CHECK (is_clearance IN (0, 1)),
    return_eligible_tag INTEGER CHECK (return_eligible_tag IN (0, 1)),
    warranty_tag        INTEGER CHECK (warranty_tag IN (0, 1)),
    PRIMARY KEY (tenant_id, item_id),
    UNIQUE (tenant_id, order_id, line_no),
    FOREIGN KEY (tenant_id, order_id) REFERENCES orders(tenant_id, order_id)
);

CREATE TABLE returns (
    tenant_id                TEXT NOT NULL,
    return_id                TEXT NOT NULL,
    order_id                 TEXT NOT NULL,
    customer_id              TEXT NOT NULL,
    payment_method           TEXT,
    return_status            TEXT NOT NULL,
    requested_at             TEXT,
    received_at              TEXT,
    qc_result                TEXT,
    refund_amount            REAL,
    refund_destination       TEXT,
    refund_initiated_at      TEXT,
    refund_completed_at      TEXT,
    delivery_attempts_failed INTEGER,
    hub_hold_started_at      TEXT,
    hub_hold_business_days   INTEGER,
    notes                    TEXT,
    PRIMARY KEY (tenant_id, return_id),
    FOREIGN KEY (tenant_id, order_id)    REFERENCES orders(tenant_id, order_id),
    FOREIGN KEY (tenant_id, customer_id) REFERENCES customers(tenant_id, customer_id)
);
CREATE INDEX returns_by_order    ON returns(tenant_id, order_id);
CREATE INDEX returns_by_customer ON returns(tenant_id, customer_id);

-- ------------------------------------------------------------------ knowledge (RAG)

CREATE TABLE policy_documents (
    tenant_id      TEXT NOT NULL REFERENCES tenants(tenant_id),
    document_id    TEXT NOT NULL,
    version        TEXT NOT NULL,
    file           TEXT NOT NULL,
    effective_date TEXT,
    current        INTEGER NOT NULL CHECK (current IN (0, 1)),
    sha256         TEXT NOT NULL,
    PRIMARY KEY (tenant_id, document_id, version)
);

-- Rows come from team_a.knowledge.index.build_passages (same parsers, same citations).
CREATE TABLE policy_passages (
    tenant_id      TEXT NOT NULL,
    passage_id     TEXT NOT NULL,    -- == citation, e.g. return_policy@v2#s2
    document_id    TEXT NOT NULL,
    version        TEXT NOT NULL,
    ordinal        INTEGER NOT NULL, -- order within the tenant's index (keeps BM25/ranking ties identical)
    section        TEXT NOT NULL,
    language       TEXT NOT NULL CHECK (language IN ('ar', 'en', 'mixed')),
    current        INTEGER NOT NULL CHECK (current IN (0, 1)),
    effective_date TEXT,
    text           TEXT NOT NULL,
    text_norm      TEXT NOT NULL,    -- team_a.text.normalize(section + "\n" + text)
    tokens         TEXT NOT NULL,    -- team_a.text.tokenize(...) joined by spaces; what FTS5 indexes
    PRIMARY KEY (tenant_id, passage_id),
    UNIQUE (tenant_id, ordinal),
    FOREIGN KEY (tenant_id, document_id, version) REFERENCES policy_documents(tenant_id, document_id, version)
);
CREATE INDEX passages_by_document ON policy_passages(tenant_id, document_id, version);

-- Keyword index over the already-normalized tokens. Used for ad-hoc lookups and `db query`;
-- ranking in search_knowledge still uses the existing BM25 + cosine fusion so MIN_BM25 keeps its meaning.
CREATE VIRTUAL TABLE passages_fts USING fts5(
    tokens, tenant_id UNINDEXED, passage_id UNINDEXED,
    content='policy_passages', content_rowid='rowid', tokenize='unicode61 remove_diacritics 0'
);

CREATE TABLE passage_embeddings (
    tenant_id  TEXT NOT NULL,
    passage_id TEXT NOT NULL,
    model      TEXT NOT NULL,
    dim        INTEGER NOT NULL,
    text_sha   TEXT NOT NULL,        -- sha256 of the embedded text; a mismatch means the vector is stale
    vector     BLOB NOT NULL,        -- float32 little-endian, unit length, dim * 4 bytes
    PRIMARY KEY (tenant_id, passage_id, model),
    FOREIGN KEY (tenant_id, passage_id) REFERENCES policy_passages(tenant_id, passage_id),
    CHECK (length(vector) = dim * 4)
);

CREATE TABLE past_tickets (
    tenant_id        TEXT NOT NULL REFERENCES tenants(tenant_id),
    ticket_id        TEXT NOT NULL,
    ordinal          INTEGER NOT NULL,
    category         TEXT NOT NULL,
    customer_message TEXT NOT NULL,
    resolution       TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    tokens           TEXT NOT NULL,
    PRIMARY KEY (tenant_id, ticket_id),
    UNIQUE (tenant_id, ordinal)
);
CREATE INDEX tickets_by_category ON past_tickets(tenant_id, category);

CREATE TABLE ticket_embeddings (
    tenant_id TEXT NOT NULL,
    ticket_id TEXT NOT NULL,
    model     TEXT NOT NULL,
    dim       INTEGER NOT NULL,
    text_sha  TEXT NOT NULL,
    vector    BLOB NOT NULL,
    PRIMARY KEY (tenant_id, ticket_id, model),
    FOREIGN KEY (tenant_id, ticket_id) REFERENCES past_tickets(tenant_id, ticket_id),
    CHECK (length(vector) = dim * 4)
);

-- ------------------------------------------------------------------ read-only mirrors

CREATE TABLE rules_mirror (
    tenant_id       TEXT NOT NULL REFERENCES tenants(tenant_id),
    rule_id         TEXT NOT NULL,
    action          TEXT NOT NULL,
    approval_status TEXT NOT NULL,
    effect          TEXT NOT NULL,
    else_effect     TEXT,
    effective_date  TEXT NOT NULL,
    approved_by     TEXT,
    approved_at     TEXT,
    source_citation TEXT NOT NULL,   -- joins to policy_passages.passage_id (not an FK: reporting only)
    rule_json       TEXT NOT NULL,   -- the rule exactly as in data/rules/<tenant>.json
    PRIMARY KEY (tenant_id, rule_id)
);
CREATE INDEX rules_by_action ON rules_mirror(tenant_id, action, approval_status);

CREATE TABLE resolutions_mirror (
    tenant_id         TEXT NOT NULL REFERENCES tenants(tenant_id),
    case_id           TEXT NOT NULL,
    category          TEXT NOT NULL,
    redacted_summary  TEXT NOT NULL,
    resolution        TEXT NOT NULL,
    cited_rule_id     TEXT,
    tags              TEXT NOT NULL, -- JSON array
    escalation_reason TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    PRIMARY KEY (tenant_id, case_id)
);

-- Mirrors reject writes. The loader drops these triggers, reloads the tenant's rows, and recreates
-- them in the same transaction.
CREATE TRIGGER rules_mirror_no_insert BEFORE INSERT ON rules_mirror BEGIN SELECT RAISE(ABORT, 'rules_mirror is read-only'); END;
CREATE TRIGGER rules_mirror_no_update BEFORE UPDATE ON rules_mirror BEGIN SELECT RAISE(ABORT, 'rules_mirror is read-only'); END;
CREATE TRIGGER rules_mirror_no_delete BEFORE DELETE ON rules_mirror BEGIN SELECT RAISE(ABORT, 'rules_mirror is read-only'); END;
CREATE TRIGGER resolutions_mirror_no_insert BEFORE INSERT ON resolutions_mirror BEGIN SELECT RAISE(ABORT, 'resolutions_mirror is read-only'); END;
CREATE TRIGGER resolutions_mirror_no_update BEFORE UPDATE ON resolutions_mirror BEGIN SELECT RAISE(ABORT, 'resolutions_mirror is read-only'); END;
CREATE TRIGGER resolutions_mirror_no_delete BEFORE DELETE ON resolutions_mirror BEGIN SELECT RAISE(ABORT, 'resolutions_mirror is read-only'); END;

-- ------------------------------------------------------------------ evaluation data

CREATE TABLE eval_scenarios (
    tenant_id             TEXT NOT NULL REFERENCES tenants(tenant_id),
    scenario_id           TEXT NOT NULL,
    type                  TEXT NOT NULL,   -- create_return | refund_status | policy_question | cancel_order
    language              TEXT NOT NULL,
    customer_id           TEXT,
    order_id              TEXT,
    item_id               TEXT,
    return_id             TEXT,
    customer_message      TEXT NOT NULL,
    tool                  TEXT,
    customer_verified     INTEGER CHECK (customer_verified IN (0, 1)),
    facts                 TEXT,            -- JSON, exactly as in check_action_input.facts
    arguments             TEXT,            -- JSON
    expected_decision     TEXT NOT NULL,   -- scenario labels, NOT check_action reason codes
    expected_reason_code  TEXT,
    answer_points         TEXT,            -- JSON array
    days_since_delivery   INTEGER,
    test_purpose          TEXT,
    PRIMARY KEY (tenant_id, scenario_id),
    FOREIGN KEY (tenant_id, customer_id) REFERENCES customers(tenant_id, customer_id),
    FOREIGN KEY (tenant_id, order_id)    REFERENCES orders(tenant_id, order_id),
    FOREIGN KEY (tenant_id, item_id)     REFERENCES order_items(tenant_id, item_id),
    FOREIGN KEY (tenant_id, return_id)   REFERENCES returns(tenant_id, return_id)
);
CREATE INDEX scenarios_by_type ON eval_scenarios(tenant_id, type);

-- One row per expected policy section; passage_id is resolved by exact section-title match.
CREATE TABLE eval_scenario_sections (
    tenant_id     TEXT NOT NULL,
    scenario_id   TEXT NOT NULL,
    section_title TEXT NOT NULL,
    passage_id    TEXT NOT NULL,
    PRIMARY KEY (tenant_id, scenario_id, section_title),
    FOREIGN KEY (tenant_id, scenario_id) REFERENCES eval_scenarios(tenant_id, scenario_id),
    FOREIGN KEY (tenant_id, passage_id)  REFERENCES policy_passages(tenant_id, passage_id)
);

CREATE TABLE benchmark_questions (
    tenant_id   TEXT NOT NULL REFERENCES tenants(tenant_id),
    suite       TEXT NOT NULL CHECK (suite IN ('retrieval', 'guardrail')),
    question_id TEXT NOT NULL,
    split       TEXT CHECK (split IN ('dev', 'test')),  -- retrieval only
    style       TEXT,                                   -- retrieval: ar | en | arabizi
    question    TEXT,                                   -- retrieval question / guardrail title
    expected    TEXT NOT NULL,                          -- JSON: citations[] or the guardrail `expect`
    request     TEXT,                                   -- guardrail: {"message", "request"} JSON
    as_of       TEXT,
    PRIMARY KEY (tenant_id, suite, question_id)
);

-- ------------------------------------------------------------------ telecom backend data (category 'telecom')
-- Copied from the tenant's telecom.db (data/telecom/<tenant>/, regenerated by its scripts/generate.py).
-- Customers go into the shared `customers` table; telecom-only customer fields are in tel_customer_profiles.
-- Not copied on purpose: full national ID, birth date, gender, home address, and SIM secrets (PUK, IMSI,
-- ICCID). The identity check only needs nid_last4, and giving out a PUK is a backend action, not a read.

CREATE TABLE tel_plans (
    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id), plan_id TEXT NOT NULL,
    plan_type TEXT NOT NULL, name_en TEXT, name_ar TEXT, monthly_fee REAL, data_mb INTEGER, minutes INTEGER,
    sms INTEGER, speed_mbps INTEGER, credit_limit REAL, min_term_months INTEGER, renew_code TEXT,
    PRIMARY KEY (tenant_id, plan_id)
);
CREATE TABLE tel_bundles (
    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id), bundle_id TEXT NOT NULL,
    kind TEXT, name_en TEXT, name_ar TEXT, price REAL, validity_days INTEGER, data_mb INTEGER, minutes INTEGER,
    roaming_zone TEXT, ussd_code TEXT,
    PRIMARY KEY (tenant_id, bundle_id)
);
CREATE TABLE tel_vas_services (
    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id), vas_id TEXT NOT NULL,
    name_en TEXT, name_ar TEXT, daily_fee REAL,
    PRIMARY KEY (tenant_id, vas_id)
);
CREATE TABLE tel_devices (
    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id), device_id TEXT NOT NULL, name TEXT, price REAL,
    PRIMARY KEY (tenant_id, device_id)
);
CREATE TABLE tel_stores (
    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id), store_id TEXT NOT NULL,
    name TEXT, governorate TEXT, city TEXT,
    PRIMARY KEY (tenant_id, store_id)
);
CREATE TABLE tel_customer_profiles (
    tenant_id TEXT NOT NULL, customer_id TEXT NOT NULL,
    nid_last4 TEXT, id_type TEXT, governorate TEXT, segment TEXT, kyc_status TEXT, status TEXT,
    marketing_opt_out INTEGER, is_demo INTEGER NOT NULL DEFAULT 0, demo_note TEXT,
    PRIMARY KEY (tenant_id, customer_id),
    FOREIGN KEY (tenant_id, customer_id) REFERENCES customers(tenant_id, customer_id)
);
CREATE TABLE tel_accounts (
    tenant_id TEXT NOT NULL, account_id TEXT NOT NULL, customer_id TEXT NOT NULL,
    account_type TEXT NOT NULL, bill_day INTEGER, credit_limit REAL, outstanding_balance REAL, status TEXT,
    opened_at TEXT, payment_method TEXT, contract_end TEXT,
    PRIMARY KEY (tenant_id, account_id),
    FOREIGN KEY (tenant_id, customer_id) REFERENCES customers(tenant_id, customer_id)
);
CREATE INDEX tel_accounts_by_customer ON tel_accounts(tenant_id, customer_id);
CREATE TABLE tel_lines (
    tenant_id TEXT NOT NULL, line_id TEXT NOT NULL, account_id TEXT NOT NULL, customer_id TEXT NOT NULL,
    msisdn TEXT, line_type TEXT, plan_id TEXT, status TEXT, status_reason TEXT, activated_at TEXT,
    last_recharge_at TEXT, last_activity_at TEXT, prepaid_balance REAL, sim_type TEXT, network_gen TEXT,
    roaming_enabled INTEGER, ported_in INTEGER, home_landline TEXT, service_address TEXT, exchange_name TEXT,
    salefny_outstanding REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (tenant_id, line_id),
    UNIQUE (tenant_id, msisdn),
    FOREIGN KEY (tenant_id, account_id) REFERENCES tel_accounts(tenant_id, account_id),
    FOREIGN KEY (tenant_id, customer_id) REFERENCES customers(tenant_id, customer_id),
    FOREIGN KEY (tenant_id, plan_id) REFERENCES tel_plans(tenant_id, plan_id)
);
CREATE INDEX tel_lines_by_customer ON tel_lines(tenant_id, customer_id);
CREATE INDEX tel_lines_by_account ON tel_lines(tenant_id, account_id);
CREATE INDEX tel_lines_by_landline ON tel_lines(tenant_id, home_landline);
CREATE TABLE tel_line_bundles (
    tenant_id TEXT NOT NULL, subscription_id TEXT NOT NULL, line_id TEXT NOT NULL, bundle_id TEXT NOT NULL,
    subscribed_at TEXT, expires_at TEXT, auto_renew INTEGER, status TEXT, data_mb_total INTEGER,
    data_mb_used INTEGER, minutes_total INTEGER, minutes_used INTEGER, channel TEXT, price REAL,
    PRIMARY KEY (tenant_id, subscription_id),
    FOREIGN KEY (tenant_id, line_id) REFERENCES tel_lines(tenant_id, line_id),
    FOREIGN KEY (tenant_id, bundle_id) REFERENCES tel_bundles(tenant_id, bundle_id)
);
CREATE INDEX tel_line_bundles_by_line ON tel_line_bundles(tenant_id, line_id, subscribed_at);
CREATE TABLE tel_vas_subscriptions (
    tenant_id TEXT NOT NULL, vas_sub_id TEXT NOT NULL, line_id TEXT NOT NULL, vas_id TEXT NOT NULL,
    subscribed_at TEXT, unsubscribed_at TEXT, channel TEXT, customer_consent TEXT, status TEXT,
    PRIMARY KEY (tenant_id, vas_sub_id),
    FOREIGN KEY (tenant_id, line_id) REFERENCES tel_lines(tenant_id, line_id),
    FOREIGN KEY (tenant_id, vas_id) REFERENCES tel_vas_services(tenant_id, vas_id)
);
CREATE INDEX tel_vas_by_line ON tel_vas_subscriptions(tenant_id, line_id);
CREATE TABLE tel_transactions (
    tenant_id TEXT NOT NULL, txn_id TEXT NOT NULL, line_id TEXT NOT NULL, account_id TEXT, customer_id TEXT,
    txn_type TEXT NOT NULL, created_at TEXT NOT NULL, amount REAL NOT NULL, net_credit REAL,
    currency TEXT NOT NULL DEFAULT 'EGP', channel TEXT, reference TEXT, related_id TEXT, status TEXT,
    description TEXT, counterparty_msisdn TEXT, is_duplicate INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (tenant_id, txn_id),
    FOREIGN KEY (tenant_id, line_id) REFERENCES tel_lines(tenant_id, line_id)
);
CREATE INDEX tel_transactions_by_line ON tel_transactions(tenant_id, line_id, created_at);
CREATE TABLE tel_invoices (
    tenant_id TEXT NOT NULL, invoice_id TEXT NOT NULL, account_id TEXT NOT NULL, customer_id TEXT,
    period_start TEXT, period_end TEXT, issued_at TEXT, due_date TEXT, subtotal REAL, taxes REAL, total REAL,
    amount_paid REAL, status TEXT, paid_at TEXT,
    PRIMARY KEY (tenant_id, invoice_id),
    FOREIGN KEY (tenant_id, account_id) REFERENCES tel_accounts(tenant_id, account_id)
);
CREATE INDEX tel_invoices_by_account ON tel_invoices(tenant_id, account_id, issued_at);
CREATE TABLE tel_invoice_items (
    tenant_id TEXT NOT NULL, item_id TEXT NOT NULL, invoice_id TEXT NOT NULL, line_id TEXT,
    item_type TEXT, description TEXT, amount REAL,
    PRIMARY KEY (tenant_id, item_id),
    FOREIGN KEY (tenant_id, invoice_id) REFERENCES tel_invoices(tenant_id, invoice_id),
    FOREIGN KEY (tenant_id, line_id) REFERENCES tel_lines(tenant_id, line_id)
);
CREATE INDEX tel_invoice_items_by_invoice ON tel_invoice_items(tenant_id, invoice_id);
CREATE TABLE tel_usage_daily (
    tenant_id TEXT NOT NULL, line_id TEXT NOT NULL, usage_date TEXT NOT NULL, data_mb REAL,
    voice_onnet_min REAL, voice_offnet_min REAL, intl_min REAL, sms INTEGER, roaming_data_mb REAL,
    roaming_country TEXT,
    PRIMARY KEY (tenant_id, line_id, usage_date),
    FOREIGN KEY (tenant_id, line_id) REFERENCES tel_lines(tenant_id, line_id)
);
CREATE TABLE tel_device_installments (
    tenant_id TEXT NOT NULL, plan_id TEXT NOT NULL, account_id TEXT NOT NULL, line_id TEXT, device_id TEXT,
    imei TEXT, start_date TEXT, months INTEGER, monthly_amount REAL, paid_installments INTEGER,
    missed_installments INTEGER, device_locked INTEGER, status TEXT,
    PRIMARY KEY (tenant_id, plan_id),
    FOREIGN KEY (tenant_id, account_id) REFERENCES tel_accounts(tenant_id, account_id),
    FOREIGN KEY (tenant_id, device_id) REFERENCES tel_devices(tenant_id, device_id)
);
CREATE TABLE tel_sim_events (
    tenant_id TEXT NOT NULL, event_id TEXT NOT NULL, line_id TEXT NOT NULL, event_type TEXT, created_at TEXT,
    store_id TEXT, channel TEXT, fee REAL, verified_by TEXT, note TEXT,
    PRIMARY KEY (tenant_id, event_id),
    FOREIGN KEY (tenant_id, line_id) REFERENCES tel_lines(tenant_id, line_id),
    FOREIGN KEY (tenant_id, store_id) REFERENCES tel_stores(tenant_id, store_id)
);
CREATE TABLE tel_port_requests (
    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id), port_id TEXT NOT NULL, msisdn TEXT,
    customer_id TEXT, direction TEXT, other_operator TEXT, requested_at TEXT, switch_date TEXT, status TEXT,
    reject_reason TEXT, fee REAL,
    PRIMARY KEY (tenant_id, port_id),
    FOREIGN KEY (tenant_id, customer_id) REFERENCES customers(tenant_id, customer_id)
);
CREATE INDEX tel_port_requests_by_msisdn ON tel_port_requests(tenant_id, msisdn);
CREATE TABLE tel_network_outages (
    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id), outage_id TEXT NOT NULL, governorate TEXT,
    city TEXT, service TEXT, started_at TEXT, ended_at TEXT, status TEXT, cause TEXT,
    compensation_eligible INTEGER,
    PRIMARY KEY (tenant_id, outage_id)
);
CREATE INDEX tel_outages_by_area ON tel_network_outages(tenant_id, governorate, city, status);
CREATE TABLE tel_technician_appointments (
    tenant_id TEXT NOT NULL, appointment_id TEXT NOT NULL, line_id TEXT NOT NULL, reason TEXT,
    scheduled_date TEXT, time_window TEXT, status TEXT, created_at TEXT, fee REAL, notes TEXT,
    PRIMARY KEY (tenant_id, appointment_id),
    FOREIGN KEY (tenant_id, line_id) REFERENCES tel_lines(tenant_id, line_id)
);
CREATE TABLE tel_complaints (
    tenant_id TEXT NOT NULL, complaint_id TEXT NOT NULL, customer_id TEXT NOT NULL, line_id TEXT,
    category TEXT, subcategory TEXT, channel TEXT, created_at TEXT, sla_due TEXT, resolved_at TEXT,
    status TEXT, priority TEXT, escalated_to TEXT, resolution TEXT, refund_amount REAL, ntra_referral INTEGER,
    PRIMARY KEY (tenant_id, complaint_id),
    FOREIGN KEY (tenant_id, customer_id) REFERENCES customers(tenant_id, customer_id),
    FOREIGN KEY (tenant_id, line_id) REFERENCES tel_lines(tenant_id, line_id)
);
CREATE INDEX tel_complaints_by_customer ON tel_complaints(tenant_id, customer_id);
CREATE TABLE tel_refunds (
    tenant_id TEXT NOT NULL, refund_id TEXT NOT NULL, customer_id TEXT NOT NULL, line_id TEXT,
    complaint_id TEXT, reason TEXT, amount REAL, method TEXT, requested_at TEXT, approved_by TEXT, status TEXT,
    processed_at TEXT, related_txn_id TEXT,
    PRIMARY KEY (tenant_id, refund_id),
    FOREIGN KEY (tenant_id, customer_id) REFERENCES customers(tenant_id, customer_id),
    FOREIGN KEY (tenant_id, complaint_id) REFERENCES tel_complaints(tenant_id, complaint_id)
);
CREATE INDEX tel_refunds_by_customer ON tel_refunds(tenant_id, customer_id);
