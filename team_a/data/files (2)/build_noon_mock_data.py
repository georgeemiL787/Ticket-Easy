"""Generate fictional customer mock data for the noon Egypt return policy.

Writes four files (customers, orders, returns, scenarios) for tenant `noon_eg`.
Every decision scenario's expected outcome is hand-labelled below AND checked
against a small reference evaluator of the policy, so date or label mistakes
fail loudly instead of shipping silently.

Usage:  python build_noon_mock_data.py [--out DIR]
All people, phones and emails are fictional. Prices and the 20 EGP COD fee are
invented (they are not in the policy page).
"""
import argparse
import json
import zlib
from datetime import date, timedelta
from pathlib import Path

AS_OF = date(2026, 10, 8)          # fixed "today" so days_since_delivery is reproducible
TENANT = "noon_eg"


def ago(n):
    return (AS_OF - timedelta(days=n)).isoformat()


# ---------------------------------------------------------------- customers
_C = [
    ("C-1001", "Nour Hassan", "نور حسن", "Cairo", "en", True),
    ("C-1002", "Karim Mostafa", "كريم مصطفى", "Giza", "arabizi", True),
    ("C-1003", "Salma Adel", "سلمى عادل", "Alexandria", "ar", True),
    ("C-1004", "Omar Fathy", "عمر فتحي", "Mansoura", "mixed", True),
    ("C-1005", "Mariam Samir", "مريم سمير", "Cairo", "en", True),
    ("C-1006", "Youssef Ibrahim", "يوسف إبراهيم", "Tanta", "ar", True),
    ("C-1007", "Hana Tarek", "هنا طارق", "Minya", "arabizi", True),
    ("C-1008", "Ahmed Saeed", "أحمد سعيد", "Assiut", "en", True),
    ("C-1009", "Rana Khaled", "رنا خالد", "Hurghada", "arabizi", True),
    ("C-1010", "Mahmoud Gamal", "محمود جمال", "Giza", "ar", True),
    ("C-1011", "Dina Fouad", "دينا فؤاد", "Cairo", "en", False),
]
customers = []
for i, (cid, en, ar, city, lang, ver) in enumerate(_C, start=1):
    customers.append({
        "customer_id": cid, "tenant_id": TENANT, "name_en": en, "name_ar": ar,
        "phone": f"0100 000 {i:04d}", "email": en.lower().replace(" ", ".") + "@example.com",
        "city": city, "preferred_language": lang,
        "verified": ver, "verified_via": "otp_sms" if ver else None,
        "noon_wallet_egp": 0, "member_since": ago(200 + 40 * i),
    })
CUST = {c["customer_id"]: c for c in customers}

# ------------------------------------------------------- policy evaluator
CAT_SECTION = {
    "electronics": "Noon section: Electronics and Mobiles",
    "beauty_health": "Noon section: Beauty and Health",
    "fashion": "Noon section: Fashion",
    "home_kitchen": "Noon section: Home and Kitchen",
    "sports": "Noon section: Sports and Outdoors",
    "toys": "Noon section: Toys and Games",
    "baby": "Noon section: Baby Products",
    "grocery": "Noon section: Grocery",
    "pet_supplies": "Noon section: Pet Supplies",
}
WINDOW_SEC = "Noon section: return eligibility and window"
MINUTES_CATS = {"electronics", "beauty_health", "home_kitchen", "sports", "toys",
                "baby", "automotive", "tools", "pet_supplies", "stationery"}
FASHION_EXCLUDED = {"underwear", "lingerie", "swimwear", "bra", "socks", "tights"}
BEAUTY_EXCLUDED = {"hair_care", "personal_care", "vitamins", "sports_nutrition"}
BABY_EXCLUDED = {"hygiene", "feeding", "diapers", "bath_care", "medical"}


