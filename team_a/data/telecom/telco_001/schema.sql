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

-- ======== network troubleshooting (migrations/001_network_automation.sql) ========
-- 001: network map, troubleshooting playbook, diagnosis/automation history (Masr Connect, telco_001).
-- Additive only: no existing table or column is removed or renamed; new columns on existing tables are optional.

-- ---------- F. bookkeeping ----------
CREATE TABLE schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL, description TEXT);

-- ---------- A. network map ----------
CREATE TABLE network_sites (site_id TEXT PRIMARY KEY, name TEXT NOT NULL, governorate TEXT NOT NULL, city TEXT NOT NULL,
  latitude REAL, longitude REAL, sim_x_m REAL, sim_y_m REAL);

CREATE TABLE network_elements (element_id TEXT PRIMARY KEY,
  element_type TEXT NOT NULL CHECK (element_type IN ('gnb_cell','enb_cell','core_nf','router','switch','access_point','backhaul_node',
                                                     'exchange','dslam_port','olt_port','bng','cpe')),
  name TEXT NOT NULL, site_id TEXT REFERENCES network_sites(site_id), parent_element_id TEXT REFERENCES network_elements(element_id),
  vendor TEXT, model TEXT, firmware TEXT,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','degraded','down','maintenance','unknown')),
  status_changed_at TEXT,
  managed_by TEXT NOT NULL DEFAULT 'simulator' CHECK (managed_by IN ('simulator','ems','acs','manual')),
  external_ref TEXT, shared INTEGER NOT NULL DEFAULT 1 CHECK (shared IN (0,1)), attributes_json TEXT NOT NULL DEFAULT '{}',
  UNIQUE (managed_by, external_ref));
CREATE INDEX ix_elements_site ON network_elements(site_id);
CREATE INDEX ix_elements_parent ON network_elements(parent_element_id);
CREATE INDEX ix_elements_type_status ON network_elements(element_type, status);

CREATE TABLE element_interfaces (interface_id TEXT PRIMARY KEY, element_id TEXT NOT NULL REFERENCES network_elements(element_id),
  name TEXT NOT NULL, if_type TEXT NOT NULL,
  admin_status TEXT DEFAULT 'up' CHECK (admin_status IN ('up','down')),
  oper_status TEXT DEFAULT 'up' CHECK (oper_status IN ('up','down','degraded','unknown')),
  speed_mbps REAL, UNIQUE (element_id, name));

CREATE TABLE network_links (link_id TEXT PRIMARY KEY,
  a_element_id TEXT NOT NULL REFERENCES network_elements(element_id), b_element_id TEXT NOT NULL REFERENCES network_elements(element_id),
  a_interface_id TEXT REFERENCES element_interfaces(interface_id), b_interface_id TEXT REFERENCES element_interfaces(interface_id),
  link_type TEXT NOT NULL CHECK (link_type IN ('X2','S1','N2','N3','backhaul','fiber','copper','ethernet','wireless')),
  capacity_mbps REAL, status TEXT DEFAULT 'active' CHECK (status IN ('active','degraded','down','maintenance','unknown')),
  external_ref TEXT, CHECK (a_element_id != b_element_id));
CREATE INDEX ix_links_a ON network_links(a_element_id);
CREATE INDEX ix_links_b ON network_links(b_element_id);

CREATE TABLE line_network_bindings (binding_id TEXT PRIMARY KEY, line_id TEXT NOT NULL REFERENCES lines(line_id),
  element_id TEXT NOT NULL REFERENCES network_elements(element_id),
  role TEXT NOT NULL CHECK (role IN ('serving_cell','home_cell','access_port','cpe','bng','core_nf')),
  valid_from TEXT NOT NULL, valid_to TEXT,
  source TEXT NOT NULL CHECK (source IN ('inventory','simulator','network_event','manual')),
  CHECK (valid_to IS NULL OR valid_to >= valid_from));
CREATE UNIQUE INDEX ux_bindings_one_current ON line_network_bindings(line_id, role) WHERE valid_to IS NULL;
CREATE INDEX ix_bindings_line_role ON line_network_bindings(line_id, role, valid_to);
CREATE INDEX ix_bindings_element ON line_network_bindings(element_id, valid_to);

