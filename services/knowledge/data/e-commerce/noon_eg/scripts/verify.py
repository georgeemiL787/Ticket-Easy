"""Check the bulk noon_eg data before anyone uses it. Prints one line per check and exits 1 on any failure.

Usage:  python verify.py [--data <tenant folder with mock/ and tickets.jsonl>] [--db team_a.sqlite]
Defaults: the noon_eg folder this script lives in; the originals are read from its seed/ folder.
"""
import argparse
import json
import re
import runpy
import sqlite3
import sys
from collections import Counter
from datetime import date
from pathlib import Path

AS_OF = date(2026, 10, 8)
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))


def d(s):
    return date.fromisoformat(s) if s else None


def main():
    ap = argparse.ArgumentParser()
    tenant_dir = Path(__file__).resolve().parents[1]  # data/e-commerce/noon_eg
    service_root = tenant_dir.parents[2]
    ap.add_argument("--data", type=Path, default=tenant_dir)
    ap.add_argument("--db", type=Path)
    a = ap.parse_args()
    seed = tenant_dir / "seed"
    new = {k: json.loads((a.data / "mock" / f"noon_{k}.json").read_text(encoding="utf-8"))
           for k in ("customers", "orders", "returns", "scenarios")}
    old = {k: json.loads((seed / f"noon_{k}.json").read_text(encoding="utf-8")) for k in new}
    C, O, R, S = (new[k][k] for k in ("customers", "orders", "returns", "scenarios"))
    tk = lambda p: [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    T_new, T_old = tk(a.data / "tickets.jsonl"), tk(seed / "tickets.jsonl")
    ref = runpy.run_path(str(seed / "build_noon_mock_data.py"))
    decide = ref["decide_return"]
    vocab = json.loads((service_root / "data" / "shared" / "vocabulary.json").read_text(encoding="utf-8"))["facts"]
    headings = set(re.findall(r"^## (.+)$", (tenant_dir / "corpus" / "noon_egypt_return_policy.md")
                              .read_text(encoding="utf-8"), re.M))

    # 1 originals untouched and first
    check("hand-built records are unchanged and come first",
          all(new[k][k][: len(old[k][k])] == old[k][k] for k in new) and T_new[: len(T_old)] == T_old)
    check("_meta keeps tenant, as_of and currency", all(new[k]["_meta"][f] == old[k]["_meta"][f]
                                                         for k in new for f in ("tenant_id", "as_of", "currency")))
    # 2 uniqueness
    ids = lambda rows, key: len(rows) == len({r[key] for r in rows})
    items = [i for o in O for i in o["items"]]
    check("ids are unique", ids(C, "customer_id") and ids(O, "order_id") and ids(R, "return_id")
          and ids(S, "scenario_id") and ids(T_new, "ticket_id") and len(items) == len({i["item_id"] for i in items}))
    check("emails and phones are unique and fictional", ids(C, "email") and ids(C, "phone")
          and all(c["email"].endswith("@example.com") for c in C))
    # 3 references
    cust, orders = {c["customer_id"]: c for c in C}, {o["order_id"]: o for o in O}
    check("every order's customer exists", all(o["customer_id"] in cust for o in O))
    check("every return points at an order of the same customer",
          all(r["order_id"] in orders and orders[r["order_id"]]["customer_id"] == r["customer_id"] for r in R))
    check("every row is tenant noon_eg", all(r["tenant_id"] == "noon_eg" for rows in (C, O, R, T_new) for r in rows))
    # 4 money
    bad = [o["order_id"] for o in O if o["total_egp"] != sum(i["price_egp"] * i["qty"] for i in o["items"])
           + o["shipping_fee_egp"] + o["cod_fee_egp"]]
    check("order totals = items + shipping + COD fee", not bad, f"{len(bad)} off" if bad else "")
    gen_o = O[len(old["orders"]["orders"]):]
    check("COD fee is charged exactly on cash-on-delivery orders",
          all((o["cod_fee_egp"] == 20) == (o["payment_method"] == "cash_on_delivery") for o in gen_o))
    gen_r = R[len(old["returns"]["returns"]):]
    item_by_id = {i["item_id"]: (i, o) for o in O for i in o["items"]}
    ok = True
    for r in gen_r:
        it, o = item_by_id[r["notes"].split()[1]]
        if r.get("qc_result") == "passed" and r["refund_amount_egp"] != it["price_egp"] * it["qty"]:
            ok = False
        if r.get("qc_result", "").startswith("failed") and r["refund_amount_egp"] != 0:
            ok = False
    check("refund = the returned item's price (fees kept); failed QC refunds 0", ok)
    dest = {"credit_card": {"original_card"}, "credit_card_emi": {"original_card"}, "bnpl_tabby": {"bnpl_provider"},
            "cash_on_delivery": {"noon_wallet", "bank_account"}}
    check("refund destination follows the payment method (policy: refund destination section)",
          all(r["refund_destination"] in dest[r["payment_method"]] for r in gen_r if r.get("refund_destination")))
    # 5 dates
    ok, n_bad = True, 0
    for o in gen_o:
        p, e, dl = d(o["placed_at"]), d(o["expected_delivery_date"]), d(o["delivered_at"])
        member = d(cust[o["customer_id"]]["member_since"])
        if not (member <= p <= AS_OF and p <= e) or (dl and not p <= dl <= AS_OF):
            n_bad += 1
    check("order dates are in order and never after 2026-10-08", n_bad == 0, f"{n_bad} bad" if n_bad else "")
    n_bad = 0
    for r in gen_r:
        o = orders[r["order_id"]]
        seq = [d(o["delivered_at"]), d(r["requested_at"]), d(r.get("received_at")), d(r.get("refund_initiated_at")),
               d(r.get("refund_completed_at"))]
        seq = [x for x in seq if x]
        if seq != sorted(seq) or seq[-1] > AS_OF:
            n_bad += 1
    check("return dates are in order (delivered, requested, received, refunded) and not in the future", n_bad == 0)
    # 6 statuses
    need_dl = {"delivered", "returned", "return_in_progress"}
    check("delivered-type orders have a delivery date and the others do not",
          all(bool(o["delivered_at"]) == (o["status"] in need_dl) for o in gen_o))
    for fact, values in (("order_status", [o["status"] for o in O]),
                         ("payment_method", [o["payment_method"] for o in O]),
                         ("item_condition", [i["item_condition"] for i in items]),
                         ("product_category", [i["category"] for i in items]),
                         ("order_section", [o["section"] for o in O])):
        extra = set(values) - set(vocab[fact]["values"])
        check(f"every {fact} is in the shared vocabulary", not extra, str(sorted(extra)) if extra else "")
    # 7 policy
    n_bad = 0
    for r in gen_r:
        it, o = item_by_id[r["notes"].split()[1]]
        k = (d(r["requested_at"]) - d(o["delivered_at"])).days
        if decide(o["section"], it["category"], it["product_subcategory"], it["item_condition"],
                  it["return_eligible_tag"], it["warranty_tag"], k, True)[0] != "allow":
            n_bad += 1
    check("every generated return was allowed by the policy on the day it was requested", n_bad == 0, f"{n_bad} not")
    n_bad = 0
    gen_s = S[len(old["scenarios"]["scenarios"]):]
    for s in gen_s:
        if s["type"] != "create_return":
            continue
        f, cai = s["check_action_input"]["facts"], s["check_action_input"]
        o = orders[cai["arguments"]["order_id"]]
        it = next(i for i in o["items"] if i["item_id"] == cai["arguments"]["item_id"])
        same = (f["item_condition"] == it["item_condition"] and f["product_subcategory"] == it["product_subcategory"]
                and f["delivered_at"] == o["delivered_at"] and f["payment_method"] == o["payment_method"]
                and cai["customer_verified"] == cust[s["customer_id"]]["verified"]
                and s["days_since_delivery"] == (AS_OF - d(o["delivered_at"])).days)
        dec, reason, secs = decide(f["order_section"], f["product_category"], f["product_subcategory"],
                                   f["item_condition"], f["return_eligible_tag"], f["warranty_tag"],
                                   s["days_since_delivery"], cai["customer_verified"])
        if not same or (dec, reason, secs) != (s["expected"]["decision"], s["expected"]["reason_code"],
                                               s["expected"]["policy_sections"]):
            n_bad += 1
    check("every create_return label matches the policy evaluator and the order data", n_bad == 0, f"{n_bad} off")
    used = {t for s in S for t in s["expected"].get("policy_sections", [])}
    check("every expected policy section is a real heading in the policy", used <= headings, str(used - headings))
    check("refund_status scenarios point at a return of that order",
          all(s["return_id"] in {r["return_id"] for r in R if r["order_id"] == s["order_id"]}
              for s in gen_s if s["type"] == "refund_status"))
    # 8 spread
    langs = Counter(s["language"] for s in gen_s)
    check("generated scenarios use all four language styles", set(langs) == {"en", "ar", "mixed", "arabizi"},
          ", ".join(f"{k} {v}" for k, v in sorted(langs.items())))
    reasons = Counter(s["expected"]["reason_code"] for s in gen_s if s["type"] == "create_return")
    check("create_return covers every decision reason the evaluator can give", len(reasons) >= 8,
          ", ".join(f"{k} {v}" for k, v in sorted(reasons.items())))
    check("tickets have valid dates before 2026-10-08", all(d(t["created_at"]) <= AS_OF for t in T_new))
    # 9 the database Team A's loader built
    if a.db:
        con = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
        q = lambda sql: con.execute(sql).fetchone()[0]
        check("db: row counts equal the files",
              q("select count(*) from customers where tenant_id='noon_eg'") == len(C)
              and q("select count(*) from orders where tenant_id='noon_eg'") == len(O)
              and q("select count(*) from order_items where tenant_id='noon_eg'") == len(items)
              and q("select count(*) from returns where tenant_id='noon_eg'") == len(R)
              and q("select count(*) from eval_scenarios where tenant_id='noon_eg'") == len(S)
              and q("select count(*) from past_tickets where tenant_id='noon_eg'") == len(T_new))
        check("db: no foreign key problems", not con.execute("pragma foreign_key_check").fetchall())
        schema = (service_root / "src" / "knowledge_service" / "db" / "schema.sql").read_text(encoding="utf-8")
        version = re.search(r'SCHEMA_VERSION = "(\w+)"', (service_root / "src" / "knowledge_service" / "db" / "__init__.py")
                            .read_text(encoding="utf-8")).group(1)
        tables = set(re.findall(r"CREATE (?:VIRTUAL )?TABLE (\w+)", schema))
        have = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
        check(f"db: built from Team A's schema (same tables, version {version})",
              tables <= have and q("select value from db_meta where key='schema_version'") == version)
        check("db: shop_001 is unchanged in size", q("select count(*) from orders where tenant_id='shop_001'") == 16)
    print(f"\n{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