def decide_return(section, cat, sub, cond, tag, warranty, days, verified):
    """Reference reading of the noon return policy -> (decision, reason, sections)."""
    if not verified:
        return "deny", "IDENTITY_REQUIRED", []
    if section == "noon":
        if not tag:
            return "deny", "NO_RETURN_TAG", [WINDOW_SEC]
        if days > 15:
            return "deny", "OUTSIDE_WINDOW", [WINDOW_SEC]
        cs = CAT_SECTION.get(cat)
        bad = lambda: ("deny", "CONDITION_NOT_MET", [cs])
        excl = lambda: ("deny", "CATEGORY_EXCLUDED", [cs])
        if cat == "grocery":
            return excl()
        if cat == "fashion":
            if sub in FASHION_EXCLUDED:
                return excl()
            if sub == "apparel" and cond != "unworn_tags_intact":
                return bad()
        elif cat == "beauty_health":
            if sub in BEAUTY_EXCLUDED:
                return excl()
            if cond in {"opened", "used"}:
                return bad()
        elif cat == "electronics":
            if cond in {"opened", "used"}:
                return bad()
        elif cat == "home_kitchen":
            if sub in {"furniture", "large_appliance"}:
                return excl()
            if sub == "bedding" and cond == "unwrapped":
                return bad()
            if sub == "home_decor" and cond != "sealed":
                return bad()
            if cond in {"used", "assembled", "installed"}:
                return bad()
        elif cat == "toys":
            if sub == "costume":
                return excl()
            if cond in {"used", "activated", "missing_parts"}:
                return bad()
        elif cat == "baby":
            if sub in BABY_EXCLUDED:
                return excl()
            if cond != "sealed":
                return bad()
        elif cat == "pet_supplies":
            if sub == "perishable":
                return excl()
        elif cond != "sealed":
            return bad()
        return "allow", "WITHIN_POLICY", [cs, WINDOW_SEC]
    # Minutes section
    if not tag:
        return "deny", "NO_RETURN_TAG", ["Noon Minutes: eligible categories"]
    if cat not in MINUTES_CATS:
        return "deny", "CATEGORY_EXCLUDED", ["Noon Minutes: eligible categories"]
    if warranty:
        return "deny", "WARRANTY_SERVICE_CENTER", ["Noon Minutes: return conditions"]
    if cond == "defective":
        # 30-day defect window exists, but the "opened = declined" rule has no defect exception.
        return "require_human", "POLICY_CONFLICT", ["Noon Minutes: return window", "Noon Minutes: return conditions"]
    if days > 14:
        return "deny", "OUTSIDE_WINDOW", ["Noon Minutes: return window"]
    if cond != "sealed":
        return "deny", "CONDITION_NOT_MET", ["Noon Minutes: return conditions"]
    return "allow", "WITHIN_POLICY", ["Noon Minutes: return conditions", "Noon Minutes: return window"]


