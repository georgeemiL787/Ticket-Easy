"""The fixed sentences the agent says, in the customer's own style (English, Egyptian Arabic, Arabizi).

Minimal for now: only what the turn pipeline needs. Every key has all three locales. The reply composer replaces this
module with the full set of templates and shop-specific wording.
"""

from collections.abc import Mapping

from team_b.domain.understanding import Locale

EN, AR, AZ = Locale.EN, Locale.AR, Locale.ARABIZI

TEMPLATES: Mapping[str, Mapping[Locale, str]] = {
    "greeting": {
        EN: "Hello! How can I help you today?",
        AR: "أهلاً بيك! أقدر أساعدك إزاي؟",
        AZ: "Ahlan beek! a2dar asa3dak ezay?",
    },
    "thanks": {
        EN: "You're welcome! Is there anything else I can help you with?",
        AR: "العفو! تحب أساعدك في حاجة تانية؟",
        AZ: "El 3afw! te7eb asa3dak fe 7aga tanya?",
    },
    "clarify_generic": {
        EN: "Could you tell me a little more about what you need help with?",
        AR: "ممكن توضحلي أكتر محتاج مساعدة في إيه؟",
        AZ: "Momken twadda7ly aktar me7tag mosa3da fe eh?",
    },
    "ask_order_id": {
        EN: "Sure, I can help with that. What is your order number?",
        AR: "تمام، أقدر أساعدك في ده. إيه رقم الأوردر؟",
        AZ: "Tamam, a2dar asa3dak fe da. eh ra2am el order?",
    },
    "ask_phone": {
        EN: "Thank you. To confirm that it is you, please send the phone number on the order.",
        AR: "شكرًا. عشان أتأكد إنه حضرتك، ابعتلي رقم التليفون المسجل على الأوردر.",
        AZ: "Shokran. 3ashan at2akked enaha 7adretak, ab3atly ra2am el telephone el mosajjal 3ala el order.",
    },
    "ask_item": {
        EN: "Which item is this about?",
        AR: "ده بخصوص أنهي منتج؟",
        AZ: "Da bekhosos anhy montag?",
    },
    "ask_reason": {
        EN: "What is the reason? A few words are enough.",
        AR: "إيه السبب؟ كلمتين كفاية.",
        AZ: "Eh el sabab? kelmetein kefaya.",
    },
    "ask_amount": {
        EN: "What amount are you asking for?",
        AR: "إيه المبلغ المطلوب؟",
        AZ: "Eh el mablagh el matloub?",
    },
    "ask_new_address": {
        EN: "What is the new delivery address?",
        AR: "إيه عنوان التوصيل الجديد؟",
        AZ: "Eh 3enwan el tawseel el gedeed?",
    },
    "ask_description": {
        EN: "Please describe the problem in a few words.",
        AR: "ممكن توصفلي المشكلة في كلمتين؟",
        AZ: "Momken twasafly el moshkela fe kelmetein?",
    },
    "ask_generic": {
        EN: "Could you tell me your {slot}?",
        AR: "ممكن تقولي {slot}؟",
        AZ: "Momken te2olly {slot}?",
    },
    "disambiguate_intent": {
        EN: "Do you want to {a} or {b}? You can say the first or the second.",
        AR: "تقصد {a} ولا {b}؟ ممكن تقول الأول أو التاني.",
        AZ: "Te2sod {a} wala {b}? momken te2ol el awel aw el tany.",
    },
    "ask_order_choice": {
        EN: "Which order do you mean?\n{orders}\nYou can say the first, the second, or send the order number.",
        AR: "تقصد أنهي أوردر؟\n{orders}\nممكن تقول الأول أو التاني، أو تبعتلي رقم الأوردر.",
        AZ: "Te2sod anhy order?\n{orders}\nMomken te2ol el awel aw el tany, aw tebba3atly ra2am el order.",
    },
    "confirm_again": {
        EN: "Please answer yes to go ahead or no to cancel.",
        AR: "لو سمحت قولي أيوه عشان أكمّل أو لأ عشان ألغي.",
        AZ: "Law samaht olly aywa 3ashan akammel aw la2 3ashan alghy.",
    },
    "action_cancelled": {
        EN: "Okay, I won't do that. Is there anything else I can help with?",
        AR: "تمام، مش هعمل ده. في حاجة تانية أقدر أساعدك فيها؟",
        AZ: "Tamam, mesh ha3mel da. fe 7aga tanya a2dar asa3dak fiha?",
    },
    "handoff_customer_request": {
        EN: "Of course. I'm connecting you with a colleague from our customer care team now.",
        AR: "أكيد. هحولك دلوقتي لزميل من فريق خدمة العملاء.",
        AZ: "Akeed. ha7awelak delwa2ty le zameel men fari2 khedmet el 3omala2.",
    },
    "handoff_mandatory_risk": {
        EN: "I'm passing this to our customer care team right away so a colleague can look into it personally.",
        AR: "هحول الموضوع ده فورًا لفريق خدمة العملاء عشان زميل يتابعه معاك بنفسه.",
        AZ: "Ha7awel el mawdoo3 da fawran le fari2 khedmet el 3omala2 3ashan zameel yetab3o ma3ak benafso.",
    },
    "handoff_generic": {
        EN: "I'm connecting you with a colleague who can help you with this.",
        AR: "هحولك لزميل يقدر يساعدك في ده.",
        AZ: "Ha7awelak le zameel ye2dar yesa3dak fe da.",
    },
    "handed_off_wait": {
        EN: "A colleague will reply to you here shortly.",
        AR: "زميل من فريق خدمة العملاء هيرد عليك هنا في أقرب وقت.",
        AZ: "Zameel men fari2 khedmet el 3omala2 hayrod 3aleik hena fe a2rab wa2t.",
    },
}


def render(key: str, locale: Locale, **values: str) -> str:
    """The sentence for `key` in `locale`, with {placeholders} filled from values. An unknown key is a bug."""
    try:
        template = TEMPLATES[key][locale]
    except KeyError:
        raise KeyError(f"no template {key!r} for locale {locale.value!r}") from None
    return template.format(**values)
