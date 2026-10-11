"""Network-side demo data: core regions and the 10 network-fault demo customers (TC-D021..TC-D030), one per fault type.

Used by net_generate.py (writes them into telecom.db) and export.py (writes fixtures/telco_001/network.json).
Infrastructure faults sit in cities no other demo customer lives in, so they never leak into the TC-D001..D020 scenarios.
"""

# each region has its own core functions and broadband gateway; Greater Cairo uses the demo cluster's AMF-1..AUSF-1
REGIONS = {
    "GC": ("Greater Cairo", ["Cairo", "Giza", "Qalyubia"]),
    "AW": ("Alexandria and West Delta", ["Alexandria", "Beheira"]),
    "DL": ("Delta", ["Dakahlia", "Damietta", "Gharbia", "Kafr El Sheikh", "Monufia", "Sharqia"]),
    "CN": ("Canal", ["Port Said", "Ismailia", "Suez"]),
    "UE": ("Upper Egypt", ["Faiyum", "Beni Suef", "Minya", "Asyut", "Sohag", "Qena", "Luxor", "Aswan"]),
    "RS": ("Red Sea and Sinai", ["Red Sea", "South Sinai"]),
}
REGION_OF = {gov: code for code, (_, govs) in REGIONS.items() for gov in govs}
CORE_NFS = ["AMF", "SMF", "UPF", "PCF", "UDM", "AUSF"]


def demo(cid, name_en, name_ar, male, lang, gov, last4, msisdn, plan, gen, note, fault, symptom, classification, cause, procedure,
         measurements, account="prepaid"):
    return dict(customer_id=cid, name_en=name_en, name_ar=name_ar, male=male, lang=lang, gov=gov, nid_last4=last4, msisdn=msisdn,
                plan=plan, network_gen=gen, account_type=account, note=note, fault=fault, symptom=symptom,
                expected={"classification": classification, "cause_code": cause, "procedure_id": procedure}, measurements=measurements)


# fault: what net_generate.py breaks for this customer
#   quota | none (the fault is only visible in the subscriber's live state) | cell_down | backhaul | core | outage
DEMO_NET = [
    demo("TC-D021", "Amr Nabil", "عمرو نبيل", True, "ar", ("Cairo", "Heliopolis"), "3142", "01012340021", "PRE-BASIC", "4G",
         "Mobile data stopped: Net 55 bundle fully used (3500 of 3500 MB).", "quota", "no_data", "user", "QUOTA_EXHAUSTED", "PR-QUOTA",
         {"attached": True, "data_session": "up", "quota_left_mb": 0}),
    demo("TC-D022", "Heba Fawzy", "هبة فوزي", False, "arabizi", ("Giza", "Mohandessin"), "5268", "01112340022", "PRE-BASIC", "4G",
         "Mobile data stuck: attached and has quota, but the data session has passed no traffic for 95 minutes.", "none", "no_data", "user",
         "SESSION_STALE", "PR-SESSION", {"attached": True, "data_session": "stale", "minutes_since_last_bytes": 95, "quota_left_mb": 2100}),
    demo("TC-D023", "Sherif Adly", "شريف عدلي", True, "en", ("Alexandria", "Sidi Gaber"), "7781", "01212340023", "POST-RED-550", "5G",
         "Slow data on a 5G plan: the network still has the old 4G subscriber profile.", "none", "slow_data", "user", "PROFILE_MISMATCH",
         "PR-PROFILE", {"attached": True, "data_session": "up", "profile_qos": "4g_default", "plan_qos": "5g_premium", "throughput_mbps": 4.2},
         account="postpaid"),
    demo("TC-D024", "Nadia Ramzy", "نادية رمزي", False, "mixed", ("Qalyubia", "Obour"), "9405", "01512340024", "CTL-90", "4G",
         "No service: the phone has not been attached to the network since 06:12 this morning; nothing wrong on the network side.",
         "none", "no_service", "user", "DEVICE_NOT_ATTACHED", "PR-REATTACH", {"attached": False, "last_attach_at": "2026-10-08T06:12:00"},
         account="control"),
    demo("TC-D025", "Khaled Shawky", "خالد شوقي", True, "ar", ("Faiyum", "Ibsheway"), "1623", "01012340025", "PRE-BASIC", "4G",
         "Calls drop at home: very weak signal at the customer's location, serving cell healthy.", "none", "drops", "user", "POOR_COVERAGE",
         "PR-COVERAGE", {"attached": True, "rsrp_dbm": -121, "sinr_db": -3, "neighbour_rsrp_dbm": -118}),
    demo("TC-D026", "Reem Galal", "ريم جلال", False, "arabizi", ("Cairo", "Helwan"), "4857", "01112340026", "PRE-YOUTH", "4G",
         "No service: the serving cell has been down since 13:20 (open cell_down alarm); no outage declared yet.", "cell_down", "no_service",
         "infrastructure", "CELL_DOWN", "PR-CELL-RST", {"attached": False, "serving_cell_status": "down", "peers_affected": True}),
    demo("TC-D027", "Wael Sabry", "وائل صبري", True, "en", ("Giza", "6th of October"), "6390", "01212340027", "POST-RED-350", "4G",
         "Slow data and drops: the site's backhaul link is losing packets (open backhaul_degraded alarm); the cell itself is fine.",
         "backhaul", "slow_data", "infrastructure", "BACKHAUL_DEGRADED", "PR-BACKHAUL",
         {"attached": True, "packet_loss_pct": 7.5, "latency_ms": 180, "serving_cell_status": "active"}, account="postpaid"),
    demo("TC-D028", "Samar Hosny", "سمر حسني", False, "ar", ("Red Sea", "Hurghada"), "2714", "01512340028", "PRE-BASIC", "5G",
         "Data sessions fail to start: the regional SMF is degraded (open alarm), affecting the whole Red Sea and Sinai region.",
         "core", "no_data", "infrastructure", "CORE_NF_DEGRADED", "PR-CORE-NF",
         {"attached": True, "data_session": "setup_failed", "session_setup_success_pct": 41}),
    demo("TC-D029", "Hany Saad", "هاني سعد", True, "mixed", ("Giza", "Imbaba"), "8036", "01012340029", "PRE-SECONDS", "4G",
         "No mobile data: inside a declared ongoing outage (OUT-D029, equipment fault on the Imbaba cell since 10:30).", "outage", "no_data",
         "known_outage", "KNOWN_OUTAGE", "PR-KNOWN", {"attached": True, "data_session": "setup_failed", "serving_cell_status": "degraded"}),
    demo("TC-D030", "Mai Fouad", "مي فؤاد", False, "en", ("Sharqia", "Belbeis"), "5172", "01112340030", "PRE-BASIC", "4G",
         "Intermittent slowness with every check normal: should end as inconclusive and be handed to a specialist.", "none", "slow_data",
         "inconclusive", "UNKNOWN", None, {"attached": True, "data_session": "up", "throughput_mbps": 18.5, "packet_loss_pct": 0.2}),
]

OUTAGE_D029 = dict(outage_id="OUT-D029", service="mobile_data", started_at="2026-10-08T10:30:00", cause="equipment_fault",
                   eta="2026-10-08T19:00:00")
ETAS = {"OUT-D007": "2026-10-09T18:00:00", "OUT-00043": "2026-10-08T20:00:00", "OUT-D029": OUTAGE_D029["eta"]}