# --------------------------------------------------- decision scenarios
# (cust, section, cat, sub, name, price, days, cond, tag, warranty, payment, expected, lang, message, purpose)
D = [
    ("C-1001", "noon", "electronics", "smartphone", "Android smartphone 128GB", 8999, 5, "sealed", True, False, "credit_card", "allow", "en",
     "Hi, I got my phone 5 days ago and the box is still sealed. I'd like to return it.", "Happy path: sealed electronics inside the 15-day window"),
    ("C-1002", "noon", "electronics", "headphones", "Wireless headphones", 1299, 3, "opened", True, False, "cash_on_delivery", "deny", "arabizi",
     "3ayez arga3 el headphones, bas ana fatahet el box. momken?", "Opened electronics without a defect"),
    ("C-1003", "noon", "electronics", "laptop", "14-inch laptop", 18500, 12, "defective", True, False, "credit_card", "allow", "ar",
     "اللابتوب اللي استلمته مش بيفتح وفيه عيب تصنيع، عايزة أرجعه.", "Manufacturing defect: return request allowed, noon decides repair/refund after inspection"),
    ("C-1004", "noon", "fashion", "apparel", "Men's padded jacket", 1450, 15, "unworn_tags_intact", True, False, "cash_on_delivery", "allow", "mixed",
     "هل لسه أقدر أرجع الجاكيت؟ استلمته من 15 يوم وكل الـ tags عليه.", "Boundary: day 15 is allowed in the Noon section (a 14-day rule would wrongly deny)"),
    ("C-1005", "noon", "fashion", "apparel", "Women's summer dress", 899, 16, "unworn_tags_intact", True, False, "credit_card", "deny", "en",
     "I received my dress 16 days ago. Can I still return it?", "Boundary: day 16 is outside the Noon window"),
    ("C-1006", "noon", "fashion", "underwear", "Men's boxer briefs 3-pack", 350, 2, "sealed", True, False, "cash_on_delivery", "deny", "ar",
     "البوكسرات مش مقاسي، ينفع أرجعها؟", "Excluded fashion category (tag set true to isolate the category rule)"),
    ("C-1007", "noon", "grocery", "snacks", "Potato chips box", 240, 1, "sealed", True, False, "cash_on_delivery", "deny", "arabizi",
     "el chips wasal w mesh 3ayza, momken a3mel return?", "All grocery is non-returnable"),
    ("C-1008", "noon", "beauty_health", "skincare", "Face moisturizer", 420, 4, "sealed", True, False, "credit_card", "allow", "en",
     "I'd like to return an unopened moisturizer I received 4 days ago.", "Unopened skincare is returnable"),
    ("C-1009", "noon", "beauty_health", "fragrance", "Women's perfume 100ml", 1800, 6, "opened", True, False, "credit_card", "deny", "arabizi",
     "el perfume ma3agebnish, fatahto w rayha msh 7elwa. arga3o?", "Opened fragrance is not accepted"),
    ("C-1010", "noon", "beauty_health", "hair_care", "Anti-dandruff shampoo", 160, 2, "sealed", True, False, "cash_on_delivery", "deny", "ar",
     "الشامبو لسه مقفول، عايز أرجعه.", "Hair care is excluded even if sealed"),
    ("C-1001", "noon", "home_kitchen", "furniture", "3-seater sofa", 12500, 3, "sealed", True, False, "credit_card", "deny", "en",
     "The sofa is too big for my living room. Can I send it back?", "Furniture is not eligible"),
    ("C-1002", "noon", "home_kitchen", "bedding", "Cotton bed sheet set", 780, 5, "unwrapped", True, False, "credit_card", "deny", "arabizi",
     "el malaya fatahtaha mn el kis, momken arga3ha?", "Unwrapped bedding is not accepted"),
    ("C-1003", "noon", "home_kitchen", "home_decor", "Ceramic vase", 650, 7, "sealed", True, False, "credit_card", "allow", "ar",
     "الفازة مش زي الصورة، عايزة أرجعها وهي لسه في الكرتونة.", "Unopened home decor is returnable"),
    ("C-1007", "noon", "baby", "diapers", "Diapers size 4 (60 pcs)", 520, 2, "sealed", True, False, "cash_on_delivery", "deny", "arabizi",
     "el pampers mesh mazbout el size, a3mel return?", "Diapers are excluded baby products"),
    ("C-1005", "noon", "baby", "stroller", "Foldable baby stroller", 4300, 9, "sealed", True, False, "credit_card", "allow", "en",
     "The stroller is still in its sealed box. I'd like to return it.", "Sealed non-hygiene baby product is returnable"),
    ("C-1006", "noon", "toys", "costume", "Kids dress-up costume", 380, 3, "sealed", True, False, "cash_on_delivery", "deny", "ar",
     "بدلة التنكر مش مقاس ابني، ممكن أرجعها؟", "Dress-up costumes are non-returnable"),
    ("C-1008", "noon", "toys", "building_toy", "Building blocks set 500 pcs", 1100, 10, "sealed", True, False, "credit_card", "allow", "en",
     "The building set is sealed and I'd like to return it, please.", "Sealed toy is returnable"),
    ("C-1009", "noon", "pet_supplies", "perishable", "Dog food treats", 275, 1, "sealed", True, False, "credit_card", "deny", "arabizi",
     "el akl bta3 el kalb mesh 3agbo, momken arga3o?", "Perishable pet food cannot be returned"),
    ("C-1010", "noon", "electronics", "accessory", "Bluetooth speaker", 950, 4, "sealed", False, False, "cash_on_delivery", "deny", "ar",
     "السماعة لسه في العلبة، ينفع أرجعها؟", "No 'hassle-free returns' tag: cannot be returned under any circumstances"),
    ("C-1002", "minutes", "electronics", "accessory", "USB-C wall charger", 249, 10, "sealed", True, False, "cash_on_delivery", "allow", "arabizi",
     "3ayez arga3 el charger bta3 noon Minutes, lessa fel 3elba.", "Minutes: sealed returnable item inside 14 days"),
    ("C-1004", "minutes", "electronics", "accessory", "USB-C cable 2m", 120, 15, "sealed", True, False, "cash_on_delivery", "deny", "mixed",
     "أنا طلبت الكابل من noon Minutes وعايز أرجعه، استلمته من 15 يوم.", "Minutes window is 14 days, so day 15 is denied (would be allowed in the Noon section)"),
    ("C-1003", "minutes", "electronics", "accessory", "Power bank 10000mAh", 599, 25, "defective", True, False, "credit_card", "require_human", "ar",
     "الباور بانك وقف شغل بعد حوالي 3 أسابيع وفيه عيب تصنيع، عايزة أرجعه.", "Policy conflict: 30-day defect window vs 'opened = declined' with no defect exception"),
    ("C-1005", "minutes", "home_kitchen", "small_appliance", "Electric kettle", 699, 6, "sealed", True, True, "credit_card", "deny", "en",
     "The kettle I bought via Minutes is unopened. I'd like to return it.", "Warranty-tagged Minutes items go to the service center, not noon returns"),
    ("C-1006", "minutes", "grocery", "beverages", "Bottled water 12-pack", 90, 1, "sealed", True, False, "cash_on_delivery", "deny", "ar",
     "مية الـ12 زجاجة وصلت بس مش عايزها، أرجعها؟", "Grocery is not in the Minutes returnable categories"),
    ("C-1011", "noon", "electronics", "accessory", "Wireless mouse", 450, 4, "sealed", True, False, "credit_card", "deny", "en",
     "Please return my wireless mouse. The order is 4 days old and the box is sealed.", "Eligible item but customer is NOT verified: identity gate must deny"),
]

