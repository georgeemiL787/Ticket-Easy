-- 002: root-cause catalogue, resolution procedures (v1, approved) and the demo cluster that mirrors TelecomAgent's default network.
-- Run after 001:  sqlite3 telecom.db < migrations/002_seed_catalogue_and_demo_cluster.sql
PRAGMA foreign_keys = ON;
BEGIN;

-- ---------- G1. root causes ----------
INSERT INTO root_cause_catalog (cause_code, scope, layer, title_en, title_ar, customer_text_en, customer_text_ar, remotely_fixable) VALUES
('LINE_SUSPENDED', 'user', 'account', 'Line suspended', 'الخط موقوف',
 'Your line is suspended, so it can''t use the network until the reason is cleared.',
 'خطك موقوف، فمش هيقدر يستخدم الشبكة لحد ما سبب الإيقاف يتحل.', 0),
('NO_BALANCE', 'user', 'account', 'No balance', 'مفيش رصيد',
 'Your balance has run out, so data and calls are paused until you recharge.',
 'رصيدك خلص، فالنت والمكالمات واقفين لحد ما تشحن.', 0),
('QUOTA_EXHAUSTED', 'user', 'account', 'Data quota used up', 'الباقة خلصت',
 'You''ve used all the data in your bundle. You can renew it or add an extra bundle.',
 'إنت استهلكت كل ميجات الباقة. تقدر تجدد أو تضيف باقة إضافية.', 0),
('DATA_ROAMING_OFF', 'user', 'configuration', 'Roaming is off', 'التجوال مقفول',
 'Roaming isn''t switched on for your line, so data doesn''t work abroad.',
 'خدمة التجوال مش مفعلة على خطك، علشان كده النت مش شغال بره مصر.', 0),
('DEVICE_NOT_ATTACHED', 'user', 'device', 'Phone not connected to the network', 'الموبايل مش متسجل على الشبكة',
 'Your phone isn''t connected to our network right now. A few simple steps on the phone usually fix this.',
 'موبايلك مش متصل بالشبكة دلوقتي. خطوات بسيطة على الموبايل غالبًا بتحل المشكلة.', 0),
('SESSION_STALE', 'user', 'configuration', 'Data session stuck', 'جلسة النت معلقة',
 'Your data connection got stuck on our side. We can reset it for you from here.',
 'اتصال النت بتاعك علّق عندنا. نقدر نعمله إعادة تشغيل من عندنا.', 1),
('PROFILE_MISMATCH', 'user', 'configuration', 'Line settings out of date', 'إعدادات الخط مش متحدثة',
 'Your line''s network settings don''t match your plan. We can refresh them for you.',
 'إعدادات خطك على الشبكة مش مطابقة لباقتك. نقدر نحدّثها من عندنا.', 1),
('POOR_COVERAGE', 'user', 'coverage', 'Weak signal where you are', 'الإشارة ضعيفة في مكانك',
 'The signal where you are is weak. We''ve passed your location to our network department.',
 'الإشارة في مكانك ضعيفة. بلّغنا فريق الشبكة بمكانك.', 0),
('CELL_DOWN', 'infrastructure', 'access', 'Nearby tower not working', 'البرج اللي جنبك واقف',
 'The tower serving your area has a fault. Our engineers are working on it.',
 'البرج اللي بيخدم منطقتك فيه عطل، ومهندسينا شغالين عليه.', 0),
('CELL_DEGRADED', 'infrastructure', 'access', 'Nearby tower not working well', 'البرج اللي جنبك أداؤه ضعيف',
 'The tower serving your area isn''t working at full strength. Our engineers are on it.',
 'البرج اللي بيخدم منطقتك مش شغال بكفاءته الكاملة، ومهندسينا متابعين.', 0),
('CELL_OVERLOAD', 'infrastructure', 'access', 'Network busy in your area', 'زحمة على الشبكة في منطقتك',
 'The network in your area is very busy right now, which slows things down. Our team is adding capacity.',
 'الشبكة في منطقتك عليها ضغط عالي دلوقتي فالسرعة بتقل. فريقنا بيزوّد السعة.', 0),
('INTERFERENCE', 'infrastructure', 'access', 'Signal interference', 'تداخل في الإشارة',
 'There''s interference on the signal in your area. Our engineers are tracing it.',
 'فيه تداخل على الإشارة في منطقتك، ومهندسينا بيدوروا على مصدره.', 0),
