"""Consistency checks for telecom.db. Run: python3 verify.py [path/to/telecom.db]. Exits non-zero on any failure."""
import re
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

db = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).resolve().parent.parent / "telecom.db")
con = sqlite3.connect(db)
one = lambda sql, *a: con.execute(sql, a).fetchone()[0]
fails = []


def check(name, ok):
    print(("PASS " if ok else "FAIL ") + name)
    if not ok:
        fails.append(name)


check("foreign keys hold", not con.execute("PRAGMA foreign_key_check").fetchall())
check("line owner matches account owner", one("SELECT COUNT(*) FROM lines l JOIN accounts a USING(account_id) WHERE l.customer_id != a.customer_id") == 0)
check("every mobile number is a valid Egyptian format",
      all(re.fullmatch(r"01[0125]\d{8}", m) for (m,) in con.execute("SELECT msisdn FROM lines WHERE msisdn IS NOT NULL")))
check("national IDs are 14 digits (passports aside)",
      all(re.fullmatch(r"[23]\d{13}", n) for (n,) in con.execute("SELECT national_id FROM customers WHERE id_type='national_id'")))
check("nid_last4 matches national ID", one("SELECT COUNT(*) FROM customers WHERE substr(national_id, -4) != nid_last4") == 0)
check("invoice subtotal = sum of items",
      one("SELECT COUNT(*) FROM invoices i WHERE abs(subtotal - (SELECT SUM(amount) FROM invoice_items WHERE invoice_id=i.invoice_id)) > 0.05") == 0)
check("invoice total = subtotal + 14% tax", one("SELECT COUNT(*) FROM invoices WHERE abs(total - round(subtotal*1.14, 2)) > 0.05") == 0)
check("recharge credit is 91% of amount", one("SELECT COUNT(*) FROM transactions WHERE txn_type='recharge' AND status='succeeded' AND abs(net_credit - amount*0.91) > 0.01") == 0)
check("no activity after the demo's current time", one("SELECT COUNT(*) FROM transactions WHERE created_at > '2026-10-08T15:00:00'") == 0
      and one("SELECT COUNT(*) FROM conversation_messages WHERE sent_at > '2026-10-08T15:00:00'") == 0
      and one("SELECT COUNT(*) FROM complaints WHERE resolved_at > '2026-10-08T15:00:00' OR created_at > '2026-10-08T15:00:00'") == 0)
check("FAQ cites existing policy passages", one("SELECT COUNT(*) FROM faq WHERE citation NOT IN (SELECT passage_id FROM policy_sections)") == 0)
check("all four languages present in conversations", one("SELECT COUNT(DISTINCT language) FROM conversations") == 4)
check("refunds above 200 EGP were supervisor-approved", one("SELECT COUNT(*) FROM refunds WHERE amount > 200 AND approved_by != 'supervisor'") == 0)
check("terminated lines have no active bundles",
      one("SELECT COUNT(*) FROM line_bundles b JOIN lines l USING(line_id) WHERE l.status='terminated' AND b.status='active'") == 0)

# demo customers match the facts their scenarios rely on
now = datetime(2026, 10, 8, 15, 0)
sub = lambda sid: con.execute("SELECT subscribed_at, data_mb_used*100.0/data_mb_total FROM line_bundles WHERE subscription_id=?", (sid,)).fetchone()
s3, s4 = sub("SUB-D003"), sub("SUB-D004")
check("D003 bundle refundable (<24h, <5%)", (now - datetime.fromisoformat(s3[0])).total_seconds() < 86400 and s3[1] < 5)
check("D004 bundle not refundable", (now - datetime.fromisoformat(s4[0])).total_seconds() > 86400 and s4[1] >= 5)
check("D009 has one duplicated 75 EGP charge", one("SELECT COUNT(*) FROM transactions WHERE customer_id='TC-D009' AND is_duplicate=1 AND amount=-75") == 1)
check("D017 has one duplicated 300 EGP charge", one("SELECT COUNT(*) FROM transactions WHERE customer_id='TC-D017' AND is_duplicate=1 AND amount=-300") == 1)
check("D011 line owned less than 4 months", (date(2026, 10, 8) - date.fromisoformat(one("SELECT activated_at FROM lines WHERE msisdn='01512340011'"))).days < 120)
check("D015 has Salefny outstanding", one("SELECT salefny_outstanding FROM lines WHERE msisdn='01512340015'") > 0)
check("D010 has fewer than 4 paid bills and roaming off", one("SELECT COUNT(*) FROM invoices WHERE customer_id='TC-D010' AND status IN ('paid','paid_late')") < 4
      and one("SELECT roaming_enabled FROM lines WHERE msisdn='01212340010'") == 0)