COD_FEE = 20
orders, scenarios = [], []
_oid = [30000]
_sid = [0]


def next_order():
    _oid[0] += 1
    return f"ORD-{_oid[0]}"


def next_scn():
    _sid[0] += 1
    return f"NS-{_sid[0]:02d}"


def make_item(oid, n, name, cat, sub, price, cond, tag, warranty, qty=1):
    return {"item_id": f"{oid}-I{n}", "sku": f"SKU-{zlib.crc32(name.encode()) % 100000:05d}", "name": name,
            "category": cat, "product_subcategory": sub, "qty": qty, "price_egp": price,
            "return_eligible_tag": tag, "warranty_tag": warranty,
            "item_condition": cond, "is_clearance": False}


def make_order(oid, cust, section, status, days, items, payment, shipping=0):
    cod = COD_FEE if payment == "cash_on_delivery" else 0
    sub = sum(i["price_egp"] * i["qty"] for i in items)
    delivered = ago(days) if status in {"delivered", "return_in_progress", "returned"} else None
    return {"order_id": oid, "tenant_id": TENANT, "customer_id": cust, "section": section, "status": status,
            "placed_at": ago(days + 3), "expected_delivery_date": ago(days) if delivered else ago(-2),
            "delivered_at": delivered, "payment_method": payment,
            "shipping_fee_egp": shipping, "cod_fee_egp": cod, "items": items,
            "total_egp": sub + shipping + cod}


