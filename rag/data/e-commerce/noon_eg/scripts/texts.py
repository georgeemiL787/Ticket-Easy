"""Customer wording in four styles (en, ar = Egyptian Arabic, mixed = Arabic with English words, arabizi)
and agent-side resolution notes for past tickets. Templates only; generate.py picks among them with its seeded RNG."""

LANGS = ("en", "ar", "mixed", "arabizi")

# ------------------------------------------------------------------ return requests

def _days(lang, d):
    if lang == "en":
        return {0: "today", 1: "yesterday"}.get(d, f"{d} days ago")
    if lang == "ar":
        return {0: "النهارده", 1: "امبارح", 2: "من يومين"}.get(d, f"من {d} يوم" if d > 10 else f"من {d} أيام")
    if lang == "mixed":
        return {0: "النهارده", 1: "امبارح"}.get(d, f"من {d} days")
    return {0: "el naharda", 1: "embare7", 2: "men youmein"}.get(d, f"men {d} yom" if d > 10 else f"men {d} ayam")


COND = {
    "sealed": {"en": ["it's still sealed in the box", "I haven't opened it", "the box is unopened"],
               "ar": ["ولسه متفتحش", "ولسه في الكرتونة مقفول", "ومفتحتهوش خالص"],
               "mixed": ["والـ box لسه sealed", "ولسه متفتحش خالص", "والـ packaging زي ما هي"],
               "arabizi": ["w lessa ma fata7toosh", "w lessa fel 3elba ma2fool", "w el box zay ma howa"]},
    "opened": {"en": ["I opened the box but didn't like it", "I opened it and it's not what I expected"],
               "ar": ["فتحت العلبة ومعجبنيش", "فتحته ولقيته مش زي الصورة"],
               "mixed": ["فتحت الـ box ومش عاجبني", "عملت unboxing ومش زي الصورة"],
               "arabizi": ["fata7t el 3elba w ma 3agabnish", "fata7to w tele3 msh zay el soura"]},
    "used": {"en": ["I used it a couple of times", "I tried it for a few days"],
             "ar": ["استخدمته كام مرة", "جربته كام يوم"],
             "mixed": ["استخدمته كام مرة بس", "عملتله try كام يوم"],
             "arabizi": ["estakhdemto kam mara", "garrabto kam yom"]},
    "defective": {"en": ["it stopped working, looks like a manufacturing defect", "it doesn't turn on at all"],
                  "ar": ["مش شغال وفيه عيب مصنعي", "مبيشتغلش خالص من أول يوم"],
                  "mixed": ["فيه manufacturing defect ومش شغال", "مش بيشتغل خالص، defect من المصنع"],
                  "arabizi": ["msh shaghal w feeh 3eib masna3", "mabyeshtaghalsh khales men awel yom"]},
    "unworn_tags_intact": {"en": ["I never wore it and the tags are still on", "it's unworn with all tags attached"],
                           "ar": ["ملبستوش والتيكت لسه عليه", "ملبستوش خالص والتيكت موجود"],
                           "mixed": ["ملبستوش والـ tags لسه عليه", "لسه بالـ tags وملبستوش"],
                           "arabizi": ["ma lbestosh w el tags lessa 3aleih", "lessa bel tags w ma lbestosh"]},
    "worn": {"en": ["I wore it once", "I wore it to one event"],
             "ar": ["لبسته مرة واحدة", "لبسته في مشوار واحد"],
             "mixed": ["لبسته مرة واحدة بس", "لبسته one time بس"],
             "arabizi": ["lbesto mara wa7da", "lbesto f mshwar wa7ed bas"]},
    "tags_removed": {"en": ["I removed the tags", "the tags are already cut off"],
                     "ar": ["شلت التيكت", "قطعت التيكت"],
                     "mixed": ["شلت الـ tags", "الـ tags اتشالت"],
                     "arabizi": ["shelt el tags", "2ata3t el tags"]},
    "unwrapped": {"en": ["I took it out of its wrapping", "I unwrapped it"],
                  "ar": ["فتحته من الكيس", "طلعته من التغليف"],
                  "mixed": ["فتحته من الـ wrapping", "طلعته من الـ plastic"],
                  "arabizi": ["fata7to men el kis", "tala3to men el taghlif"]},
    "missing_parts": {"en": ["some parts are missing", "a few pieces are missing"],
                      "ar": ["ناقص منه حتت", "فيه قطع ناقصة"],
                      "mixed": ["ناقص منه parts", "فيه pieces ناقصة"],
                      "arabizi": ["na2es menno 7etat", "feeh 2eta3 na2sa"]},
    "assembled": {"en": ["I already assembled it", "I installed it already"],
                  "ar": ["ركبته خلاص", "ركبته وجربته"],
                  "mixed": ["عملتله assembly خلاص", "ركبته already"],
                  "arabizi": ["rakabto khalas", "rakabto w garrabto"]},
    "activated": {"en": ["I already activated it", "I switched it on and set it up"],
                  "ar": ["فعّلته خلاص", "شغلته وظبطته"],
                  "mixed": ["عملتله activate خلاص", "شغلته وعملت setup"],
                  "arabizi": ["fa3alto khalas", "shaghalto w zabatto"]},
    "damaged": {"en": ["it got damaged", "it's scratched and dented"],
                "ar": ["اتبهدل شوية", "فيه خدوش وكسر"],
                "mixed": ["حصله damage", "فيه scratches"],
                "arabizi": ["etbahdel shewaya", "feeh 5edoosh w kasr"]},
    "torn": {"en": ["a few pages are torn", "the cover is torn"],
             "ar": ["فيه صفحات متقطعة", "الغلاف اتقطع"],
             "mixed": ["فيه pages متقطعة", "الـ cover اتقطع"],
             "arabizi": ["feeh sa7afat mat2at3a", "el ghelaf et2ata3"]},
}

