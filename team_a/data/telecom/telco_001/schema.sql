-- Masr Connect (telco_001) synthetic telecom data. SQLite dialect; plain types so it ports to Postgres.
CREATE TABLE tenant (tenant_id TEXT PRIMARY KEY, brand_en TEXT, brand_ar TEXT, as_of TEXT, currency TEXT, note TEXT);

CREATE TABLE plans (plan_id TEXT PRIMARY KEY, plan_type TEXT NOT NULL, name_en TEXT, name_ar TEXT, monthly_fee REAL,
  data_mb INTEGER, minutes INTEGER, sms INTEGER, speed_mbps INTEGER, credit_limit REAL, min_term_months INTEGER, renew_code TEXT);
CREATE TABLE bundles (bundle_id TEXT PRIMARY KEY, kind TEXT, name_en TEXT, name_ar TEXT, price REAL, validity_days INTEGER,
  data_mb INTEGER, minutes INTEGER, roaming_zone TEXT, ussd_code TEXT);
CREATE TABLE vas_services (vas_id TEXT PRIMARY KEY, name_en TEXT, name_ar TEXT, daily_fee REAL);
CREATE TABLE devices (device_id TEXT PRIMARY KEY, name TEXT, price REAL);
CREATE TABLE stores (store_id TEXT PRIMARY KEY, name TEXT, governorate TEXT, city TEXT);

CREATE TABLE customers (customer_id TEXT PRIMARY KEY, national_id TEXT UNIQUE, nid_last4 TEXT, id_type TEXT,
  first_name_en TEXT, last_name_en TEXT, full_name_ar TEXT, gender TEXT, birth_date TEXT, governorate TEXT, city TEXT,
  address TEXT, email TEXT, contact_msisdn TEXT, preferred_language TEXT, segment TEXT, kyc_status TEXT,
  customer_since TEXT, status TEXT, marketing_opt_out INTEGER, is_demo INTEGER DEFAULT 0, demo_note TEXT);

CREATE TABLE accounts (account_id TEXT PRIMARY KEY, customer_id TEXT NOT NULL REFERENCES customers(customer_id),
  account_type TEXT NOT NULL, bill_day INTEGER, credit_limit REAL, outstanding_balance REAL, status TEXT,
  opened_at TEXT, payment_method TEXT, contract_end TEXT);

CREATE TABLE lines (line_id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts(account_id),
  customer_id TEXT NOT NULL REFERENCES customers(customer_id), msisdn TEXT UNIQUE, line_type TEXT, plan_id TEXT REFERENCES plans(plan_id),
  status TEXT, status_reason TEXT, activated_at TEXT, last_recharge_at TEXT, last_activity_at TEXT,
  prepaid_balance REAL, iccid TEXT, imsi TEXT, puk TEXT, sim_type TEXT, network_gen TEXT, roaming_enabled INTEGER,
  ported_in INTEGER, home_landline TEXT, service_address TEXT, exchange_name TEXT, salefny_outstanding REAL DEFAULT 0);

CREATE TABLE line_bundles (subscription_id TEXT PRIMARY KEY, line_id TEXT REFERENCES lines(line_id), bundle_id TEXT REFERENCES bundles(bundle_id),
  subscribed_at TEXT, expires_at TEXT, auto_renew INTEGER, status TEXT, data_mb_total INTEGER, data_mb_used INTEGER,
  minutes_total INTEGER, minutes_used INTEGER, channel TEXT, price REAL);

CREATE TABLE vas_subscriptions (vas_sub_id TEXT PRIMARY KEY, line_id TEXT REFERENCES lines(line_id), vas_id TEXT REFERENCES vas_services(vas_id),
  subscribed_at TEXT, unsubscribed_at TEXT, channel TEXT, customer_consent TEXT, status TEXT);

CREATE TABLE transactions (txn_id TEXT PRIMARY KEY, line_id TEXT REFERENCES lines(line_id), account_id TEXT, customer_id TEXT,
  txn_type TEXT NOT NULL, created_at TEXT NOT NULL, amount REAL NOT NULL, net_credit REAL, currency TEXT DEFAULT 'EGP',
  channel TEXT, reference TEXT, related_id TEXT, status TEXT, description TEXT, counterparty_msisdn TEXT, is_duplicate INTEGER DEFAULT 0);

CREATE TABLE invoices (invoice_id TEXT PRIMARY KEY, account_id TEXT REFERENCES accounts(account_id), customer_id TEXT,
  period_start TEXT, period_end TEXT, issued_at TEXT, due_date TEXT, subtotal REAL, taxes REAL, total REAL,
  amount_paid REAL, status TEXT, paid_at TEXT);
CREATE TABLE invoice_items (item_id TEXT PRIMARY KEY, invoice_id TEXT REFERENCES invoices(invoice_id), line_id TEXT,
  item_type TEXT, description TEXT, amount REAL);