for row in D:
    cust, section, cat, sub, name, price, days, cond, tag, warranty, pay, exp, lang, msg, purpose = row
    oid = next_order()
    it = make_item(oid, 1, name, cat, sub, price, cond, tag, warranty)
    o = make_order(oid, cust, section, "delivered", days, [it], pay)
    orders.append(o)
    dec, reason, secs = decide_return(section, cat, sub, cond, tag, warranty, days, CUST[cust]["verified"])
    assert dec == exp, f"label mismatch for {name!r}: hand={exp} evaluator={dec} ({reason})"
    scenarios.append({
        "scenario_id": next_scn(), "type": "create_return", "language": lang,
        "customer_id": cust, "order_id": oid, "customer_message": msg,
        "check_action_input": {
            "tenant_id": TENANT, "tool": "create_return",
            "customer_verified": CUST[cust]["verified"],
            "facts": {"order_status": "delivered", "delivered_at": o["delivered_at"],
                      "expected_delivery_date": o["expected_delivery_date"],
                      "product_category": cat, "product_subcategory": sub,
                      "is_clearance": False, "item_condition": cond,
                      "order_section": section, "return_eligible_tag": tag,
                      "warranty_tag": warranty, "payment_method": pay},
            "arguments": {"order_id": oid, "item_id": it["item_id"]}},
        "expected": {"decision": dec, "reason_code": reason, "policy_sections": secs},
        "days_since_delivery": days, "test_purpose": purpose,
    })

# cancel-before-delivery: not covered by the return policy page -> no evidence
oid = next_order()
it = make_item(oid, 1, "Kitchen blender", "home_kitchen", "small_appliance", 1250, "sealed", True, False)
o = make_order(oid, "C-1007", "noon", "shipped", 0, [it], "cash_on_delivery")
orders.append(o)
scenarios.append({
    "scenario_id": next_scn(), "type": "cancel_order", "language": "arabizi", "customer_id": "C-1007",
    "order_id": oid, "customer_message": "3ayza alghi el order abl ma yewsal.",
    "check_action_input": {"tenant_id": TENANT, "tool": "cancel_order", "customer_verified": True,
                           "facts": {"order_status": "shipped"}, "arguments": {"order_id": oid}},
    "expected": {"decision": "no_evidence", "reason_code": "NOT_IN_CORPUS", "policy_sections": []},
    "test_purpose": "Cancellation is not covered by the return policy page: retrieval must return an explicit empty result, not a guess",
})

# ------------------------------------------- returns / refund-status cases
returns = []


def add_return(cust, section, name, cat, sub, price, deliv_days, payment, status_order, ret, msg, lang, exp, purpose,
               extra_items=None, shipping=0):
    oid = next_order()
    items = [make_item(oid, 1, name, cat, sub, price, "sealed", True, False)]
    for k, (n2, c2, s2, p2) in enumerate(extra_items or [], start=2):
        items.append(make_item(oid, k, n2, c2, s2, p2, "sealed", True, False))
    o = make_order(oid, cust, section, status_order, deliv_days, items, payment, shipping)
    orders.append(o)
    rid = f"RET-{5000 + len(returns) + 1}"
    rec = {"return_id": rid, "tenant_id": TENANT, "order_id": oid, "customer_id": cust, "payment_method": payment}
    rec.update(ret)
    returns.append(rec)
    scenarios.append({
        "scenario_id": next_scn(), "type": "refund_status", "language": lang, "customer_id": cust,
        "order_id": oid, "return_id": rid, "customer_message": msg,
        "expected": exp, "test_purpose": purpose})
    return o


add_return("C-1001", "noon", "Wireless mouse", "electronics", "accessory", 450, 14, "credit_card", "returned",
           {"status": "refund_initiated", "requested_at": ago(11), "received_at": ago(4), "qc_result": "passed",
            "refund_amount_egp": 450, "refund_destination": "original_card", "refund_initiated_at": ago(2)},
           "My return was approved 3 days ago. When will I see the money on my card?", "en",
           {"decision": "answer", "policy_sections": ["Refunds: card payments", "Refunds: refund destination by payment method"],
            "answer_points": ["Refund first appears as noon credits for 24 hours",
                              "If unused, it transfers automatically to the card within 14 days, depending on the bank",
                              "Status is visible under My Account"]},
           "Card refund timing")

