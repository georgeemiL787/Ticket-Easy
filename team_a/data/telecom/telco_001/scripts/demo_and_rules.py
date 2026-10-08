"""Hand-made demo customers (each one set up for specific test conversations) and the business rules.

Demo customers get IDs TC-D001..TC-D020 and fixed line numbers so scenarios can refer to them.
Identity check = line number (or home landline) + last 4 digits of the owner's national ID.
"""
from datetime import date


def cust(cid, name_en, name_ar, male, lang, gov, last4, note, since="2019-05-10", segment="mass"):
    return dict(customer_id=cid, name_en=name_en, name_ar=name_ar, male=male, lang=lang, gov=gov,
                nid_last4=last4, note=note, since=date.fromisoformat(since), segment=segment)


def line(plan, msisdn=None, **kw):
    return dict(plan=plan, msisdn=msisdn, demo=True, **kw)


DEMO = [
    {"customer": cust("TC-D001", "Mona Adel", "منى عادل فاروق", False, "arabizi", ("Cairo", "Nasr City"), "4821",
                      "Recharged 100 EGP today and got 91 (tax question); has an active Net 55 bundle."),
     "accounts": [{"type": "prepaid", "lines": [line("PRE-BASIC", "01012340001", balance=82.5, activated=date(2021, 3, 2), extras=[
         {"kind": "recharge", "at": "2026-10-08T09:12:40", "amount": 100, "channel": "fawry", "reference": "RC7000000101"},
         {"kind": "bundle", "bundle": "NET-55", "subscription_id": "SUB-D001", "at": "2026-09-20T19:02:11", "expires": "2026-10-20T19:02:11", "used": 0.4}])]}]},
    {"customer": cust("TC-D002", "Karim Hassan", "كريم حسن محمود", True, "en", ("Giza", "Dokki"), "7315",
                      "Postpaid; latest bill is high because of 780.50 EGP roaming from a Saudi trip; bill not yet due."),
     "accounts": [{"type": "postpaid", "bill_day": 1, "lines": [line("POST-RED-350", "01112340002", credit_limit=1500, activated=date(2020, 6, 14),
                                                                      invoice_items=[{"type": "roaming", "description": "Roaming usage - Saudi Arabia", "amount": 780.5}],
                                                                      unpaid_cycles=1)]}]},
    {"customer": cust("TC-D003", "Salma Fathy", "سلمى فتحي إبراهيم", False, "ar", ("Cairo", "Maadi"), "1946",
                      "Subscribed to Net 100 by mistake today at 12:05 and used about 1%: refund allowed."),
     "accounts": [{"type": "prepaid", "lines": [line("PRE-SECONDS", "01212340003", balance=3.2, activated=date(2022, 1, 9), extras=[
         {"kind": "bundle", "bundle": "NET-100", "subscription_id": "SUB-D003", "at": "2026-10-08T12:05:00", "expires": "2026-11-07T12:05:00", "used": 0.01}])]}]},
    {"customer": cust("TC-D004", "Omar Farouk", "عمر فاروق السيد", True, "ar", ("Alexandria", "Smouha"), "6603",
                      "Subscribed to Net 200 three days ago and used 42%: bundle refund not allowed."),
     "accounts": [{"type": "prepaid", "lines": [line("PRE-BASIC", "01512340004", balance=14.0, activated=date(2018, 11, 20), extras=[
         {"kind": "bundle", "bundle": "NET-200", "subscription_id": "SUB-D004", "at": "2026-10-05T18:40:00", "expires": "2026-11-04T18:40:00", "used": 0.42}])]}]},
    {"customer": cust("TC-D005", "Nour El-Din Samir", "نور الدين سمير", True, "mixed", ("Giza", "Haram"), "2290",
                      "Charged daily for Games club after a web click (consent unclear) since 2026-08-15."),
     "accounts": [{"type": "prepaid", "lines": [line("PRE-YOUTH", "01012340005", balance=9.3, activated=date(2023, 7, 1), extras=[
         {"kind": "vas", "vas": "VAS-GAMES", "vas_sub_id": "VS-D005", "since": "2026-08-15", "channel": "web_click", "consent": "unclear"}])]}]},
    {"customer": cust("TC-D006", "Hana Mostafa", "هنا مصطفى كامل", False, "arabizi", ("Cairo", "Shubra"), "5537",
                      "Postpaid line suspended: two unpaid bills, the older one overdue by more than 30 days."),
     "accounts": [{"type": "postpaid", "bill_day": 15, "lines": [line("POST-RED-200", "01112340006", status="suspended", status_reason="unpaid_invoice",
                                                                       credit_limit=800, activated=date(2021, 9, 30), unpaid_cycles=2)]}]},
    {"customer": cust("TC-D007", "Youssef Samir", "يوسف سمير حسن", True, "ar", ("Dakahlia", "Mansoura"), "8812",
                      "Home internet down: there is an ongoing fiber-cut outage in Mansoura since yesterday morning."),
     "accounts": [{"type": "home_internet", "lines": [line("HOME-VDSL-250", landline="0502234007", activated=date(2022, 4, 4), extras=[
         {"kind": "outage", "outage_id": "OUT-D007", "service": "home_internet", "start": "2026-10-07T06:00:00", "cause": "fiber_cut"}])]},
                  {"type": "prepaid", "lines": [line("PRE-BASIC", "01212340007", balance=20.0, activated=date(2019, 2, 2))]}]},
    {"customer": cust("TC-D008", "Laila Ibrahim", "ليلى إبراهيم عبد العزيز", False, "en", ("Alexandria", "Smouha"), "4107",
                      "Home internet slow; technician visit already booked for 2026-10-10, 13:00-17:00."),
     "accounts": [{"type": "home_internet", "lines": [line("HOME-VDSL-400", landline="0342234008", activated=date(2025, 1, 12), extras=[
         {"kind": "appointment", "appointment_id": "TA-D008", "reason": "slow_speed", "date": "2026-10-10", "window": "13:00-17:00", "created": "2026-10-06T11:20:00"}])]},
                  {"type": "prepaid", "lines": [line("CTL-90", "01012340008", balance=45.0, activated=date(2020, 8, 8))]}]},
    {"customer": cust("TC-D009", "Ahmed Kamel", "أحمد كامل حسين", True, "ar", ("Qalyubia", "Banha"), "3378",
                      "Mix 75 was charged twice at the same minute last night (system duplicate): 75 EGP refund within agent limit."),
     "accounts": [{"type": "prepaid", "lines": [line("PRE-BASIC", "01112340009", balance=6.1, activated=date(2017, 5, 5), extras=[
         {"kind": "bundle", "bundle": "MIX-75", "subscription_id": "SUB-D009", "at": "2026-10-07T21:14:03", "expires": "2026-11-06T21:14:03", "used": 0.1, "times": 2}])]}]},
    {"customer": cust("TC-D010", "Mariam Girgis", "مريم جرجس حنا", False, "en", ("Cairo", "Heliopolis"), "9054",
                      "New postpaid line (since July 2026, fewer than 4 paid bills), so roaming needs a deposit; travelling to Saudi Arabia.", since="2026-07-20"),
     "accounts": [{"type": "postpaid", "bill_day": 8, "opened": date(2026, 7, 20), "lines": [line("POST-RED-200", "01212340010", credit_limit=800, activated=date(2026, 7, 20))]}]},
    {"customer": cust("TC-D011", "Tarek Soliman", "طارق سليمان", True, "arabizi", ("Sharqia", "Zagazig"), "1172",
                      "Prepaid line only 40 days old: porting out needs 4 months of ownership.", since="2026-08-29"),
     "accounts": [{"type": "prepaid", "opened": date(2026, 8, 29), "lines": [line("PRE-BASIC", "01512340011", balance=30.0, activated=date(2026, 8, 29))]}]},
    {"customer": cust("TC-D012", "Dina Mansour", "دينا منصور", False, "mixed", ("Cairo", "New Cairo"), "6640",
                      "Postpaid bill has an unrecognised 650 EGP premium content charge: dispute over 500 needs a supervisor."),
     "accounts": [{"type": "postpaid", "bill_day": 1, "lines": [line("POST-RED-550", "01012340012", credit_limit=2500, activated=date(2021, 2, 2),
                                                                      invoice_items=[{"type": "content_charge", "description": "Premium content service", "amount": 650.0}])]}]},
    {"customer": cust("TC-D013", "Mostafa Zaki", "مصطفى زكي", True, "ar", ("Giza", "Faisal"), "2803",
                      "Phone stolen today: line should be suspended; SIM replacement only in a store."),
     "accounts": [{"type": "control", "lines": [line("CTL-90", "01112340013", balance=22.0, activated=date(2020, 12, 12))]}]},
    {"customer": cust("TC-D014", "Rana Hegazy", "رنا حجازي", False, "en", ("Giza", "Sheikh Zayed"), "7719",
                      "iPhone 15 on 24 installments; the last two bills (with installments) are unpaid, so the device is locked.", segment="premium"),
     "accounts": [{"type": "postpaid", "bill_day": 1, "lines": [line("POST-RED-350", "01212340014", credit_limit=1500, activated=date(2025, 12, 1), unpaid_cycles=2,
                                                                       installment={"device": "DEV-IP15", "months": 24, "start": date(2025, 12, 1), "missed": 2})]}]},
    {"customer": cust("TC-D015", "Seif Badawy", "سيف بدوي", True, "arabizi", ("Gharbia", "Tanta"), "5091",
                      "Has an unpaid Salefny of 10 EGP, so cannot borrow again until the next recharge."),
     "accounts": [{"type": "prepaid", "lines": [line("PRE-YOUTH", "01512340015", balance=0.4, salefny=10.0, activated=date(2023, 3, 3))]}]},
    {"customer": cust("TC-D016", "Fatma Gaber", "فاطمة جابر", False, "ar", ("Minya", "Minya"), "3365",
                      "No paid use for 93 days: the line only receives calls now; a recharge brings it back before day 105."),
     "accounts": [{"type": "prepaid", "lines": [line("PRE-BASIC", "01012340016", balance=6.0, status="inactive", status_reason="receive_only_no_paid_use_90_days",
                                                     idle_days=93, activated=date(2016, 6, 6), extras=[
         {"kind": "recharge", "at": "2026-07-07T18:20:00", "amount": 20, "channel": "ussd_card", "reference": "RC7000001601"}])]}]},
    {"customer": cust("TC-D017", "Hossam Tawfik", "حسام توفيق", True, "en", ("Cairo", "Zamalek"), "4482",
                      "Gulf roaming bundle charged twice (300 EGP duplicate): above the 200 EGP agent refund limit.", segment="premium"),
     "accounts": [{"type": "control", "lines": [line("CTL-140", "01112340017", balance=75.0, activated=date(2019, 9, 9), extras=[
         {"kind": "bundle", "bundle": "ROAM-GULF-7", "subscription_id": "SUB-D017", "at": "2026-10-06T07:30:12", "expires": "2026-10-13T07:30:12", "used": 0.2, "times": 2}])]}]},
    {"customer": cust("TC-D018", "Yasmin Lotfy", "ياسمين لطفي", False, "mixed", ("Cairo", "Mokattam"), "8126",
                      "On Control 50 with 160 EGP balance; wants to upgrade to Control 90."),
     "accounts": [{"type": "control", "lines": [line("CTL-50", "01212340018", balance=160.0, activated=date(2024, 4, 4))]}]},
    {"customer": cust("TC-D019", "Ziad Hamdy", "زياد حمدي", True, "ar", ("Cairo", "Ain Shams"), "6031",
                      "Owner of a second line used by a family member (third-party request scenarios)."),
     "accounts": [{"type": "prepaid", "lines": [line("PRE-BASIC", "01512340019", balance=40.0, activated=date(2018, 1, 1)),
                                                line("PRE-BASIC", "01512340119", balance=5.0, activated=date(2024, 1, 1))]}]},
    {"customer": cust("TC-D020", "Malak Ezzat", "ملك عزت", False, "arabizi", ("Port Said", "Port Said"), "2217",
                      "SIM is asking for the PUK code."),
     "accounts": [{"type": "prepaid", "lines": [line("PRE-YOUTH", "01512340020", balance=18.0, activated=date(2022, 2, 2))]}]},
]