CREATE TABLE network_alarms (alarm_id TEXT PRIMARY KEY, element_id TEXT NOT NULL REFERENCES network_elements(element_id),
  severity TEXT NOT NULL CHECK (severity IN ('critical','major','minor','warning','info')),
  alarm_code TEXT NOT NULL, message TEXT NOT NULL, raised_at TEXT NOT NULL, acknowledged_at TEXT, cleared_at TEXT, external_ref TEXT);
CREATE INDEX ix_alarms_element_open ON network_alarms(element_id, cleared_at);
CREATE INDEX ix_alarms_raised ON network_alarms(raised_at);

-- ---------- D. troubleshooting playbook ----------
-- UNKNOWN is the one cause that is neither side, hence the extra 'unknown' value on scope and layer.
CREATE TABLE root_cause_catalog (cause_code TEXT PRIMARY KEY,
  scope TEXT NOT NULL CHECK (scope IN ('user','infrastructure','unknown')),
  layer TEXT NOT NULL CHECK (layer IN ('account','device','configuration','coverage','access','transport','core','unknown')),
  title_en TEXT NOT NULL, title_ar TEXT NOT NULL, customer_text_en TEXT, customer_text_ar TEXT,
  remotely_fixable INTEGER NOT NULL DEFAULT 0 CHECK (remotely_fixable IN (0,1)));

CREATE TABLE resolution_procedures (procedure_id TEXT NOT NULL, version INTEGER NOT NULL,
  cause_code TEXT NOT NULL REFERENCES root_cause_catalog(cause_code),
  kind TEXT NOT NULL CHECK (kind IN ('automated','guided','policy_action','technician','noc')),
  action_name TEXT, steps_json TEXT DEFAULT '[]',
  target_scope TEXT NOT NULL CHECK (target_scope IN ('single_line','element','area')),
  risk TEXT NOT NULL CHECK (risk IN ('low','medium','high','critical')),
  needs_customer_consent INTEGER NOT NULL DEFAULT 1 CHECK (needs_customer_consent IN (0,1)),
  needs_human_approval INTEGER NOT NULL DEFAULT 0 CHECK (needs_human_approval IN (0,1)),
  max_per_line_per_day INTEGER, verify_checks_json TEXT DEFAULT '[]', settle_seconds INTEGER DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','approved','retired')),
  PRIMARY KEY (procedure_id, version),
  CHECK (target_scope = 'single_line' OR needs_human_approval = 1));

-- ---------- B1. outages: link to equipment ----------
ALTER TABLE network_outages ADD COLUMN root_element_id TEXT REFERENCES network_elements(element_id);
ALTER TABLE network_outages ADD COLUMN detected_by TEXT;
ALTER TABLE network_outages ADD COLUMN affected_lines INTEGER;
ALTER TABLE network_outages ADD COLUMN eta TEXT;

CREATE TABLE outage_elements (outage_id TEXT NOT NULL REFERENCES network_outages(outage_id),
  element_id TEXT NOT NULL REFERENCES network_elements(element_id), PRIMARY KEY (outage_id, element_id));
CREATE INDEX ix_outage_elements_element ON outage_elements(element_id);

-- ---------- E. history and proof ----------
CREATE TABLE diagnostic_runs (run_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
  customer_id TEXT REFERENCES customers(customer_id), line_id TEXT REFERENCES lines(line_id),
  element_id TEXT REFERENCES network_elements(element_id),
  trigger TEXT NOT NULL CHECK (trigger IN ('conversation','complaint','alarm','scheduled','manual')),
  conversation_id TEXT, trace_id TEXT, symptom TEXT NOT NULL, playbook_version TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('running','completed','failed','timed_out')),
  classification TEXT CHECK (classification IN ('user','infrastructure','known_outage','inconclusive')),
  root_cause_code TEXT REFERENCES root_cause_catalog(cause_code),
  confidence REAL CHECK (confidence BETWEEN 0 AND 1),
  outage_id TEXT REFERENCES network_outages(outage_id),
  started_at TEXT NOT NULL, finished_at TEXT, summary TEXT,
  CHECK (line_id IS NOT NULL OR element_id IS NOT NULL));
CREATE INDEX ix_runs_line_time ON diagnostic_runs(line_id, started_at);
CREATE INDEX ix_runs_conversation ON diagnostic_runs(tenant_id, conversation_id);
CREATE INDEX ix_runs_cause_time ON diagnostic_runs(root_cause_code, started_at);