add_return("C-1004", "noon", "Men's sneakers", "fashion", "footwear", 799, 9, "cash_on_delivery", "returned",
           {"status": "refunded", "requested_at": ago(8), "received_at": ago(6), "qc_result": "passed",
            "refund_amount_egp": 799, "refund_destination": "noon_wallet", "refund_initiated_at": ago(5),
            "refund_completed_at": ago(5), "notes": "COD fee of 20 EGP not refunded"},
           "دفعت 819 جنيه بس الريفند جه 799 بس، فين العشرين جنيه؟", "mixed",
           {"decision": "answer", "policy_sections": ["Refunds: partial refunds and non-refundable fees",
                                                       "Refunds: refund destination by payment method"],
            "answer_points": ["The COD fee (and shipping fee) is not refundable once the item has been delivered",
                              "COD refunds go to the noon wallet or a bank account"]},
           "Non-refundable COD fee")

add_return("C-1005", "noon", "Backpack", "fashion", "bags", 650, 15, "bnpl_tabby", "returned",
           {"status": "refund_initiated", "requested_at": ago(13), "received_at": ago(11), "qc_result": "passed",
            "refund_amount_egp": 650, "refund_destination": "bnpl_provider", "refund_initiated_at": ago(10)},
           "My Tabby refund started 10 days ago and I still don't see it.", "en",
           {"decision": "answer", "policy_sections": ["Refunds: buy now pay later (Tabby, Tamara)"],
            "answer_points": ["BNPL refunds reflect within 14 to 21 days depending on the partner",
                              "10 days is still inside that range; roughly 4 to 11 days remain"]},
           "BNPL refund not overdue yet")

add_return("C-1003", "noon", "10-inch tablet", "electronics", "tablet", 7200, 13, "credit_card_emi", "returned",
           {"status": "refund_initiated", "requested_at": ago(9), "received_at": ago(6), "qc_result": "passed",
            "refund_amount_egp": 7200, "refund_destination": "original_card", "refund_initiated_at": ago(3)},
           "اشتريت التابلت بالتقسيط على الكريدت كارد، ينفع الفلوس ترجع في محفظة noon بدل الكارت؟", "ar",
           {"decision": "answer", "policy_sections": ["Refunds: credit card EMI"],
            "answer_points": ["No: an EMI order can only be refunded to the same credit card",
                              "Full refund of EMIs paid; the bank may charge refund or pre-closure fees"]},
           "EMI refund destination")

add_return("C-1002", "noon", "Gaming headset", "electronics", "headphones", 1500, 10, "cash_on_delivery", "return_in_progress",
           {"status": "rejected_in_hub", "requested_at": ago(7), "received_at": ago(4), "qc_result": "failed_item_used",
            "refund_amount_egp": 0, "refund_destination": None, "delivery_attempts_failed": 2,
            "hub_hold_started_at": ago(2), "hub_hold_business_days": 3},
           "el return bta3y et3amal reject w 3ayez el headset yege tany.", "arabizi",
           {"decision": "require_human", "reason_code": "POLICY_CONFLICT",
            "policy_sections": ["Noon section: rejected items and redelivery",
                                "Noon section: returned items that could not be redelivered"],
            "answer_points": ["The page says the hub holds the item 3 business days but customers have 7 business days to ask for redelivery",
                              "Time-sensitive and ambiguous: hand to customer care with context"]},
           "Rejected item: contradictory hold vs request windows should escalate")

add_return("C-1008", "noon", "Yoga mat", "sports", "fitness", 380, 12, "credit_card", "return_in_progress",
           {"status": "partially_refunded", "requested_at": ago(8), "received_at": ago(5), "qc_result": "passed",
            "refund_amount_egp": 380, "refund_destination": "original_card", "refund_initiated_at": ago(2),
            "notes": "Dumbbell set (RET item 2) still in QC; shipping fee 40 EGP not refunded"},
           "I returned two items but only got 380 EGP back. Where's the rest?", "en",
           {"decision": "answer", "policy_sections": ["Refunds: partial refunds and non-refundable fees"],
            "answer_points": ["Refunds are processed item by item, so a part can arrive first",
                              "The shipping fee is not refundable once the item was delivered"]},
           "Partial refund explanation", extra_items=[("Dumbbell set", "sports", "fitness", 900)], shipping=40)