CREATE TABLE usage_daily (line_id TEXT REFERENCES lines(line_id), usage_date TEXT, data_mb REAL, voice_onnet_min REAL,
  voice_offnet_min REAL, intl_min REAL, sms INTEGER, roaming_data_mb REAL, roaming_country TEXT, PRIMARY KEY (line_id, usage_date));

CREATE TABLE device_installments (plan_id TEXT PRIMARY KEY, account_id TEXT REFERENCES accounts(account_id), line_id TEXT,
  device_id TEXT REFERENCES devices(device_id), imei TEXT, start_date TEXT, months INTEGER, monthly_amount REAL,
  paid_installments INTEGER, missed_installments INTEGER, device_locked INTEGER, status TEXT);

CREATE TABLE sim_events (event_id TEXT PRIMARY KEY, line_id TEXT REFERENCES lines(line_id), event_type TEXT, created_at TEXT,
  store_id TEXT, channel TEXT, fee REAL, old_iccid TEXT, new_iccid TEXT, verified_by TEXT, note TEXT);

CREATE TABLE port_requests (port_id TEXT PRIMARY KEY, msisdn TEXT, customer_id TEXT, direction TEXT, other_operator TEXT,
  requested_at TEXT, switch_date TEXT, status TEXT, reject_reason TEXT, fee REAL);

CREATE TABLE network_outages (outage_id TEXT PRIMARY KEY, governorate TEXT, city TEXT, service TEXT, started_at TEXT,
  ended_at TEXT, status TEXT, cause TEXT, compensation_eligible INTEGER);

CREATE TABLE technician_appointments (appointment_id TEXT PRIMARY KEY, line_id TEXT REFERENCES lines(line_id), reason TEXT,
  scheduled_date TEXT, window TEXT, status TEXT, created_at TEXT, fee REAL, notes TEXT);

CREATE TABLE complaints (complaint_id TEXT PRIMARY KEY, customer_id TEXT, line_id TEXT, category TEXT, subcategory TEXT,
  channel TEXT, created_at TEXT, sla_due TEXT, resolved_at TEXT, status TEXT, priority TEXT, escalated_to TEXT,
  resolution TEXT, refund_amount REAL, ntra_referral INTEGER);

CREATE TABLE refunds (refund_id TEXT PRIMARY KEY, customer_id TEXT, line_id TEXT, complaint_id TEXT, reason TEXT, amount REAL,
  method TEXT, requested_at TEXT, approved_by TEXT, status TEXT, processed_at TEXT, related_txn_id TEXT);

CREATE TABLE conversations (conversation_id TEXT PRIMARY KEY, customer_id TEXT, line_id TEXT, channel TEXT, language TEXT,
  started_at TEXT, intent TEXT, secondary_intent TEXT, identity_verified INTEGER, outcome TEXT, escalation_reason TEXT,
  complaint_id TEXT, csat INTEGER, frustration TEXT, turns INTEGER);
CREATE TABLE conversation_messages (message_id TEXT PRIMARY KEY, conversation_id TEXT REFERENCES conversations(conversation_id),
  seq INTEGER, sender TEXT, sent_at TEXT, text TEXT, citation TEXT);

CREATE TABLE policy_documents (document_id TEXT, version TEXT, title_en TEXT, title_ar TEXT, effective_date TEXT, current INTEGER,
  PRIMARY KEY (document_id, version));
CREATE TABLE policy_sections (passage_id TEXT PRIMARY KEY, document_id TEXT, version TEXT, section_no INTEGER,
  title_en TEXT, title_ar TEXT, text_en TEXT, text_ar TEXT, keywords TEXT, source_ids TEXT);
CREATE TABLE policy_sources (source_id TEXT PRIMARY KEY, description TEXT, url TEXT);
CREATE TABLE faq (faq_id TEXT PRIMARY KEY, category TEXT, question_en TEXT, question_ar TEXT, question_arabizi TEXT,
  answer_en TEXT, answer_ar TEXT, citation TEXT REFERENCES policy_sections(passage_id));
CREATE TABLE business_rules (rule_id TEXT PRIMARY KEY, action TEXT, rule_json TEXT);

CREATE INDEX ix_accounts_customer ON accounts(customer_id);
CREATE INDEX ix_lines_customer ON lines(customer_id);
CREATE INDEX ix_lines_account ON lines(account_id);
CREATE INDEX ix_txn_line_time ON transactions(line_id, created_at);
CREATE INDEX ix_txn_type ON transactions(txn_type);
CREATE INDEX ix_invoices_account ON invoices(account_id);
CREATE INDEX ix_items_invoice ON invoice_items(invoice_id);
CREATE INDEX ix_bundles_line ON line_bundles(line_id);
CREATE INDEX ix_complaints_customer ON complaints(customer_id);
CREATE INDEX ix_conv_customer ON conversations(customer_id);
CREATE INDEX ix_msgs_conv ON conversation_messages(conversation_id);