('BACKHAUL_DEGRADED', 'infrastructure', 'transport', 'Network link problem in your area', 'مشكلة في وصلة الشبكة في منطقتك',
 'A network link that serves your area has a problem. A technician has been assigned.',
 'فيه مشكلة في وصلة شبكة بتخدم منطقتك، واتحدد فني يصلحها.', 0),
('CORE_NF_DEGRADED', 'infrastructure', 'core', 'Central network problem', 'مشكلة في الشبكة الرئيسية',
 'Part of our central network has a problem affecting several areas. Our engineers are fixing it.',
 'فيه جزء من الشبكة الرئيسية عنده مشكلة مأثرة على كذا منطقة، ومهندسينا بيصلحوها.', 0),
('KNOWN_OUTAGE', 'infrastructure', 'access', 'Known outage in your area', 'عطل معروف في منطقتك',
 'There''s a known outage in your area. We''re fixing it and will let you know the expected time.',
 'فيه عطل معروف في منطقتك، وبنصلحه وهنبلغك بالوقت المتوقع.', 0),
('UNKNOWN', 'unknown', 'unknown', 'Cause not found yet', 'السبب لسه مش معروف',
 'We couldn''t find the cause from here, so we''re passing it to a specialist.',
 'ماقدرناش نحدد السبب من هنا، فهنحوّل الموضوع لمتخصص.', 0);

-- ---------- G2. procedures (version 1, approved) ----------
INSERT INTO resolution_procedures (procedure_id, version, cause_code, kind, action_name, steps_json, target_scope, risk,
  needs_customer_consent, needs_human_approval, max_per_line_per_day, verify_checks_json, settle_seconds, status) VALUES
('PR-QUOTA', 1, 'QUOTA_EXHAUSTED', 'policy_action', 'subscribe_bundle', '[]', 'single_line', 'medium', 1, 0, NULL, '["quota"]', 0, 'approved'),
('PR-ROAMING', 1, 'DATA_ROAMING_OFF', 'policy_action', 'activate_roaming', '[]', 'single_line', 'medium', 1, 0, NULL, '["roaming_enabled"]', 0, 'approved'),
('PR-REATTACH', 1, 'DEVICE_NOT_ATTACHED', 'guided', NULL,
 '[{"step":"airplane_mode_toggle","recheck":"subscriber_attach"},{"step":"restart_phone","recheck":"subscriber_attach"},{"step":"reseat_sim","recheck":"subscriber_attach"},{"step":"select_network_automatically","recheck":"subscriber_attach"}]',
 'single_line', 'low', 0, 0, NULL, '["subscriber_attach"]', 30, 'approved'),
('PR-SESSION', 1, 'SESSION_STALE', 'automated', 'reset_data_session', '[]', 'single_line', 'low', 1, 0, 2, '["subscriber_attach","data_session"]', 20, 'approved'),
('PR-PROFILE', 1, 'PROFILE_MISMATCH', 'automated', 'refresh_subscriber_profile', '[]', 'single_line', 'medium', 1, 0, 1, '["subscriber_profile","data_session"]', 30, 'approved'),
('PR-COVERAGE', 1, 'POOR_COVERAGE', 'noc', NULL, '[]', 'area', 'low', 0, 1, NULL, '[]', 0, 'approved'),
('PR-CELL-RST', 1, 'CELL_DOWN', 'noc', 'restart_cell', '[]', 'element', 'high', 0, 1, NULL, '["serving_cell","peer_impact"]', 120, 'approved'),
('PR-CELL-DEG', 1, 'CELL_DEGRADED', 'noc', 'restart_cell', '[]', 'element', 'high', 0, 1, NULL, '["serving_cell","peer_impact"]', 120, 'approved'),
('PR-OVERLOAD', 1, 'CELL_OVERLOAD', 'noc', 'change_cell_parameter', '[]', 'element', 'medium', 0, 1, NULL, '["serving_cell"]', 300, 'approved'),
('PR-BACKHAUL', 1, 'BACKHAUL_DEGRADED', 'technician', NULL, '[]', 'element', 'high', 0, 1, NULL, '["serving_cell"]', 0, 'approved'),
('PR-CORE-NF', 1, 'CORE_NF_DEGRADED', 'noc', 'restart_network_function', '[]', 'area', 'critical', 0, 1, NULL, '["subscriber_attach","data_session"]', 180, 'approved'),
('PR-KNOWN', 1, 'KNOWN_OUTAGE', 'policy_action', NULL, '[{"step":"inform_outage_and_eta"},{"step":"offer_compensation_if_eligible"}]', 'single_line', 'low', 0, 0, NULL, '[]', 0, 'approved');