def rule(rule_id, action, scope, conditions, effect, else_effect, citation, quote, msg_ar, msg_en, applies_if=None):
    return dict(rule_id=rule_id, action=action, scope=scope, status="approved", applies_if=applies_if or [],
                conditions=[dict(field=f, op=o, value=v, **{"from": src}) for f, o, v, src in conditions],
                effect=effect, else_effect=else_effect, citation=citation, quote=quote,
                user_message={"ar": msg_ar, "en": msg_en}, effective_date="2026-09-01")


RULES = [
    rule("R-BUNDLE-REFUND-24H", "refund_bundle", "refunds", [("hours_since_subscription", "<=", 24, "facts")], "allow", "deny",
         "refund_policy@v3#s1", "A bundle subscribed by mistake is refunded in full if it is cancelled within 24 hours",
         "للأسف استرداد الباقة متاح خلال 24 ساعة بس من الاشتراك والمدة دي عدت.", "Bundle refunds are only possible within 24 hours of subscribing, and that time has passed."),
    rule("R-BUNDLE-REFUND-USAGE", "refund_bundle", "refunds", [("quota_used_percent", "<", 5, "facts")], "allow", "deny",
         "refund_policy@v3#s1", "and less than 5% of it was used",
         "للأسف اتستخدم أكتر من 5% من الباقة فمش هقدر أرجع قيمتها.", "More than 5% of the bundle has been used, so it can't be refunded."),
    rule("R-REFUND-AGENT-CAP", "refund_charge", "refunds", [("amount", "<=", 200, "arguments")], "allow", "require_human",
         "refund_policy@v3#s2", "Customer service can approve such refunds up to 200 EGP per case; above that a supervisor must approve.",
         "المبلغ أكبر من 200 جنيه فمحتاج موافقة مشرف، هحول الطلب له.", "The amount is over 200 EGP, so a supervisor needs to approve it. I'm passing it on."),
    rule("R-REFUND-CONFIRMED", "refund_charge", "refunds", [("charge_confirmed_wrong", "==", True, "facts")], "allow", "deny",
         "refund_policy@v3#s2", "If system records confirm a charge was wrong or duplicated, the full amount is returned",
         "مش لاقي في السجلات إن الخصم ده غلط أو متكرر، هحولك لزميل يراجعه.", "The records don't show this charge as wrong or duplicated; I'll pass it to a colleague to review."),
    rule("R-VAS-REFUND-30D", "refund_vas", "refunds", [("refund_days", "<=", 30, "arguments")], "allow", "deny",
         "refund_policy@v3#s3", "are refunded for up to the last 30 days",
         "الاسترداد بيكون عن آخر 30 يوم بس.", "Refunds cover the last 30 days at most."),
    rule("R-DISPUTE-60D", "open_bill_dispute", "billing", [("days_since_invoice", "<=", 60, "facts")], "allow", "deny",
         "postpaid_billing_policy@v2#s4", "A customer may dispute any charge within 60 days of the bill date.",
         "الاعتراض متاح خلال 60 يوم من تاريخ الفاتورة والمدة دي عدت.", "Disputes must be raised within 60 days of the invoice date, and that has passed."),
    rule("R-ADJUST-SUPERVISOR", "open_bill_dispute", "billing", [("amount", "<=", 500, "arguments")], "allow", "require_human",
         "postpaid_billing_policy@v2#s4", "adjustments above 500 EGP need a supervisor",
         "المبلغ أكبر من 500 جنيه فهحول الاعتراض لمشرف.", "The amount is over 500 EGP, so I'm passing the dispute to a supervisor."),
    rule("R-PORT-OUT-4M", "issue_port_out", "porting", [("line_age_days", ">=", 120, "facts")], "allow", "deny",
         "porting_policy@v2#s2", "if they have owned the line for at least 4 months",
         "نقل الرقم متاح بعد 4 شهور من ملكية الخط.", "Porting out is possible once you have owned the line for 4 months."),
    rule("R-PORT-OUT-UNPAID", "issue_port_out", "porting", [("unpaid_invoices", "==", 0, "facts")], "allow", "deny",
         "porting_policy@v2#s2", "and the latest bill is paid",
         "لازم تسدد آخر فاتورة الأول قبل نقل الرقم.", "The latest bill must be paid before porting out."),
    rule("R-SIM-SWAP-STORE", "sim_swap", "sim", [], "deny", "deny",
         "sim_policy@v2#s2", "SIM replacement is never done through chat or phone.",
         "بدل الفاقد بيتعمل في الفرع بس بالبطاقة الأصلية.", "A SIM replacement is only done in a store with the original national ID."),
    rule("R-LINE-CANCEL-STORE", "cancel_line", "contracts", [], "deny", "deny",
         "contract_policy@v1#s4", "Chat agents can explain the steps but cannot cancel a line.",
         "إلغاء الخط بيكون من الفرع بس.", "Lines are cancelled in a store only."),
    rule("R-SALEFNY-LOW-BALANCE", "grant_salefny", "recharge", [("prepaid_balance", "<", 5, "facts")], "allow", "deny",
         "prepaid_recharge_policy@v3#s5", "When balance is below 5 EGP, customers can borrow",
         "سلفني متاحة لما الرصيد يكون أقل من 5 جنيه.", "Salefny is available when your balance is under 5 EGP."),
    rule("R-SALEFNY-ONE", "grant_salefny", "recharge", [("salefny_outstanding", "==", 0, "facts")], "allow", "deny",
         "prepaid_recharge_policy@v3#s5", "Only one Salefny can be outstanding at a time.",
         "عليك سلفة لسه متسددتش، أول ما تشحن هتقدر تتسلف تاني.", "You still have an unpaid Salefny; once your next recharge repays it you can borrow again."),
    rule("R-ROAMING-POSTPAID-BILLS", "activate_roaming", "roaming", [("paid_invoices", ">=", 4, "facts")], "allow", "require_human",
         "roaming_policy@v2#s1", "Postpaid roaming is free to activate once the line has paid 4 bills, or earlier with a 750 EGP roaming deposit.",
         "خطك لسه مسددش 4 فواتير، فتفعيل التجوال محتاج تأمين 750 جنيه، هحولك لزميل يكمل معاك.", "Your line hasn't paid 4 bills yet, so roaming needs a 750 EGP deposit. I'll pass you to a colleague to arrange it.",
         applies_if=[dict(field="line_type", op="==", value="postpaid", **{"from": "facts"})]),
    rule("R-PLAN-CHANGE-ONCE", "change_plan", "bundles", [("plan_changes_this_cycle", "==", 0, "facts")], "allow", "deny",
         "bundles_policy@v4#s4", "Postpaid plan changes are allowed once per billing cycle",
         "غيرت النظام مرة في الدورة دي بالفعل، تقدر تغيره تاني من الدورة الجاية.", "You already changed plan this cycle; you can change again next cycle.",
         applies_if=[dict(field="line_type", op="==", value="postpaid", **{"from": "facts"})]),
    rule("R-BUNDLE-BALANCE", "subscribe_bundle", "bundles", [("prepaid_balance_minus_price", ">=", 0, "facts")], "allow", "deny",
         "bundles_policy@v4#s1", "The price is taken from balance (prepaid)",
         "الرصيد مش كفاية للباقة دي.", "Your balance isn't enough for this bundle.",
         applies_if=[dict(field="line_type", op="in", value=["prepaid", "control"], **{"from": "facts"})]),
    rule("R-OUTAGE-COMP-24H", "grant_outage_compensation", "refunds", [("outage_hours", ">", 24, "facts")], "allow", "deny",
         "refund_policy@v3#s4", "for more than 24 continuous hours",
         "التعويض بيكون للأعطال اللي أكتر من 24 ساعة متصلة.", "Compensation applies to outages longer than 24 continuous hours."),
    rule("R-TRANSFER-LIMIT", "transfer_balance", "recharge", [("amount", "<=", 50, "arguments")], "allow", "deny",
         "prepaid_recharge_policy@v3#s6", "between 1 and 50 EGP of credit per transfer",
         "التحويل من 1 لـ 50 جنيه في المرة.", "Transfers are 1 to 50 EGP each."),
    rule("R-PUK-OWNER", "get_puk", "sim", [("identity_verified", "==", True, "facts")], "allow", "deny",
         "sim_policy@v2#s6", "The PUK code is given only to the verified line owner",
         "لازم أتأكد إنك صاحب الخط الأول.", "I need to verify you're the line owner first."),
]