_OPEN = {"en": ["", "", "Hi, ", "Hello, ", "Good evening, ", "Hi there. "],
         "ar": ["", "", "السلام عليكم، ", "مساء الخير، ", "لو سمحت، "],
         "mixed": ["", "", "Hi، ", "لو سمحت، ", "Hello، "],
         "arabizi": ["", "", "salam, ", "hi, ", "law sama7t, "]}


def _want(lang, female):
    return {"ar": "عايزة" if female else "عايز", "mixed": "عايزة" if female else "عايز",
            "arabizi": "3ayza" if female else "3ayez", "en": "I'd like"}[lang]


def return_request(rnd, lang, female, item, days, cond, section):
    o, c, w = rnd.choice(_OPEN[lang]), rnd.choice(COND[cond][lang]), _want(lang, female)
    if lang in ("ar", "mixed") and not c.startswith("و"):
        c = "و" + c
    elif lang == "arabizi" and not c.startswith("w "):
        c = "w " + c
    d = _days(lang, days)
    m = section == "minutes"
    if lang == "en":
        src = " from noon Minutes" if m else ""
        got = rnd.choice([f"I received the {item}{src} {d}", f"My {item}{src} arrived {d}", f"I got the {item}{src} {d}"])
        ask = rnd.choice(["Can I return it?", "I'd like to return it.", "How do I send it back?", "Can I get a refund?"])
        return f"{o}{got} and {c}. {ask}"
    if lang == "ar":
        src = " من نون مينتس" if m else ""
        got = rnd.choice([f"استلمت الـ {item}{src} {d}", f"الـ {item}{src} وصلني {d}"])
        ask = rnd.choice(["ينفع أرجعه؟", f"{w} أرجعه.", "أرجعه إزاي؟", "ممكن أعمل مرتجع؟"])
        return f"{o}{got} {c}، {ask}"
    if lang == "mixed":
        src = " من noon Minutes" if m else ""
        got = rnd.choice([f"استلمت الـ {item}{src} {d}", f"الـ order{src} بتاع الـ {item} وصل {d}"])
        ask = rnd.choice(["ينفع أعمل return؟", f"{w} أعمل return.", "أعمل return إزاي؟", "ينفع refund؟"])
        return f"{o}{got} {c}، {ask}"
    src = " men noon Minutes" if m else ""
    got = rnd.choice([f"estalamt el {item}{src} {d}", f"el {item}{src} wesel {d}"])
    ask = rnd.choice(["momken arga3o?", f"{w} arga3o.", "a3mel return ezay?", "ynfa3 refund?"])
    return f"{o}{got} {c}, {ask}"


