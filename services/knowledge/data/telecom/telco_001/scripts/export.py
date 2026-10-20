"""Write the plug-in files next to the database: policies, FAQ, agent-shaped fixtures, tenant config, scenarios.

Run after generate.py:  python3 export.py
"""
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from content_conversations import INTENTS
from content_faq import FAQ
from content_policies import BRAND_AR, BRAND_EN, EFFECTIVE, POLICIES, SOURCES
from content_scenarios import SCENARIOS
from demo_and_rules import DEMO, RULES
from demo_network import DEMO_NET

OUT = Path(__file__).resolve().parent.parent
TENANT = "telco_001"
AS_OF = "2026-10-08"


def dump(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def obj(props, required=None):
    return {"type": "object", "properties": props, "required": required or list(props), "additionalProperties": False}


S = {"type": "string"}
N = {"type": "number"}
B = {"type": "boolean"}
IDENT = obj({"msisdn": S, "nid_last4": S})


def tool(name, title, desc, kind, risk, inp, out, requires_identity=True, personal=True, human_only=False, enabled=True):
    return dict(name=name, capability=name, title=title, description=desc, operation_kind=kind, risk=risk,
                requires_identity=requires_identity, exposes_personal_data=personal, human_only=human_only, enabled=enabled,
                input_schema=inp, output_schema=out)


TOOLS = [
    tool("verify_customer", "Verify customer", "Check that a line number (or home landline) and the last 4 national ID digits belong to the same owner.",
         "read", "low", obj({"msisdn": S, "nid_last4": S}), obj({"verified": B, "customer_id": {"type": ["string", "null"]}}), requires_identity=False),
    tool("get_line", "Get line", "Line facts: plan, status and reason, balance, roaming, Salefny outstanding, activation date.", "read", "low",
         obj({"msisdn": S}), obj({"line": {"type": "object"}})),
    tool("list_line_bundles", "List bundles", "Active and recent bundles on a line with quota used and expiry.", "read", "low",
         obj({"msisdn": S}), obj({"bundles": {"type": "array"}})),
    tool("list_transactions", "List transactions", "Recharges, bundle purchases, VAS charges, transfers, refunds on a line for a date range.", "read", "low",
         obj({"msisdn": S, "from": S, "to": S}, ["msisdn"]), obj({"transactions": {"type": "array"}})),
    tool("get_invoice", "Get invoice", "Latest (or a given) postpaid invoice with line items, due date and paid amount.", "read", "low",
         obj({"msisdn": S, "invoice_id": S}, ["msisdn"]), obj({"invoice": {"type": "object"}})),
    tool("get_usage_summary", "Usage summary", "Daily totals of data, minutes and SMS (never the numbers called).", "read", "low",
         obj({"msisdn": S, "days": N}, ["msisdn"]), obj({"usage": {"type": "array"}})),
    tool("check_outage", "Check outage", "Open network outages in the customer's governorate and city.", "read", "low",
         obj({"governorate": S, "city": S}), obj({"outages": {"type": "array"}}), requires_identity=False, personal=False),
    tool("get_appointment", "Get appointment", "Technician appointments for a home internet line.", "read", "low",
         obj({"landline": S}), obj({"appointments": {"type": "array"}})),
    tool("get_puk", "Get PUK", "PUK code of the verified owner's SIM.", "read", "medium", obj({"msisdn": S}), obj({"puk": S})),
    tool("subscribe_bundle", "Subscribe bundle", "Subscribe a line to a bundle; price taken from balance or added to the bill.", "create", "medium",
         obj({"msisdn": S, "bundle_id": S}), obj({"subscription_id": S, "status": S})),
    tool("refund_bundle", "Refund bundle", "Cancel a bundle subscribed by mistake and return its price (24h, under 5% used).", "create", "medium",
         obj({"msisdn": S, "subscription_id": S}), obj({"refund_id": S, "amount": N, "status": S})),
    tool("refund_charge", "Refund charge", "Return a charge confirmed as wrong or duplicated (agent limit 200 EGP).", "create", "high",
         obj({"msisdn": S, "txn_id": S, "amount": N}), obj({"refund_id": S, "amount": N, "status": S})),
    tool("unsubscribe_vas", "Unsubscribe service", "Stop a value-added content service.", "update", "low",
         obj({"msisdn": S, "vas_sub_id": S}), obj({"reference": S, "status": S})),
    tool("refund_vas", "Refund service charges", "Refund value-added service charges for up to the last 30 days.", "create", "medium",
         obj({"msisdn": S, "vas_sub_id": S, "refund_days": N}), obj({"refund_id": S, "amount": N, "status": S})),
    tool("open_bill_dispute", "Open bill dispute", "Open a dispute on an invoice line item (adjustments over 500 EGP need a supervisor).", "create", "high",
         obj({"msisdn": S, "invoice_id": S, "item_id": S, "amount": N}), obj({"dispute_id": S, "status": S})),
    tool("suspend_line", "Suspend line", "Suspend a line after loss or theft.", "update", "high",
         obj({"msisdn": S, "reason": S}), obj({"reference": S, "status": S})),
    tool("activate_roaming", "Activate roaming", "Turn roaming on for a line.", "update", "medium", obj({"msisdn": S}), obj({"status": S})),
    tool("change_plan", "Change plan", "Move a line to another plan (upgrade now, downgrade at renewal).", "update", "medium",
         obj({"msisdn": S, "plan_id": S}), obj({"effective_at": S, "status": S})),
    tool("grant_salefny", "Salefny", "Lend 5, 10 or 20 EGP emergency credit.", "create", "low", obj({"msisdn": S, "amount": N}), obj({"loan_id": S, "status": S})),
    tool("issue_port_out", "Port-out code", "Issue a number-portability code for porting out.", "create", "medium", obj({"msisdn": S}), obj({"port_code": S, "status": S})),
    tool("create_complaint", "Create complaint", "Open a complaint and return its CMP- reference.", "create", "low",
         obj({"msisdn": S, "category": S, "summary": S}), obj({"complaint_id": S, "sla_due": S})),
    tool("schedule_technician", "Schedule technician", "Book a technician visit for a home internet fault.", "create", "medium",
         obj({"landline": S, "reason": S, "date": S, "window": S}), obj({"appointment_id": S, "status": S})),
    tool("sim_swap", "SIM replacement", "Issue a replacement SIM. Store only, owner in person.", "update", "critical",
         obj({"msisdn": S, "new_iccid": S}), obj({"status": S}), human_only=True, enabled=False),
    tool("cancel_line", "Cancel line", "Terminate a line. Store only.", "delete", "critical", obj({"msisdn": S}), obj({"status": S}),
         human_only=True, enabled=False),
]


def intent(kind, desc, examples, slots=(), lookup=None, action=None, query=None, labels=None):
    d = {"kind": kind, "description": desc, "examples": examples, "required_slots": list(slots), "argument_map": {}}
    if lookup:
        d["lookup_tool"] = lookup
    if action:
        d["action_tool"] = action
    if query:
        d["knowledge_query"] = query
    if labels:
        d["labels"] = labels
    return d


def ex(name):
    o = INTENTS[name]["openers"]
    return [o["en"][0], o["ar"][0], o["arabizi"][0], o["mixed"][0]]


TENANT_CONFIG = {
    "tenant_id": TENANT, "display_name": BRAND_EN, "display_name_ar": BRAND_AR, "domain": "telecom", "default_locale": "ar",
    "history_max_turns": 12, "managers": ["manager"],
    "line_id_pattern": "01[0125]\\d{8}", "landline_pattern": "0\\d{1,2}\\d{7,8}",
    "identity": {"verify_tool": "verify_customer", "required_slots": ["msisdn", "nid_last4"], "max_attempts": 2},
    "escalation": {"max_tool_failures": 2, "max_clarifications": 2, "min_intent_confidence": 0.55, "escalate_on_deny": False,
                   "escalate_on_high_frustration": True, "sla_minutes": {"urgent": 15, "high": 60, "normal": 240},
                   "final_deny_rules": ["R-BUNDLE-REFUND-24H", "R-BUNDLE-REFUND-USAGE", "R-PORT-OUT-60D", "R-SALEFNY-ONE",
                                        "R-SIM-SWAP-STORE", "R-LINE-CANCEL-STORE"]},
    "permissions": {"allowed_tools": [t["name"] for t in TOOLS if not t["human_only"]], "max_risk": "high",
                    "confirm_operation_kinds": ["create", "update", "delete"]},
    "intents": {
        "policy_question": intent("knowledge", "A question about plans, recharge, bills, roaming, SIM, porting or complaints that needs no account data.",
                                  ["How does Salefny work?", "الضرايب على الشحن كام؟", "el roaming byesht8al ezay?"]),
        "greeting": intent("smalltalk", "A greeting or thanks with no request.", ex("greeting")),
        "balance_inquiry": intent("lookup", "Balance, remaining bundle quota, line status.", ex("balance_inquiry"), lookup="get_line"),
        "bill_inquiry": intent("lookup", "What is on my bill and why.", ex("bill_inquiry"), lookup="get_invoice", query="bill dispute"),
        "line_suspended": intent("lookup", "Why the line can't call or is blocked.", ex("line_suspended"), lookup="get_line", query="credit limit suspension"),
        "internet_down": intent("lookup", "Home internet down or slow.", ex("home_internet_down") + ex("slow_internet")[:2], lookup="check_outage", query="home internet faults"),
        "bundle_subscribe": intent("action", "Subscribe to a bundle.", ex("bundle_subscribe"), ["bundle_id"], "get_line", "subscribe_bundle", "subscribe bundle",
                                   {"en": "subscribe to the bundle", "ar": "الاشتراك في الباقة", "arabizi": "el eshterak fel ba2a"}),
        "bundle_refund": intent("action", "Refund a bundle subscribed by mistake.", ex("bundle_refund"), [], "list_line_bundles", "refund_bundle", "bundle refund",
                                {"en": "refund the bundle", "ar": "استرداد قيمة الباقة", "arabizi": "estrdad el ba2a"}),
        "wrong_deduction": intent("action", "Money deducted wrongly or twice.", ex("wrong_deduction"), [], "list_transactions", "refund_charge", "wrong deduction refund",
                                  {"en": "refund the charge", "ar": "رد المبلغ", "arabizi": "rad el mablagh"}),
        "vas_unsubscribe": intent("action", "Stop a content service and refund it.", ex("vas_unsubscribe"), [], "list_transactions", "unsubscribe_vas", "value-added services refund",
                                  {"en": "stop the service", "ar": "إلغاء الخدمة", "arabizi": "elgha2 el khedma"}),
        "bill_dispute": intent("action", "Dispute a charge on a postpaid bill.", ex("bill_dispute"), [], "get_invoice", "open_bill_dispute", "bill dispute",
                               {"en": "open a dispute", "ar": "فتح اعتراض", "arabizi": "fat7 e3terad"}),
        "lost_or_stolen": intent("action", "Phone or SIM lost or stolen; suspend the line.", ex("sim_lost"), [], "get_line", "suspend_line", "lost stolen phone",
                                 {"en": "suspend the line", "ar": "إيقاف الخط", "arabizi": "ee2af el 5at"}),
        "roaming_activation": intent("action", "Turn roaming on before travel.", ex("roaming_activation"), [], "get_line", "activate_roaming", "roaming activation",
                                     {"en": "turn on roaming", "ar": "تفعيل التجوال", "arabizi": "taf3eel el roaming"}),
        "plan_change": intent("action", "Upgrade or downgrade the plan.", ex("plan_change"), ["plan_id"], "get_line", "change_plan", "change plan",
                              {"en": "change the plan", "ar": "تغيير النظام", "arabizi": "ta8yeer el nezam"}),
        "puk_request": intent("lookup", "PUK code for a locked SIM.", ex("puk_request"), lookup="get_puk", query="puk code"),
        "salefny": intent("action", "Emergency credit.", ex("salefny"), ["amount"], "get_line", "grant_salefny", "salefny emergency credit",
                          {"en": "take Salefny", "ar": "سلفني", "arabizi": "salefny"}),
        "port_out": intent("action", "Move the number to another network.", ex("port_out"), [], "get_line", "issue_port_out", "port out",
                           {"en": "port out", "ar": "نقل الرقم", "arabizi": "na2l el ra2am"}),
        "sim_swap_request": intent("refuse", "SIM replacement or swap requested in chat (store only).", ex("sim_swap_request"), query="sim replacement"),
        "cancel_line": intent("refuse", "Cancel the line (store only).", ex("cancel_line"), query="cancel line"),
        "call_log_request": intent("refuse", "Detailed call records (store only).", ex("call_log_request"), query="call records"),
        "third_party_request": intent("refuse", "Account details of someone else's line.", ex("third_party_request"), query="third parties"),
        "fraud_report": intent("handoff", "Scam, fraud or unauthorised SIM swap.", ex("fraud_report")),
        "human_request": intent("handoff", "Asks for a person or a manager.", ex("complaint_escalation")),
    },
}


def network_fixture(con, q, demo_ids):
    """The equipment around the demo customers, their bindings, open alarms, ongoing outages, the playbook and the demo faults,
    so troubleshooting tests run without telecom.db."""
    marks = ",".join("?" * len(demo_ids))
    bindings = q(f"SELECT b.* FROM line_network_bindings b JOIN lines l USING(line_id) WHERE l.customer_id IN ({marks}) AND b.valid_to IS NULL "
                 "ORDER BY b.line_id, b.role", *demo_ids)
    ids = {b["element_id"] for b in bindings}
    ids |= {r["element_id"] for r in q("SELECT element_id FROM network_elements WHERE site_id = 'SITE-CAI-DT'")}  # demo cluster
    ids |= {r["element_id"] for r in q("SELECT element_id FROM network_alarms WHERE cleared_at IS NULL")}
    ids |= {r["element_id"] for r in q("SELECT element_id FROM outage_elements JOIN network_outages USING(outage_id) WHERE status = 'ongoing'")}
    serving = [b["element_id"] for b in bindings if b["role"] == "serving_cell"]
    ids |= {r["element_id"] for r in q(f"SELECT n.element_id FROM network_elements n JOIN network_elements s ON s.site_id = n.site_id "  # neighbours
                                        f"WHERE s.element_id IN ({','.join('?' * len(serving))}) AND n.element_type IN ('gnb_cell','enb_cell')", *serving)}
    while True:  # everything upstream: parents, uplinks and the core functions next to them
        idl = sorted(ids)
        m = ",".join("?" * len(idl))
        more = {r["x"] for r in q(f"SELECT parent_element_id AS x FROM network_elements WHERE element_id IN ({m}) AND parent_element_id IS NOT NULL", *idl)}
        more |= {r["x"] for r in q(f"SELECT b_element_id AS x FROM network_links WHERE a_element_id IN ({m}) AND link_type IN ('backhaul','fiber','N2','N3')", *idl)}
        more |= {r["x"] for r in q(f"SELECT c.element_id AS x FROM network_elements c JOIN network_elements n ON n.site_id = c.site_id "
                                    f"WHERE n.element_id IN ({m}) AND n.element_type = 'core_nf' AND c.element_type = 'core_nf'", *idl)}
        if more <= ids:
            break
        ids |= more
    idl = sorted(ids)
    m = ",".join("?" * len(idl))
    elements = q(f"SELECT * FROM network_elements WHERE element_id IN ({m}) ORDER BY element_id", *idl)
    for e in elements:
        e["attributes"] = json.loads(e.pop("attributes_json"))
    js = lambda rows, *cols: [{**r, **{c: json.loads(r[c]) for c in cols}} for r in rows]
    outages = q("SELECT * FROM network_outages WHERE status = 'ongoing' ORDER BY outage_id")
    for o in outages:
        o["element_ids"] = [r["element_id"] for r in q("SELECT element_id FROM outage_elements WHERE outage_id = ? ORDER BY 1", o["outage_id"])]
    line_of = {r["customer_id"]: r for r in q(f"SELECT customer_id, line_id, msisdn FROM lines WHERE customer_id IN ({marks})", *demo_ids)}
    return {"tenant_id": TENANT, "as_of": AS_OF,
            "note": "Equipment serving the demo customers and everything upstream of it; the full network is in telecom.db.",
            "sites": q(f"SELECT * FROM network_sites WHERE site_id IN (SELECT site_id FROM network_elements WHERE element_id IN ({m})) ORDER BY 1", *idl),
            "elements": elements,
            "interfaces": q(f"SELECT * FROM element_interfaces WHERE element_id IN ({m}) ORDER BY 1", *idl),
            "links": q(f"SELECT * FROM network_links WHERE a_element_id IN ({m}) AND b_element_id IN ({m}) ORDER BY 1", *idl, *idl),
            "bindings": bindings,
            "alarms": q("SELECT * FROM network_alarms WHERE cleared_at IS NULL ORDER BY alarm_id"),
            "outages": outages,
            "playbook": {"root_causes": q("SELECT * FROM root_cause_catalog ORDER BY cause_code"),
                         "procedures": js(q("SELECT * FROM resolution_procedures WHERE status = 'approved' ORDER BY procedure_id, version"),
                                          "steps_json", "verify_checks_json")},
            "demo_faults": [{"customer_id": d["customer_id"], "line_id": line_of[d["customer_id"]]["line_id"], "msisdn": d["msisdn"],
                             "symptom": d["symptom"], "fault": d["fault"], "note": d["note"], "measurements": d["measurements"],
                             "expected": d["expected"]} for d in DEMO_NET]}


def main():
    con = sqlite3.connect(OUT / "telecom.db")
    con.row_factory = sqlite3.Row
    q = lambda sql, *a: [dict(r) for r in con.execute(sql, a)]

    # ---- policies as readable documents ----
    pol_dir = OUT / "corpus"  # the knowledge service reads the policies as this tenant's RAG corpus
    for doc_id, ver, ten, tar, sections in POLICIES:
        md = [f"# {ten} — {tar}", "", f"{BRAND_EN} ({BRAND_AR}) · version {ver} · effective {EFFECTIVE}", "",
              "_Fictional brand. The rules follow real Egyptian operators' and NTRA's public terms; each section lists what it is based on._", ""]
        for i, (se, sa, te, ta, _, src) in enumerate(sections, 1):
            basis = "; ".join(f"[{SOURCES[k][0]}]({SOURCES[k][1]})" for k in src) if src else "Masr Connect's own internal rule (no operator publishes this)."
            md += [f"## {i}. {se} — {sa}", "", f"`{doc_id}@{ver}#s{i}`", "", te, "", f'<div dir="rtl">{ta}</div>', "", f"Based on: {basis}", ""]
        (pol_dir / f"{doc_id}.md").parent.mkdir(parents=True, exist_ok=True)
        (pol_dir / f"{doc_id}.md").write_text("\n".join(md), encoding="utf-8", newline="\n")

    faq_rows = q("SELECT * FROM faq ORDER BY faq_id")
    dump(OUT / "faq.json", faq_rows)
    md = [f"# {BRAND_EN} FAQ — الأسئلة الشائعة", ""]
    cat = None
    for r in faq_rows:
        if r["category"] != cat:
            cat = r["category"]
            md += [f"## {cat.replace('_', ' ').title()}", ""]
        md += [f"**{r['question_en']}**  ", f"{r['question_ar']} · _{r['question_arabizi']}_", "", r["answer_en"], "",
               f'<div dir="rtl">{r["answer_ar"]}</div>', "", f"Source: `{r['citation']}`", ""]
    (OUT / "faq.md").write_text("\n".join(md), encoding="utf-8", newline="\n")

    # ---- the agent shaped fixtures ----
    fx = OUT / "fixtures" / TENANT
    passages = []
    for doc_id, ver, ten, tar, sections in POLICIES:
        for i, (se, sa, te, ta, kw, src) in enumerate(sections, 1):
            for lang, title, text in (("en", se, te), ("ar", sa, ta)):
                passages.append({"passage_id": f"{doc_id}@{ver}#s{i}" + ("" if lang == "en" else ".ar"), "document_id": doc_id, "version": ver,
                                 "section": f"{i}. {title}", "language": lang, "text": text, "keywords": kw, "superseded": False,
                                 "effective_date": EFFECTIVE, "based_on": [SOURCES[k][1] for k in src]})
    dump(fx / "policies.json", {"tenant_id": TENANT,
                                "documents": [{"document_id": d, "version": v, "effective_date": EFFECTIVE, "current": True, "language": "en+ar"}
                                              for d, v, *_ in POLICIES] + [{"document_id": "faq", "version": "v1", "effective_date": EFFECTIVE, "current": True, "language": "mixed"}],
                                "passages": passages,
                                "faq": [{"faq_id": r["faq_id"], "question": {"en": r["question_en"], "ar": r["question_ar"], "arabizi": r["question_arabizi"]},
                                         "answer": {"en": r["answer_en"], "ar": r["answer_ar"]}, "citation": r["citation"]} for r in faq_rows]})
    dump(fx / "rules.json", {"tenant_id": TENANT,
                             "description": "Same rule shape as shop_001: a rule yields `effect` when applies_if and all conditions hold, otherwise `else_effect`.",
                             "derived_facts": {"hours_since_subscription": "now minus line_bundles.subscribed_at, in hours",
                                               "quota_used_percent": "data_mb_used / data_mb_total * 100",
                                               "line_age_days": "as_of minus lines.activated_at",
                                               "days_since_invoice": "as_of minus invoices.issued_at",
                                               "unpaid_invoices": "1 if the latest past-due invoice is not fully paid, else 0",
                                               "paid_invoices": "count of invoices with status paid or paid_late",
                                               "prepaid_balance": "lines.prepaid_balance",
                                               "charge_confirmed_wrong": "transactions.is_duplicate = 1 or a confirmed system error",
                                               "prepaid_balance_minus_price": "lines.prepaid_balance minus bundle price"},
                             "rules": RULES})
    dump(fx / "tools.json", {"tenant_id": TENANT, "tools": TOOLS})

    demo_ids = [d["customer"]["customer_id"] for d in DEMO] + [d["customer_id"] for d in DEMO_NET]
    marks = ",".join("?" * len(demo_ids))
    backend = {"tenant_id": TENANT, "as_of": AS_OF,
               "note": "Only the 30 hand-made demo customers; the full data set is in telecom.db.",
               "customers": q(f"SELECT customer_id, first_name_en || ' ' || last_name_en AS name, full_name_ar AS name_ar, nid_last4, preferred_language, governorate, city, demo_note FROM customers WHERE customer_id IN ({marks})", *demo_ids),
               "accounts": q(f"SELECT * FROM accounts WHERE customer_id IN ({marks})", *demo_ids),
               "lines": q(f"SELECT * FROM lines WHERE customer_id IN ({marks})", *demo_ids),
               "bundles": q(f"SELECT b.* FROM line_bundles b JOIN lines l USING(line_id) WHERE l.customer_id IN ({marks}) AND b.status='active'", *demo_ids),
               "vas_subscriptions": q(f"SELECT v.* FROM vas_subscriptions v JOIN lines l USING(line_id) WHERE l.customer_id IN ({marks})", *demo_ids),
               "invoices": q(f"SELECT * FROM invoices WHERE customer_id IN ({marks}) AND issued_at >= '2026-08-01'", *demo_ids),
               "invoice_items": q(f"SELECT i.* FROM invoice_items i JOIN invoices v USING(invoice_id) WHERE v.customer_id IN ({marks}) AND v.issued_at >= '2026-08-01'", *demo_ids),
               "recent_transactions": q(f"SELECT * FROM transactions WHERE customer_id IN ({marks}) AND created_at >= '2026-09-24' AND txn_type != 'vas_charge' ORDER BY created_at", *demo_ids),
               "device_installments": q(f"SELECT d.* FROM device_installments d JOIN accounts a USING(account_id) WHERE a.customer_id IN ({marks})", *demo_ids),
               "appointments": q("SELECT * FROM technician_appointments WHERE appointment_id LIKE 'TA-D%'"),
               "outages": q("SELECT * FROM network_outages WHERE status='ongoing'")}
    dump(fx / "backend.json", backend)
    dump(fx / "network.json", network_fixture(con, q, demo_ids))
    dump(OUT / "config" / "tenants" / f"{TENANT}.json", TENANT_CONFIG)

    # ---- scenarios (fill values that come from generated data) ----
    d6 = con.execute("SELECT outstanding_balance FROM accounts WHERE customer_id='TC-D006' AND account_type='postpaid'").fetchone()[0]
    sc_dir = OUT / "scenarios" / TENANT
    for s in SCENARIOS:
        s = json.loads(json.dumps(s))
        if s["id"] == "T13":
            s["turns"][1]["expect"]["text_contains"] = f"{d6:g}"
        dump(sc_dir / f"{s['id']}.json", s)
    print(f"policies {len(POLICIES)}, passages {len(passages)}, faq {len(faq_rows)}, rules {len(RULES)}, tools {len(TOOLS)}, scenarios {len(SCENARIOS)}")


if __name__ == "__main__":
    main()
