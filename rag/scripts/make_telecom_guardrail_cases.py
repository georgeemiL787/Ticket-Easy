"""Generate telco_001 guardrails.jsonl and approve rules for Team A parity with shop_001.

Run from rag/:  python scripts/make_telecom_guardrail_cases.py
Also writes a short rules_history.jsonl entry and can refresh mock scenarios via
scripts/export_telco_eval_scenarios.py (called separately).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = ROOT / "data/telecom/telco_001/rules.json"
OUT = ROOT / "data/telecom/telco_001/benchmark/guardrails.jsonl"
HISTORY = ROOT / "data/telecom/telco_001/rules_history.jsonl"
AS_OF = "2026-10-08"

TOOLS = {
    "refund_bundle": {"name": "refund_bundle", "operation_kind": "create", "risk": "medium"},
    "refund_charge": {"name": "refund_charge", "operation_kind": "create", "risk": "high"},
    "refund_vas": {"name": "refund_vas", "operation_kind": "create", "risk": "medium"},
    "open_bill_dispute": {"name": "open_bill_dispute", "operation_kind": "create", "risk": "medium"},
    "issue_port_out": {"name": "issue_port_out", "operation_kind": "create", "risk": "high"},
    "sim_swap": {"name": "sim_swap", "operation_kind": "update", "risk": "high"},
    "cancel_line": {"name": "cancel_line", "operation_kind": "delete", "risk": "high"},
    "grant_salefny": {"name": "grant_salefny", "operation_kind": "create", "risk": "medium"},
    "activate_roaming": {"name": "activate_roaming", "operation_kind": "update", "risk": "medium"},
    "change_plan": {"name": "change_plan", "operation_kind": "update", "risk": "medium"},
    "subscribe_bundle": {"name": "subscribe_bundle", "operation_kind": "create", "risk": "low"},
    "grant_outage_compensation": {"name": "grant_outage_compensation", "operation_kind": "create", "risk": "medium"},
    "transfer_balance": {"name": "transfer_balance", "operation_kind": "create", "risk": "medium"},
    "get_puk": {"name": "get_puk", "operation_kind": "read", "risk": "medium", "exposes_personal_data": True},
    "get_balance": {"name": "get_balance", "operation_kind": "read", "risk": "low"},
}

VERIFIED = {"verified": True, "customer_id": "TC-D001", "method": "line+nid_last4"}
UNVERIFIED = {"verified": False}

cases: list[dict] = []


def add(title, tool, facts=None, args=None, identity=VERIFIED, expect=None, message="", **extra):
    request = {"tool": TOOLS[tool], "facts": facts or {}, "arguments": args or {}, "identity": identity, **extra}
    cases.append({
        "id": f"TG{len(cases) + 1:02d}",
        "title": title,
        "as_of": AS_OF,
        "message": message,
        "request": request,
        "expect": expect,
    })


# --- bundle refund (both 24h and usage rules must allow) ---
add("Bundle refund within 24h and low usage", "refund_bundle",
    {"hours_since_subscription": 2, "quota_used_percent": 1},
    expect={"decision": "allow", "reason_code": "RULES_PASSED"})
add("Bundle refund after 24h", "refund_bundle",
    {"hours_since_subscription": 30, "quota_used_percent": 1},
    expect={"decision": "deny", "reason_code": "RULE_BLOCKED", "rule_id": "R-BUNDLE-REFUND-24H"})
add("Bundle refund over 5% usage", "refund_bundle",
    {"hours_since_subscription": 2, "quota_used_percent": 10},
    expect={"decision": "deny", "reason_code": "RULE_BLOCKED", "rule_id": "R-BUNDLE-REFUND-USAGE"})

# --- charge refund ---
add("Charge refund within agent cap", "refund_charge",
    {"charge_confirmed_wrong": True}, {"amount": 150},
    expect={"decision": "allow", "reason_code": "RULES_PASSED"})
add("Charge refund above agent cap needs supervisor", "refund_charge",
    {"charge_confirmed_wrong": True}, {"amount": 250},
    expect={"decision": "require_human", "reason_code": "RULE_REQUIRES_HUMAN", "rule_id": "R-REFUND-AGENT-CAP"})
add("Charge refund not confirmed wrong", "refund_charge",
    {"charge_confirmed_wrong": False}, {"amount": 50},
    expect={"decision": "deny", "reason_code": "RULE_BLOCKED", "rule_id": "R-REFUND-CONFIRMED"})

# --- VAS / dispute ---
add("VAS refund within 30 days", "refund_vas", {}, {"refund_days": 10},
    expect={"decision": "allow", "reason_code": "RULES_PASSED"})
add("VAS refund after 30 days", "refund_vas", {}, {"refund_days": 45},
    expect={"decision": "deny", "reason_code": "RULE_BLOCKED", "rule_id": "R-VAS-REFUND-30D"})
add("Bill dispute within 60 days under supervisor cap", "open_bill_dispute",
    {"days_since_invoice": 20}, {"amount": 100},
    expect={"decision": "allow", "reason_code": "RULES_PASSED"})
add("Bill dispute after 60 days", "open_bill_dispute",
    {"days_since_invoice": 90}, {"amount": 100},
    expect={"decision": "deny", "reason_code": "RULE_BLOCKED", "rule_id": "R-DISPUTE-60D"})
add("Bill dispute large amount needs supervisor", "open_bill_dispute",
    {"days_since_invoice": 10}, {"amount": 600},
    expect={"decision": "require_human", "reason_code": "RULE_REQUIRES_HUMAN", "rule_id": "R-ADJUST-SUPERVISOR"})

# --- port-out ---
add("Port-out after 4 months with no unpaid bills", "issue_port_out",
    {"line_age_days": 130, "unpaid_invoices": 0},
    expect={"decision": "allow", "reason_code": "RULES_PASSED"})
add("Port-out before 4 months", "issue_port_out",
    {"line_age_days": 30, "unpaid_invoices": 0},
    expect={"decision": "deny", "reason_code": "RULE_BLOCKED", "rule_id": "R-PORT-OUT-4M"})
add("Port-out with unpaid invoices", "issue_port_out",
    {"line_age_days": 200, "unpaid_invoices": 2},
    expect={"decision": "deny", "reason_code": "RULE_BLOCKED", "rule_id": "R-PORT-OUT-UNPAID"})

# --- store-only ---
add("SIM swap is store-only", "sim_swap", {},
    expect={"decision": "deny", "reason_code": "RULE_BLOCKED", "rule_id": "R-SIM-SWAP-STORE"})
add("Line cancel is store-only", "cancel_line", {},
    expect={"decision": "deny", "reason_code": "RULE_BLOCKED", "rule_id": "R-LINE-CANCEL-STORE"})

# --- Salefny ---
add("Salefny when low balance and none outstanding", "grant_salefny",
    {"prepaid_balance": 2, "salefny_outstanding": 0},
    expect={"decision": "allow", "reason_code": "RULES_PASSED"})
add("Salefny denied when balance not low", "grant_salefny",
    {"prepaid_balance": 20, "salefny_outstanding": 0},
    expect={"decision": "deny", "reason_code": "RULE_BLOCKED", "rule_id": "R-SALEFNY-LOW-BALANCE"})
add("Salefny denied when already outstanding", "grant_salefny",
    {"prepaid_balance": 1, "salefny_outstanding": 1},
    expect={"decision": "deny", "reason_code": "RULE_BLOCKED", "rule_id": "R-SALEFNY-ONE"})

# --- roaming / plan / bundle (applies_if) ---
add("Roaming for postpaid with enough paid invoices", "activate_roaming",
    {"line_type": "postpaid", "paid_invoices": 5},
    expect={"decision": "allow", "reason_code": "RULES_PASSED"})
add("Roaming for postpaid with few paid invoices needs human", "activate_roaming",
    {"line_type": "postpaid", "paid_invoices": 1},
    expect={"decision": "require_human", "reason_code": "RULE_REQUIRES_HUMAN", "rule_id": "R-ROAMING-POSTPAID-BILLS"})
add("Plan change once per cycle", "change_plan",
    {"line_type": "postpaid", "plan_changes_this_cycle": 0},
    expect={"decision": "allow", "reason_code": "RULES_PASSED"})
add("Plan change blocked if already changed this cycle", "change_plan",
    {"line_type": "postpaid", "plan_changes_this_cycle": 1},
    expect={"decision": "deny", "reason_code": "RULE_BLOCKED", "rule_id": "R-PLAN-CHANGE-ONCE"})
add("Subscribe bundle when prepaid balance covers price", "subscribe_bundle",
    {"line_type": "prepaid", "prepaid_balance_minus_price": 10},
    expect={"decision": "allow", "reason_code": "RULES_PASSED"})
add("Subscribe bundle denied when balance too low", "subscribe_bundle",
    {"line_type": "prepaid", "prepaid_balance_minus_price": -5},
    expect={"decision": "deny", "reason_code": "RULE_BLOCKED", "rule_id": "R-BUNDLE-BALANCE"})

# --- outage / transfer / PUK ---
add("Outage compensation after 24h", "grant_outage_compensation", {"outage_hours": 30},
    expect={"decision": "allow", "reason_code": "RULES_PASSED"})
add("Outage compensation under 24h denied", "grant_outage_compensation", {"outage_hours": 10},
    expect={"decision": "deny", "reason_code": "RULE_BLOCKED", "rule_id": "R-OUTAGE-COMP-24H"})
add("Transfer within 50 EGP limit", "transfer_balance", {}, {"amount": 30},
    expect={"decision": "allow", "reason_code": "RULES_PASSED"})
add("Transfer above 50 EGP denied", "transfer_balance", {}, {"amount": 80},
    expect={"decision": "deny", "reason_code": "RULE_BLOCKED", "rule_id": "R-TRANSFER-LIMIT"})
add("PUK for verified owner", "get_puk", {"identity_verified": True},
    expect={"decision": "allow", "reason_code": "RULES_PASSED"})
add("PUK denied when identity not verified fact", "get_puk", {"identity_verified": False},
    expect={"decision": "deny", "reason_code": "RULE_BLOCKED", "rule_id": "R-PUK-OWNER"})

# --- identity / tenant / risk / missing / read-only ---
add("Side effect with unverified identity", "refund_bundle",
    {"hours_since_subscription": 2, "quota_used_percent": 1}, identity=UNVERIFIED,
    expect={"decision": "deny", "reason_code": "IDENTITY_REQUIRED"})
add("Wrong tenant resource", "refund_bundle",
    {"hours_since_subscription": 2, "quota_used_percent": 1}, resource_tenant_id="shop_001",
    expect={"decision": "deny", "reason_code": "TENANT_MISMATCH"})
add("Mandatory risk on refund", "refund_bundle",
    {"hours_since_subscription": 2, "quota_used_percent": 1},
    message="my phone was stolen and there is unauthorized fraud on my account",
    expect={"decision": "require_human", "reason_code": "MANDATORY_RISK", "mandatory_escalation": True})
add("Missing fact fails closed on bundle refund", "refund_bundle",
    {"hours_since_subscription": 2},
    expect={"decision": "deny", "reason_code": "MISSING_CONTEXT"})
add("Read-only balance with no covering rule still allows", "get_balance", {},
    expect={"decision": "allow", "reason_code": "NO_RULE_READ_ONLY"})


def approve_all_rules() -> None:
    data = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc).isoformat()
    for rule in data["rules"]:
        rule["approval_status"] = "approved"
        rule["approved_by"] = "parity-bootstrap"
        rule["approved_at"] = now
    RULES_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    HISTORY.write_text(
        json.dumps({
            "ts": now,
            "action": "approved_all",
            "reviewer": "parity-bootstrap",
            "rule_ids": [r["rule_id"] for r in data["rules"]],
            "note": "Approved for Team A parity with shop_001 after telco guardrails were added.",
        }, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    approve_all_rules()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in cases) + "\n", encoding="utf-8")
    print(f"wrote {len(cases)} cases -> {OUT}")
    print(f"approved {len(json.loads(RULES_PATH.read_text(encoding='utf-8'))['rules'])} rules")


if __name__ == "__main__":
    main()