-- ---------- G3. demo cluster (TelecomAgent default network) ----------
INSERT INTO network_sites VALUES ('SITE-CAI-DT', 'Cairo Downtown', 'Cairo', 'Downtown', 30.0444, 31.2357, 0, 0);

INSERT INTO network_elements (element_id, element_type, name, site_id, parent_element_id, vendor, model, firmware, status, status_changed_at,
  managed_by, external_ref, shared, attributes_json) VALUES
('NE-CORE-001', 'core_nf', 'AMF-1',  'SITE-CAI-DT', NULL, 'Open5GS', 'amf', '2.7', 'active', '2026-10-01T00:00:00', 'simulator', 'AMF-1',  1, '{"nf":"AMF"}'),
('NE-CORE-002', 'core_nf', 'SMF-1',  'SITE-CAI-DT', NULL, 'Open5GS', 'smf', '2.7', 'active', '2026-10-01T00:00:00', 'simulator', 'SMF-1',  1, '{"nf":"SMF"}'),
('NE-CORE-003', 'core_nf', 'UPF-1',  'SITE-CAI-DT', NULL, 'Open5GS', 'upf', '2.7', 'active', '2026-10-01T00:00:00', 'simulator', 'UPF-1',  1, '{"nf":"UPF","capacity_mbps":10000}'),
('NE-CORE-004', 'core_nf', 'PCF-1',  'SITE-CAI-DT', NULL, 'Open5GS', 'pcf', '2.7', 'active', '2026-10-01T00:00:00', 'simulator', 'PCF-1',  1, '{"nf":"PCF"}'),
('NE-CORE-005', 'core_nf', 'UDM-1',  'SITE-CAI-DT', NULL, 'Open5GS', 'udm', '2.7', 'active', '2026-10-01T00:00:00', 'simulator', 'UDM-1',  1, '{"nf":"UDM"}'),
('NE-CORE-006', 'core_nf', 'AUSF-1', 'SITE-CAI-DT', NULL, 'Open5GS', 'ausf','2.7', 'active', '2026-10-01T00:00:00', 'simulator', 'AUSF-1', 1, '{"nf":"AUSF"}'),
('NE-CELL-001', 'gnb_cell', 'gNB-1', 'SITE-CAI-DT', NULL, 'Ericsson', 'AIR 6449', '23.Q4', 'active', '2026-10-01T00:00:00', 'simulator', 'CELL-001', 1,
 '{"cell_class":"macro","band":"n78","bandwidth_mhz":100,"pci":101,"capacity_mbps":1500,"sim_x_m":0,"sim_y_m":0}'),
('NE-CELL-002', 'gnb_cell', 'gNB-2', 'SITE-CAI-DT', NULL, 'Ericsson', 'AIR 6449', '23.Q4', 'active', '2026-10-01T00:00:00', 'simulator', 'CELL-002', 1,
 '{"cell_class":"macro","band":"n78","bandwidth_mhz":100,"pci":102,"capacity_mbps":1500,"sim_x_m":1200,"sim_y_m":0}'),
('NE-CELL-003', 'gnb_cell', 'gNB-3', 'SITE-CAI-DT', NULL, 'Ericsson', 'AIR 6449', '23.Q4', 'active', '2026-10-01T00:00:00', 'simulator', 'CELL-003', 1,
 '{"cell_class":"macro","band":"n78","bandwidth_mhz":100,"pci":103,"capacity_mbps":1500,"sim_x_m":600,"sim_y_m":1000}'),
('NE-CELL-004', 'gnb_cell', 'gNB-4', 'SITE-CAI-DT', NULL, 'Nokia', 'AirScale Micro', '24R1', 'active', '2026-10-01T00:00:00', 'simulator', 'CELL-004', 1,
 '{"cell_class":"micro","band":"n78","bandwidth_mhz":40,"pci":201,"capacity_mbps":400,"sim_x_m":300,"sim_y_m":300}'),
('NE-CELL-005', 'gnb_cell', 'gNB-5', 'SITE-CAI-DT', NULL, 'Nokia', 'AirScale Micro', '24R1', 'active', '2026-10-01T00:00:00', 'simulator', 'CELL-005', 1,
 '{"cell_class":"micro","band":"n78","bandwidth_mhz":40,"pci":202,"capacity_mbps":400,"sim_x_m":900,"sim_y_m":300}'),
('NE-CELL-006', 'gnb_cell', 'gNB-6', 'SITE-CAI-DT', NULL, 'Nokia', 'AirScale Micro', '24R1', 'active', '2026-10-01T00:00:00', 'simulator', 'CELL-006', 1,
 '{"cell_class":"micro","band":"n78","bandwidth_mhz":40,"pci":203,"capacity_mbps":400,"sim_x_m":600,"sim_y_m":650}'),