# ------------------------------------------------------------------ refund-status and other questions

REFUND = {
    "timeline": {
        "en": ["I sent back the {item} and I'm still waiting. When will I get my money?",
               "My return for the {item} is on its way to you. How long until the refund?",
               "What happens now with my {item} return and when is the refund?"],
        "ar": ["رجعت الـ {item} ولسه مستني، الفلوس هترجع إمتى؟", "المرتجع بتاع الـ {item} لسه في السكة، الريفند هياخد قد إيه؟"],
        "mixed": ["عملت return للـ {item} ولسه مستني الـ refund، هياخد قد إيه؟", "الـ return بتاع الـ {item} لسه processing، الفلوس هترجع إمتى؟"],
        "arabizi": ["raga3t el {item} w lessa mestanni, el flous hatrga3 emta?", "el return bta3 el {item} lessa fel sekka, el refund hayakhod ad eh?"]},
    "card": {
        "en": ["My return was approved {n} days ago. When will I see the money on my card?",
               "The refund for the {item} started but nothing is on my credit card yet."],
        "ar": ["المرتجع اتقبل من {n} أيام، الفلوس هتنزل على الكارت إمتى؟", "الريفند بتاع الـ {item} بدأ بس لسه مفيش حاجة على الفيزا."],
        "mixed": ["الـ refund اتعمل من {n} days ولسه مش ظاهر على الـ card.", "الـ return اتقبل بس الفلوس لسه منزلتش على الـ visa."],
        "arabizi": ["el refund et3amal men {n} ayam w lessa msh zaher 3al card.", "el return et2abal bas el flous lessa ma nezletsh 3al visa."]},
    "wallet": {
        "en": ["I paid cash on delivery for the {item}. Where does my refund go?", "Where is the refund for my {item}? I paid cash."],
        "ar": ["دفعت كاش للـ {item}، الفلوس بترجع فين؟", "أنا دافع كاش عند الاستلام، الريفند هيرجعلي إزاي؟"],
        "mixed": ["دفعت cash on delivery، الـ refund بيروح فين؟", "الـ refund بتاع الـ {item} هيرجع إزاي وأنا دافع cash؟"],
        "arabizi": ["dafa3t cash 3and el estelam, el refund byro7 fen?", "el flous bta3et el {item} hatrga3li ezay w ana dafe3 cash?"]},
    "bank": {
        "en": ["The refund went to my noon wallet. Can I move it to my bank account?"],
        "ar": ["الريفند نزل في محفظة نون، ينفع أحوله على حسابي في البنك؟"],
        "mixed": ["الـ refund نزل في الـ noon wallet، ينفع أحوله للـ bank account؟"],
        "arabizi": ["el refund nezel fel noon wallet, momken a7awelo 3ala el bank?"]},
    "bnpl": {
        "en": ["My {provider} refund started {n} days ago and I still don't see it.", "I paid with {provider}. How long does the refund take?"],
        "ar": ["الريفند على {provider} بدأ من {n} أيام ولسه مش ظاهر.", "أنا دافع بـ {provider}، الريفند بياخد قد إيه؟"],
        "mixed": ["الـ refund على {provider} بدأ من {n} days ولسه مش ظاهر.", "دفعت بـ {provider}، الـ refund بياخد قد إيه؟"],
        "arabizi": ["el refund 3ala {provider} bada2 men {n} ayam w lessa msh zaher.", "dafa3t b {provider}, el refund byakhod ad eh?"]},
    "emi": {
        "en": ["I bought the {item} in installments on my credit card. Can the refund go to my noon wallet instead?"],
        "ar": ["اشتريت الـ {item} بالتقسيط على الكريدت كارد، ينفع الفلوس ترجع في محفظة نون؟"],
        "mixed": ["اشتريت الـ {item} بالـ installments على الـ credit card، ينفع الـ refund يروح الـ wallet؟"],
        "arabizi": ["eshtareit el {item} bel ta2seet 3al credit card, momken el refund yro7 el wallet?"]},
    "approved_overdue": {
        "en": ["The refund says Approved for {n} days but nothing reached my card.", "My refund was approved weeks ago and the money never arrived."],
        "ar": ["الريفند مكتوب Approved من {n} يوم ومفيش حاجة وصلت للكارت.", "الريفند اتوافق عليه من أسابيع والفلوس موصلتش."],
        "mixed": ["الـ refund status بقاله {n} يوم Approved ومفيش فلوس على الـ card.", "الـ refund approved من زمان والفلوس موصلتش."],
        "arabizi": ["el refund maktoob Approved men {n} yom w mafeesh 7aga weslet lel card.", "el refund et-approve men asabee3 w el flous ma weseletsh."]},
    "rejected_hub": {
        "en": ["My return was rejected and nobody delivered the {item} back to me.", "The {item} return got rejected and the courier never reached me. I want it back."],
        "ar": ["المرتجع اترفض ومحدش رجعلي الـ {item}.", "الـ {item} اترفض في المرتجع والمندوب موصليش، عايزه يرجع."],
        "mixed": ["الـ return اترفض والـ {item} مرجعليش.", "الـ return بتاعي اتعمله reject والمندوب ماجاش."],
        "arabizi": ["el return bta3y et3amal reject w 3ayez el {item} yegi tany.", "el return et-rafad w mafeesh 7ad gabli el {item}."]},
    "returned_back": {
        "en": ["You sent the {item} back to me and I got no refund. Why?", "My {item} came back to me instead of a refund."],
        "ar": ["الـ {item} رجعلي تاني ومفيش ريفند، ليه؟", "بعتوا الـ {item} تاني بدل ما ترجعوا الفلوس."],
        "mixed": ["الـ {item} رجعلي ومفيش refund، ليه؟", "الـ return اترفض والـ {item} رجعلي."],
        "arabizi": ["el {item} rege3li tany w mafeesh refund, leih?", "ba3atoli el {item} tany badal el refund."]},
    "disposed": {
        "en": ["I never got my rejected {item} back. Can I still get it or a refund?"],
        "ar": ["الـ {item} المرفوض عمره ما رجعلي، ينفع أخده أو أخد فلوسه؟"],
        "mixed": ["الـ {item} اللي اترفض مرجعليش، ينفع أخده أو آخد refund؟"],
        "arabizi": ["el {item} elli et-rafad 3omro ma rege3li, momken akhdo aw akhod el flous?"]},
    "partial_fees": {
        "en": ["I paid {paid} EGP but the refund was only {refund} EGP. Where is the rest?"],
        "ar": ["دفعت {paid} جنيه بس الريفند جه {refund} بس، فين الباقي؟"],
        "mixed": ["دفعت {paid} EGP بس الـ refund جه {refund} بس، فين الباقي؟"],
        "arabizi": ["dafa3t {paid} geneh bas el refund kan {refund} bas, fen el ba2y?"]},
    "cancel": {
        "en": ["Please cancel my order before it arrives.", "I want to cancel the order, I don't need it anymore.", "Can I still cancel this order?"],
        "ar": ["عايز ألغي الأوردر قبل ما يوصل.", "لو سمحت ألغوا الطلب، مش محتاجه.", "ينفع ألغي الأوردر ده؟"],
        "mixed": ["عايز أعمل cancel للـ order قبل ما يوصل.", "ينفع أعمل cancel للـ order؟"],
        "arabizi": ["3ayez alghi el order abl ma yewsal.", "momken a3mel cancel lel order?", "law sama7t elghy el order."]},
}


