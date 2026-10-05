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