('NE-CELL-007', 'gnb_cell', 'gNB-7', 'SITE-CAI-DT', NULL, 'Nokia', 'AirScale Micro', '24R1', 'active', '2026-10-01T00:00:00', 'simulator', 'CELL-007', 1,
 '{"cell_class":"micro","band":"n78","bandwidth_mhz":40,"pci":204,"capacity_mbps":400,"sim_x_m":1100,"sim_y_m":800}');

-- every cell has an N2 (control, to AMF) and N3 (user data, to UPF) interface; AMF and UPF have one each
INSERT INTO element_interfaces (interface_id, element_id, name, if_type, admin_status, oper_status, speed_mbps)
SELECT 'IF-' || substr(element_id, 4) || '-N2', element_id, 'N2', 'N2', 'up', 'up', 1000 FROM network_elements WHERE element_type = 'gnb_cell'
UNION ALL
SELECT 'IF-' || substr(element_id, 4) || '-N3', element_id, 'N3', 'N3', 'up', 'up', 10000 FROM network_elements WHERE element_type = 'gnb_cell'
UNION ALL SELECT 'IF-CORE-001-N2', 'NE-CORE-001', 'N2', 'N2', 'up', 'up', 10000
UNION ALL SELECT 'IF-CORE-003-N3', 'NE-CORE-003', 'N3', 'N3', 'up', 'up', 40000;

INSERT INTO network_links (link_id, a_element_id, b_element_id, a_interface_id, b_interface_id, link_type, capacity_mbps, status, external_ref)
SELECT 'LK-' || substr(element_id, 4) || '-N2', element_id, 'NE-CORE-001', 'IF-' || substr(element_id, 4) || '-N2', 'IF-CORE-001-N2', 'N2', 1000, 'active', NULL
  FROM network_elements WHERE element_type = 'gnb_cell' AND site_id = 'SITE-CAI-DT'
UNION ALL
SELECT 'LK-' || substr(element_id, 4) || '-N3', element_id, 'NE-CORE-003', 'IF-' || substr(element_id, 4) || '-N3', 'IF-CORE-003-N3', 'N3', 10000, 'active', NULL
  FROM network_elements WHERE element_type = 'gnb_cell' AND site_id = 'SITE-CAI-DT';

-- neighbour (X2/Xn) relations: the three macros with each other, each micro with its nearest macro
INSERT INTO network_links (link_id, a_element_id, b_element_id, link_type, capacity_mbps, status) VALUES
('LK-X2-001-002', 'NE-CELL-001', 'NE-CELL-002', 'X2', 1000, 'active'),
('LK-X2-002-003', 'NE-CELL-002', 'NE-CELL-003', 'X2', 1000, 'active'),
('LK-X2-001-003', 'NE-CELL-001', 'NE-CELL-003', 'X2', 1000, 'active'),
('LK-X2-004-001', 'NE-CELL-004', 'NE-CELL-001', 'X2', 1000, 'active'),
('LK-X2-005-002', 'NE-CELL-005', 'NE-CELL-002', 'X2', 1000, 'active'),
('LK-X2-006-003', 'NE-CELL-006', 'NE-CELL-003', 'X2', 1000, 'active'),
('LK-X2-007-002', 'NE-CELL-007', 'NE-CELL-002', 'X2', 1000, 'active');

-- three demo lines attached to the cluster (by phone number, so the seed does not depend on generated line ids)
INSERT INTO line_network_bindings (binding_id, line_id, element_id, role, valid_from, valid_to, source)
SELECT 'BD-DEMO-D007', line_id, 'NE-CELL-003', 'serving_cell', '2026-10-08T08:00:00', NULL, 'simulator' FROM lines WHERE msisdn = '01212340007'
UNION ALL
SELECT 'BD-DEMO-D001', line_id, 'NE-CELL-001', 'serving_cell', '2026-10-08T08:00:00', NULL, 'simulator' FROM lines WHERE msisdn = '01012340001'
UNION ALL
SELECT 'BD-DEMO-D009', line_id, 'NE-CELL-004', 'serving_cell', '2026-10-08T08:00:00', NULL, 'simulator' FROM lines WHERE msisdn = '01112340009';

INSERT INTO schema_migrations VALUES ('002', strftime('%Y-%m-%dT%H:%M:%S', 'now'), 'Root-cause catalogue, procedures v1, demo cluster');
COMMIT;