def refund_message(rnd, lang, female, kind, **ctx):
    text = rnd.choice(REFUND[kind][lang]).format(**ctx)
    if lang in ("ar", "mixed") and female:
        text = text.replace("عايز ", "عايزة ").replace("مستني", "مستنية").replace("دافع", "دافعة")
    if lang == "arabizi" and female:
        text = text.replace("3ayez", "3ayza").replace("mestanni", "mestanneya").replace("dafe3", "daf3a")
    return text


# ------------------------------------------------------------------ past-ticket resolutions (English, like the seed tickets)

CAT_LABEL = {"electronics": "electronics", "beauty_health": "beauty and health", "fashion": "fashion",
             "home_kitchen": "home and kitchen", "sports": "sports", "toys": "toys", "baby": "baby",
             "grocery": "grocery", "pet_supplies": "pet supplies", "automotive": "automotive", "tools": "tools",
             "books": "books", "stationery": "stationery"}


def return_resolution(rnd, reason, section, cat, sub, cond, days):
    label, window = CAT_LABEL[cat], 14 if section == "minutes" else 15
    if reason == "WITHIN_POLICY":
        return rnd.choice([
            f"Eligible {label} item ({cond.replace('_', ' ')}) on day {days} of the {window}-day window: return request opened and pickup scheduled.",
            f"Checked the tag, condition and the {window}-day window; all met. Return created and the customer told the refund follows quality control."])
    if reason == "OUTSIDE_WINDOW":
        return f"Delivered {days} days ago; the {'Minutes' if section == 'minutes' else 'Noon'} section window is {window} days, so the return was declined and the policy explained."
    if reason == "NO_RETURN_TAG":
        return "The product does not carry the returnable tag, so it cannot be returned under any circumstances; explained the tag rule."
    if reason == "CATEGORY_EXCLUDED":
        return f"{sub.replace('_', ' ').capitalize()} items are not eligible for return under the {label} rules; return declined and the rule explained."
    if reason == "CONDITION_NOT_MET":
        return f"The item is {cond.replace('_', ' ')}, which the {label} return conditions do not accept; return declined."
    if reason == "WARRANTY_SERVICE_CENTER":
        return "Warranty-tagged Minutes products are not returned through noon; customer referred to the seller's or manufacturer's service center."
    if reason == "POLICY_CONFLICT":
        return "Defect reported on an opened Minutes item: the 30-day defect window and the opened-item rule conflict, so the case was escalated to customer care."
    if reason == "IDENTITY_REQUIRED":
        return "Customer could not complete verification, so no order details were shared and no return was created."
    raise ValueError(reason)