add_return("C-1009", "noon", "Women's handbag", "fashion", "bags", 1250, 22, "credit_card", "returned",
           {"status": "refund_approved", "requested_at": ago(21), "received_at": ago(19), "qc_result": "passed",
            "refund_amount_egp": 1250, "refund_destination": "original_card", "refund_initiated_at": ago(16)},
           "el return et-approve men 16 yom w el flous lessa ma gatsh 3al card.", "arabizi",
           {"decision": "answer", "policy_sections": ["Refunds: tracking status and escalation"],
            "answer_points": ["Status is Approved and the standard time frame has passed",
                              "Customer should contact their bank"]},
           "Approved refund past the standard time: point to the bank")

# ------------------------------------------------- policy-only questions
PQ = [
    ("en", "If I paid cash on delivery, where does my refund go?", "answer",
     ["Refunds: refund destination by payment method", "Refunds: cash payments"],
     ["COD refunds go to the noon wallet or a bank account"]),
    ("ar", "هل رسوم الشحن بترجعلي لو رجعت المنتج؟", "answer",
     ["Refunds: partial refunds and non-refundable fees"],
     ["Shipping fee and COD fee are not refundable once the item was delivered"]),
    ("en", "What is the return window for noon Minutes orders?", "answer",
     ["Noon Minutes: return window"],
     ["14 days for products in original state", "30 days for manufacturer defects"]),
    ("arabizi", "momken a8ayar tare2et el refund ba3d ma et3amal?", "answer",
     ["Refunds: partial refunds and non-refundable fees"],
     ["No: once a refund is issued, the refund method cannot be changed"]),
    ("en", "Do you match prices from other stores?", "no_evidence", [], []),
    ("ar", "هل ممكن أرجع الأثاث أو الأجهزة المنزلية الكبيرة؟", "answer",
     ["Noon section: Home and Kitchen"],
     ["Furniture and large home appliances are not eligible for return"]),
    ("en", "How many days do I have to return a product from the Noon section?", "answer",
     ["Noon section: return eligibility and window"],
     ["15 days from receipt, only for products with the hassle-free returns tag"]),
]
for lang, msg, dec, secs, pts in PQ:
    scenarios.append({
        "scenario_id": next_scn(), "type": "policy_question", "language": lang, "customer_id": None,
        "customer_message": msg,
        "expected": {"decision": dec, "policy_sections": secs, "answer_points": pts},
        "test_purpose": "Grounded answer with citation" if dec == "answer" else "Unanswerable: must return explicit no-evidence"})

# ----------------------------------------------------------------- write
META = {"tenant_id": TENANT, "as_of": AS_OF.isoformat(), "currency": "EGP",
        "fictional": True,
        "source_policy": "noon_egypt_return_policy.md (https://www.noon.com/egypt-en/return-policy/)",
        "note": "Fictional people, phones and emails. Prices and the 20 EGP COD fee are invented."}


def write(path, key, rows):
    path.write_text(json.dumps({"_meta": META, key: rows}, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=".")
    out = Path(ap.parse_args().out)
    out.mkdir(parents=True, exist_ok=True)
    write(out / "noon_customers.json", "customers", customers)
    write(out / "noon_orders.json", "orders", orders)
    write(out / "noon_returns.json", "returns", returns)
    write(out / "noon_scenarios.json", "scenarios", scenarios)
    from collections import Counter
    print(f"customers={len(customers)} orders={len(orders)} returns={len(returns)} scenarios={len(scenarios)}")
    print("by type:", dict(Counter(s["type"] for s in scenarios)))
    print("by language:", dict(Counter(s["language"] for s in scenarios)))
    print("create_return decisions:", dict(Counter(s["expected"]["decision"] for s in scenarios if s["type"] == "create_return")))