CREATE TABLE diagnostic_checks (check_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES diagnostic_runs(run_id),
  seq INTEGER NOT NULL, check_name TEXT NOT NULL, element_id TEXT REFERENCES network_elements(element_id),
  status TEXT NOT NULL CHECK (status IN ('ok','abnormal','error','skipped','timed_out')),
  verdict_code TEXT, measurements_json TEXT DEFAULT '{}', duration_ms INTEGER, UNIQUE (run_id, seq));

CREATE TABLE automation_executions (execution_id TEXT PRIMARY KEY, run_id TEXT REFERENCES diagnostic_runs(run_id),
  procedure_id TEXT NOT NULL, procedure_version INTEGER NOT NULL, action_name TEXT NOT NULL,
  target_line_id TEXT REFERENCES lines(line_id), target_element_id TEXT REFERENCES network_elements(element_id),
  mode TEXT NOT NULL CHECK (mode IN ('dry_run','simulation','lab','production')),
  idempotency_key TEXT NOT NULL UNIQUE,
  requested_by TEXT NOT NULL CHECK (requested_by IN ('agent','human','noc')), actor TEXT NOT NULL,
  policy_request_id TEXT, approval_id TEXT,
  status TEXT NOT NULL CHECK (status IN ('pending','running','succeeded','failed','timed_out','outcome_unknown','rolled_back','cancelled')),
  pre_state_json TEXT, result_json TEXT, error_code TEXT,
  rollback_of TEXT REFERENCES automation_executions(execution_id),
  requested_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
  FOREIGN KEY (procedure_id, procedure_version) REFERENCES resolution_procedures(procedure_id, version),
  CHECK (target_line_id IS NOT NULL OR target_element_id IS NOT NULL),
  CHECK (target_element_id IS NULL OR approval_id IS NOT NULL OR mode = 'dry_run'));
CREATE INDEX ix_exec_line_time ON automation_executions(target_line_id, requested_at);
CREATE INDEX ix_exec_element_time ON automation_executions(target_element_id, requested_at);
CREATE INDEX ix_exec_status ON automation_executions(status);

CREATE TABLE verification_results (verification_id TEXT PRIMARY KEY,
  execution_id TEXT REFERENCES automation_executions(execution_id), run_id TEXT NOT NULL REFERENCES diagnostic_runs(run_id),
  checked_at TEXT NOT NULL, passed INTEGER NOT NULL CHECK (passed IN (0,1)), checks_json TEXT NOT NULL,
  customer_confirmed INTEGER CHECK (customer_confirmed IN (0,1)));
CREATE INDEX ix_verif_execution ON verification_results(execution_id);

CREATE TABLE automation_events (event_id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
  run_id TEXT REFERENCES diagnostic_runs(run_id), execution_id TEXT REFERENCES automation_executions(execution_id),
  actor TEXT NOT NULL, event_type TEXT NOT NULL, detail_json TEXT DEFAULT '{}');
CREATE INDEX ix_events_run ON automation_events(run_id);
CREATE INDEX ix_events_execution ON automation_events(execution_id);
CREATE TRIGGER trg_automation_events_no_update BEFORE UPDATE ON automation_events
BEGIN SELECT RAISE(ABORT, 'automation_events is append-only'); END;
CREATE TRIGGER trg_automation_events_no_delete BEFORE DELETE ON automation_events
BEGIN SELECT RAISE(ABORT, 'automation_events is append-only'); END;

-- ---------- B2/B3. complaints and appointments: link to the investigation ----------
ALTER TABLE complaints ADD COLUMN diagnostic_run_id TEXT REFERENCES diagnostic_runs(run_id);
ALTER TABLE complaints ADD COLUMN root_cause_code TEXT REFERENCES root_cause_catalog(cause_code);
ALTER TABLE complaints ADD COLUMN resolution_method TEXT;
ALTER TABLE complaints ADD COLUMN outage_id TEXT REFERENCES network_outages(outage_id);
ALTER TABLE complaints ADD COLUMN handoff_case_id TEXT;
ALTER TABLE technician_appointments ADD COLUMN diagnostic_run_id TEXT REFERENCES diagnostic_runs(run_id);

-- ---------- C. faster lookups on existing tables ----------
CREATE INDEX ix_complaints_line_time ON complaints(line_id, created_at);
CREATE INDEX ix_appointments_line ON technician_appointments(line_id, scheduled_date);
CREATE INDEX ix_outages_area_status ON network_outages(governorate, city, status);
CREATE INDEX ix_lines_landline ON lines(home_landline);
CREATE INDEX ix_lines_imsi ON lines(imsi);