REFUND_RESOLUTION = {
    "timeline": "Explained the timeline: up to one week for the item to reach noon, up to 2 business days to process it, then 7 to 14 business days to the card or straight to the noon wallet for cash.",
    "card": "Card refunds show as noon credits for 24 hours, then move to the card within 14 days depending on the bank; customer asked to wait and check My Account.",
    "wallet": "Cash on delivery refunds go to the noon wallet; amounts above the stated limit can be moved to a bank account.",
    "bank": "Explained that a cash refund in the noon wallet above the stated limit can be transferred to a bank account; transfer requested.",
    "bnpl": "BNPL refunds take 14 to 21 days depending on the partner; still inside the time frame, customer asked to wait.",
    "emi": "Orders paid with credit card EMI can only be refunded to the same credit card; the bank may charge pre-closure fees.",
    "approved_overdue": "Approved and past the standard time frame: advised the customer to contact their bank with the refund reference.",
    "rejected_hub": "Rejected item after failed delivery attempts and a hub hold; hold and request windows on the page disagree, so customer care arranged a redelivery manually.",
    "returned_back": "The returned item failed the return conditions (Rejected Item), so it was delivered back and no refund applies; explained the rule.",
    "disposed": "The rejected item could not be redelivered and was disposed of after the reminder period; no refund applies under the policy.",
    "partial_fees": "Refunds are item by item and the shipping and COD fees are not refundable after delivery; explained the difference.",
    "cancel": "Order cancellation is not covered by the return policy page; handed to customer care to cancel before dispatch.",
}
REFUND_TICKET_CATEGORY = {
    "timeline": "refund_timing", "card": "refund_timing", "wallet": "refund_method", "bank": "refund_method",
    "bnpl": "bnpl_refund", "emi": "emi_refund", "approved_overdue": "refund_status", "rejected_hub": "rejected_return",
    "returned_back": "rejected_return", "disposed": "rejected_return", "partial_fees": "refund_amount",
    "cancel": "cancellation",
}