check("D012 latest bill has 650 EGP content charge", one("SELECT COUNT(*) FROM invoice_items WHERE amount=650 AND item_type='content_charge' AND invoice_id IN (SELECT invoice_id FROM invoices WHERE customer_id='TC-D012')") == 1)
check("D006 oldest unpaid bill is more than 30 days past due",
      (date(2026, 10, 8) - date.fromisoformat(one("SELECT MIN(due_date) FROM invoices WHERE customer_id='TC-D006' AND status='overdue'"))).days > 30)
check("D007 area has an ongoing outage", one("SELECT COUNT(*) FROM network_outages WHERE outage_id='OUT-D007' AND status='ongoing'") == 1)
check("D016 is in the receive-only window (90-105 days idle)",
      90 < (date(2026, 10, 8) - date.fromisoformat(one("SELECT MAX(substr(created_at,1,10)) FROM transactions WHERE customer_id='TC-D016' AND txn_type IN ('recharge','bundle_purchase')"))).days < 105)
check("D014 device locked", one("SELECT device_locked FROM device_installments d JOIN accounts a USING(account_id) WHERE a.customer_id='TC-D014'") == 1)

# every rule quotes its cited policy text word for word, and every policy source is listed
sys.path.insert(0, str(Path(__file__).parent))
from content_policies import POLICIES, SOURCES  # noqa: E402
from demo_and_rules import RULES  # noqa: E402
text = {f"{d}@{v}#s{i}": s[2] for d, v, _, _, secs in POLICIES for i, s in enumerate(secs, 1)}
check("rule quotes match their cited policy text", all(r["citation"] in text and r["quote"] in text[r["citation"]] for r in RULES))
check("policy sources all exist", all(k in SOURCES for *_, secs in POLICIES for s in secs for k in s[5]))
check("salefny fee taken on receipt", one("SELECT COUNT(*) FROM transactions WHERE txn_type='salefny_loan' AND net_credit >= amount") == 0)

# network troubleshooting (migrations/ + net_generate.py)
if one("SELECT COUNT(*) FROM sqlite_master WHERE name='line_network_bindings'"):
    current = lambda role: f"SELECT COUNT(*) FROM line_network_bindings b WHERE b.line_id=l.line_id AND b.role='{role}' AND b.valid_to IS NULL"
    check("every active mobile line has exactly one current serving cell",
          one(f"SELECT COUNT(*) FROM lines l WHERE l.status='active' AND l.line_type!='home_internet' AND ({current('serving_cell')}) != 1") == 0)
    check("every active home internet line has one access port and one home router",
          one(f"SELECT COUNT(*) FROM lines l WHERE l.status='active' AND l.line_type='home_internet' "
              f"AND (({current('access_port')}) != 1 OR ({current('cpe')}) != 1)") == 0)
    check("approved procedures on shared equipment need a person's approval",
          one("SELECT COUNT(*) FROM resolution_procedures WHERE status='approved' AND target_scope!='single_line' AND needs_human_approval!=1") == 0)
    check("every outage is linked to equipment",
          one("SELECT COUNT(*) FROM network_outages o WHERE NOT EXISTS (SELECT 1 FROM outage_elements e WHERE e.outage_id=o.outage_id)") == 0)
    check("every procedure's cause is in the catalogue",
          one("SELECT COUNT(*) FROM resolution_procedures WHERE cause_code NOT IN (SELECT cause_code FROM root_cause_catalog)") == 0)
    check("every cause has English and Arabic customer wording",
          one("SELECT COUNT(*) FROM root_cause_catalog WHERE trim(coalesce(customer_text_en,''))='' OR trim(coalesce(customer_text_ar,''))=''") == 0)
else:
    check("network tables present (run net_generate.py)", False)

print(f"\n{len(fails)} failed" if fails else "\nall checks passed")
sys.exit(1 if fails else 0)
