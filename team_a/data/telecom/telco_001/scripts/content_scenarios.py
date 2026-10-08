"""Hand-written test conversations for telco_001, in the same shape as the shop_001 scenarios.

Every scenario names the demo customer it uses; the expected facts (dates, amounts, refusals)
match that customer's data in the database. Status is "pending" until the agent supports telecom.
Identity = line number (or landline) + last 4 digits of the owner's national ID ("slot:identity").
"""

ID = "slot:identity"


def T(say, decision, awaiting=None, locale=None, **extra):
    exp = {"decision": decision, "awaiting": awaiting}
    if locale:
        exp["locale"] = locale
    exp.update(extra)
    return {"say": say, "expect": exp}


def S(sid, title, lang, customer, turns, **final):
    final.setdefault("trace_invariants", True)
    return {"id": sid, "title": title, "status": "pending", "language": lang, "customer": customer,
            "setup": {"today": "2026-10-08", "now": "2026-10-08T15:00:00"}, "turns": turns, "final": final}


SCENARIOS = [
    # ---------- knowledge only ----------
    S("T01", "English: why a 10 EGP card gives 9.10 credit and costs 13 (policy answer, no identity needed)", "en", None, [
        T("I bought a 10 pound card and only got 9.10, why??", "answer", None, "en", citations_include=["prepaid_recharge_policy@v3#s3"], text_contains="13")],
      no_writes=True, audit_count=0),
    S("T02", "Egyptian Arabic: how to transfer credit and the daily limit", "ar", None, [
        T("ازاي احول رصيد لأختي؟ واقصى حاجة احولها كام؟", "answer", None, "ar", citations_include=["prepaid_recharge_policy@v3#s6"], text_contains="50")],
      no_writes=True),
    S("T03", "Arabizi: roaming activation question for a prepaid line", "arabizi", None, [
        T("msafer el sa3odeya bokra, afa3al el roaming ezay 3al kart?", "answer", None, "arabizi", citations_include=["roaming_policy@v2#s1"])],
      no_writes=True),
    S("T04", "Mixed: do unused Control units carry over (only if renewed within 28 days)", "mixed", None, [
        T("لو وحدات الـ control ما خلصتش الشهر ده بتترحل للشهر الجاي؟", "answer", None, "ar", citations_include=["bundles_policy@v4#s3"], text_contains="28")],
      no_writes=True),
    S("T05", "English: question the policies don't cover is handed over, not invented", "en", None, [
        T("Do you offer satellite phone service for ships?", "handoff", "human", "en", escalation="no_evidence")],
      no_writes=True, case_reason="no_evidence"),
    S("T06", "Egyptian Arabic: NTRA complaint route after an unresolved complaint", "ar", None, [
        T("اشتكيت ومحدش رد عليا، اشتكي فين تاني؟", "answer", None, "ar", citations_include=["complaints_policy@v2#s3"], text_contains="155")],
      no_writes=True),
    S("T07", "Arabizi greeting then a FAQ question", "arabizi", None, [
        T("ahlan", "answer", None, "arabizi"),
        T("salefny b kam?", "answer", None, "arabizi", citations_include=["prepaid_recharge_policy@v3#s5"], text_contains="5")],
      no_writes=True),

    # ---------- lookups after identity ----------
    S("T10", "Arabizi: balance and bundle check for Mona after identity", "arabizi", "TC-D001", [
        T("3ayza a3raf rasedy w el megat el fadla", "verify_identity", ID, "arabizi"),
        T("01012340001 w a5er 4 ar2am 4821", "answer", None, "arabizi", text_contains="82.5", tools_called=["verify_customer", "get_line", "list_line_bundles"])],
      no_writes=True),
    S("T11", "Egyptian Arabic: wrong identity digits twice goes to a human", "ar", "TC-D001", [
        T("عايزة اعرف رصيدي", "verify_identity", ID, "ar"),
        T("01012340001 والرقم 1111", "verify_identity", ID, "ar"),
        T("01012340001 والرقم 2222", "handoff", "human", "ar", escalation="identity_failed")],
      no_writes=True, case_reason="identity_failed"),
    S("T12", "English: Karim asks why his bill is high; the agent points to the roaming line item", "en", "TC-D002", [
        T("Why is my bill 1,288 pounds this month?", "verify_identity", ID, "en"),
        T("01112340002, 7315", "answer", None, "en", text_contains="780.5", tools_called=["verify_customer", "get_invoice"])],
      no_writes=True),
    S("T13", "Arabizi: Hana's line is suspended because two bills are unpaid", "arabizi", "TC-D006", [
        T("el 5at wa2ef leh?? msh 3arfa attesel", "verify_identity", ID, "arabizi"),
        T("01112340006 - 5537", "answer", None, "arabizi", citations_include=["postpaid_billing_policy@v2#s3"], text_contains="467.82")],
      no_writes=True),
    S("T14", "Egyptian Arabic: Youssef's home internet is down because of a known outage", "ar", "TC-D007", [
        T("النت في البيت فاصل من امبارح الصبح", "verify_identity", ID, "ar"),
        T("الأرضي 0502234007 وآخر 4 أرقام 8812", "answer", None, "ar", text_contains="OUT-D007", tools_called=["verify_customer", "check_outage"])],
      no_writes=True),
    S("T15", "English: Laila asks about slow internet; a visit is already booked", "en", "TC-D008", [
        T("My internet is super slow again", "verify_identity", ID, "en"),
        T("landline 0342234008, last four 4107", "answer", None, "en", text_contains="13:00-17:00", tools_called=["verify_customer", "get_appointment"])],
      no_writes=True),
    S("T16", "Mixed: Fatma's line only receives calls after 93 days without use; a recharge before day 105 brings it back", "mixed", "TC-D016", [
        T("الخط بتاعي مش شغال خالص، inactive ليه؟", "verify_identity", ID, "ar"),
        T("01012340016 - 3365", "answer", None, "ar", citations_include=["prepaid_recharge_policy@v3#s4"])],
      no_writes=True),

    # ---------- actions with confirmation ----------
    S("T20", "Egyptian Arabic: Salma's mistaken bundle is refunded (within 24h, under 5% used)", "ar", "TC-D003", [
        T("اشتركت في باقة نت 100 بالغلط النهارده، رجعولي فلوسي", "verify_identity", ID, "ar"),
        T("01212340003 و 1946", "confirm", "confirmation", "ar", text_contains="100", citations_include=["refund_policy@v3#s1"]),
        T("ايوه", "execute", None, "ar", text_contains="RF-")],
      executed_tools=["refund_bundle"], audit_count=1),
    S("T21", "Egyptian Arabic: Omar's bundle is not refundable (3 days, 42% used)", "ar", "TC-D004", [
        T("عايز ألغي باقة الـ 200 ورجعولي فلوسها", "verify_identity", ID, "ar"),
        T("01512340004 - 6603", "refuse", None, "ar", citations_include=["refund_policy@v3#s1"])],
      no_writes=True),
    S("T22", "Arabic: Ahmed's duplicated Mix 75 charge refunded (75 EGP, within agent limit)", "ar", "TC-D009", [
        T("اتخصم مني تمن باقة الميكس مرتين امبارح بالليل", "verify_identity", ID, "ar"),
        T("01112340009 والرقم 3378", "confirm", "confirmation", "ar", text_contains="75", citations_include=["refund_policy@v3#s2"]),
        T("تمام اعمل كده", "execute", None, "ar", text_contains="RF-")],
      executed_tools=["refund_charge"], audit_count=1),
    S("T23", "English: Hossam's duplicated 300 EGP roaming bundle needs a supervisor (over 200 EGP)", "en", "TC-D017", [
        T("You charged me twice for the Gulf roaming bundle, 600 pounds gone!", "verify_identity", ID, "en"),
        T("01112340017 and 4482", "confirm", "confirmation", "en", text_contains="300"),
        T("yes please", "handoff", "human", "en", escalation="needs_approval")],
      no_writes=True, case_reason="needs_approval", approval_requested=True),
    S("T24", "Mixed: Nour's Games club is unsubscribed and the last 30 days refunded", "mixed", "TC-D005", [
        T("في service اسمها games club بتسحب مني رصيد كل يوم وانا مشتركتش", "verify_identity", ID, "ar"),
        T("01012340005 / 2290", "confirm", "confirmation", "ar", citations_include=["refund_policy@v3#s3"], text_contains="30"),
        T("ok تمام", "execute", None, "ar", text_contains="VAS-")],
      executed_tools=["unsubscribe_vas", "refund_vas"], audit_count=2),
    S("T25", "Egyptian Arabic: Mostafa's stolen phone, line suspended after confirmation", "ar", "TC-D013", [
        T("الموبايل اتسرق مني دلوقتي، وقفوا الخط بسرعة", "verify_identity", ID, "ar"),
        T("01112340013 - 2803", "confirm", "confirmation", "ar", citations_include=["sim_policy@v2#s3"]),
        T("ايوه وقفه", "execute", None, "ar", text_contains="SUS-")],
      executed_tools=["suspend_line"], audit_count=1),
    S("T26", "Mixed: Yasmin upgrades Control 50 to Control 90 (immediate, old quota cancelled)", "mixed", "TC-D018", [
        T("عايزة اعمل upgrade من كنترول 50 لكنترول 90", "verify_identity", ID, "ar"),
        T("01212340018 - 8126", "confirm", "confirmation", "ar", citations_include=["bundles_policy@v4#s4"]),
        T("yes", "execute", None, "ar")],
      executed_tools=["change_plan"], audit_count=1),
    S("T27", "Arabizi: Malak gets her PUK after identity", "arabizi", "TC-D020", [
        T("el shee7a bt6lob PUK", "verify_identity", ID, "arabizi"),
        T("01512340020 2217", "answer", None, "arabizi", citations_include=["sim_policy@v2#s6"], tools_called=["verify_customer", "get_puk"])],
      no_writes=True, audit_count=1),
    S("T28", "English: subscribe Mona to Net 30 (enough balance), then she says no", "en", "TC-D001", [
        T("Please add the Net 30 bundle to my line", "verify_identity", ID, "en"),
        T("01012340001 / 4821", "confirm", "confirmation", "en", text_contains="30"),
        T("actually no, leave it", "answer", None, "en")],
      no_writes=True),
    S("T29", "Arabizi: Mona's declined confirmation is not executed later by a stray yes", "arabizi", "TC-D001", [
        T("ashtrekly fe net 30", "verify_identity", ID, "arabizi"),
        T("01012340001 4821", "confirm", "confirmation", "arabizi"),
        T("la2 5alas msh 3ayza", "answer", None, "arabizi"),
        T("ok", "answer", None, "arabizi")],
      no_writes=True),

    # ---------- rule denials and approvals ----------
    S("T30", "Arabizi: Tarek can't port out yet (needs 4 months of ownership, line is 40 days old)", "arabizi", "TC-D011", [
        T("3ayez a2el ra2my le shabaka tanya", "verify_identity", ID, "arabizi"),
        T("01512340011 - 1172", "refuse", None, "arabizi", citations_include=["porting_policy@v2#s2"])],
      no_writes=True),
    S("T31", "Arabizi: Seif can't take Salefny again while one is outstanding", "arabizi", "TC-D015", [
        T("salefny msh rad ya tshta8al leh", "verify_identity", ID, "arabizi"),
        T("01512340015 5091", "refuse", None, "arabizi", citations_include=["prepaid_recharge_policy@v3#s5"], text_contains="10")],
      no_writes=True),
    S("T32", "English: Mariam's postpaid line has fewer than 4 paid bills, so roaming needs a 750 EGP deposit and goes to a colleague", "en", "TC-D010", [
        T("I'm flying to Riyadh tonight, please turn on roaming", "verify_identity", ID, "en"),
        T("01212340010, 9054", "handoff", "human", "en", escalation="needs_approval", citations_include=["roaming_policy@v2#s1"], text_contains="750")],
      no_writes=True, case_reason="needs_approval"),
    S("T33", "Mixed: Dina disputes a 650 EGP content charge (over 500 needs a supervisor)", "mixed", "TC-D012", [
        T("في charge 650 جنيه على الفاتورة اسمها premium content انا معملتهاش", "verify_identity", ID, "ar"),
        T("01012340012 - 6640", "confirm", "confirmation", "ar", text_contains="650", citations_include=["postpaid_billing_policy@v2#s4"]),
        T("ايوه افتح اعتراض", "handoff", "human", "ar", escalation="needs_approval")],
      no_writes=True, case_reason="needs_approval"),
    S("T34", "English: Rana's device is locked for two unpaid installments; agent explains, no unlock", "en", "TC-D014", [
        T("My iPhone is locked, unlock it now", "verify_identity", ID, "en"),
        T("01212340014 - 7719", "answer", None, "en", citations_include=["contract_policy@v1#s3"])],
      no_writes=True),

    # ---------- refusals that never touch tools ----------
    S("T40", "Egyptian Arabic: SIM swap over chat is always refused", "ar", "TC-D013", [
        T("ابعتولي شريحة جديدة بنفس الرقم على البيت", "refuse", None, "ar", citations_include=["sim_policy@v2#s2"])],
      no_writes=True, audit_count=0),
    S("T41", "English: call log request is refused and explained", "en", "TC-D002", [
        T("Send me the numbers I called last week", "refuse", None, "en", citations_include=["privacy_policy@v2#s2"])],
      no_writes=True),
    S("T42", "Arabizi: checking someone else's line is refused", "arabizi", "TC-D019", [
        T("3ayza a3raf rased 5at gozy 01512340019", "refuse", None, "arabizi", citations_include=["privacy_policy@v2#s3"])],
      no_writes=True, audit_count=0),
    S("T43", "Mixed: cancel line in chat is refused with store steps", "mixed", "TC-D010", [
        T("عايزة اعمل cancel للخط ده خالص", "refuse", None, "ar", citations_include=["contract_policy@v1#s4"])],
      no_writes=True),

    # ---------- mandatory handoffs ----------
    S("T50", "English: SIM-swap fraud report goes to a human urgently", "en", None, [
        T("Someone did a SIM swap on my number, my bank sent OTPs to them!", "handoff", "human", "en", escalation="mandatory_risk")],
      no_writes=True, case_reason="mandatory_risk", case_priority="urgent"),
    S("T51", "Egyptian Arabic: scam call impersonating the operator", "ar", None, [
        T("حد كلمني قال انه من مصر كونكت وطلب الكود اللي جالي في رسالة", "handoff", "human", "ar", escalation="mandatory_risk")],
      no_writes=True, case_reason="mandatory_risk", case_priority="urgent"),
    S("T52", "Arabizi: customer asks for a person", "arabizi", "TC-D006", [
        T("3ayza akalem 7ad bany adam law sama7t", "handoff", "human", "arabizi", escalation="customer_requested_human")],
      no_writes=True, case_reason="customer_requested_human"),
    S("T53", "Mixed: angry repeat complaint is escalated with a full summary", "mixed", "TC-D007", [
        T("دي تالت مرة اشتكي من النت ومحدش عمل حاجة!! worst service", "handoff", "human", "ar", escalation="high_frustration")],
      no_writes=True, case_reason="high_frustration"),

    # ---------- failures and multi-request ----------
    S("T60", "English: billing system down while looking up the bill", "en", "TC-D002", [
        T("What's my current bill?", "verify_identity", ID, "en"),
        T("01112340002 7315", "handoff", "human", "en", escalation="tool_failure")],
      no_writes=True, case_reason="tool_failure", setup_faults={"get_invoice": "timeout"}),
    S("T61", "Egyptian Arabic: refund tool result can't be confirmed, so no success is claimed", "ar", "TC-D009", [
        T("اتخصم مني الباقة مرتين", "verify_identity", ID, "ar"),
        T("01112340009 - 3378", "confirm", "confirmation", "ar"),
        T("ايوه", "handoff", "human", "ar", escalation="unverified_result")],
      case_reason="unverified_result", setup_faults={"refund_charge": "outcome_unknown"}),
    S("T62", "Arabizi: balance check plus roaming question in one message", "arabizi", "TC-D001", [
        T("3ayza a3raf rasedy w kaman afa3al el roaming ezay", "verify_identity", ID, "arabizi"),
        T("01012340001 4821", "answer", None, "arabizi", citations_include=["roaming_policy@v2#s1"], text_contains="82.5")],
      no_writes=True),
    S("T63", "English: Hossam asks for a refund amount he made up; agent uses the recorded charge", "en", "TC-D017", [
        T("Refund me 900 pounds for the double roaming charge", "verify_identity", ID, "en"),
        T("01112340017 / 4482", "confirm", "confirmation", "en", text_contains="300")],
      no_writes=True),
    S("T64", "Egyptian Arabic: ambiguous 'it stopped' asks which service", "ar", "TC-D007", [
        T("هو وقف تاني", "clarify", "detail", "ar")],
      no_writes=True),
    S("T65", "Mixed: Laila has two services; agent asks which one", "mixed", "TC-D008", [
        T("الـ internet بقى بطيء جدًا", "verify_identity", ID, "ar"),
        T("01012340008 - 4107", "clarify", "line_choice", "ar")],
      no_writes=True),
]
