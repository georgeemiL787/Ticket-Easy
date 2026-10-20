-- 001: network map, troubleshooting playbook, diagnosis/automation history (Masr Connect, telco_001).
-- Additive only: no existing table or column is removed or renamed; new columns on existing tables are optional.
-- Run:  sqlite3 telecom.db < migrations/001_network_automation.sql
PRAGMA foreign_keys = ON;
BEGIN;

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

INSERT INTO schema_migrations VALUES ('001', strftime('%Y-%m-%dT%H:%M:%S', 'now'), 'Network map, troubleshooting playbook, diagnosis and automation history');
COMMIT;
