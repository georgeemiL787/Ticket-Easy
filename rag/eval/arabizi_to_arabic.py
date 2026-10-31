"""Write a derived labels file where Arabizi questions/replies are Egyptian Arabic script.

The original faithfulness_labels.jsonl is not modified. Faithfulness scores the reply
against the cited passage, so the reply is what we convert; the question is converted
too so the sample is fully Arabic. Meaning, including the errors, is kept.
"""
from __future__ import annotations

import json
from pathlib import Path

from team_a.eval_rag import LABELS_FILE, load_labels
from team_a.knowledge.index import TenantIndex

OUT = Path(__file__).with_name("faithfulness_labels_ar_script.jsonl")

# id -> (question_ar, reply_ar). Errors stay errors (30 days stays 30, etc.).
ARABIZI = {
    "az-syn-ship": (
        "الشحن بكام؟",
        "الشحن 50 جنيه للقاهرة والجيزة، و70 للباقي. لو الأوردر 1500 أو أكتر بعد الخصومات، الشحن ببلاش.",
    ),
    "az-syn-return": (
        "كام يوم عندي عشان أرجع؟",
        "عندك 14 يوم من تاريخ الاستلام. بعد كده مفيش استرجاع.",
    ),
    "az-syn-cod": (
        "في دفع عند الاستلام؟",
        "أيوه، الدفع عند الاستلام لحد 5000 جنيه. فوق كده لازم تدفع قبلها.",
    ),
    "az-syn-30d": (
        "كام يوم عندي عشان أرجع؟",
        "عندك 30 يوم ترجيع من غير شروط.",
    ),
    "az-syn-free": (
        "الشحن بكام؟",
        "الشحن ببلاش على طول على أي أوردر.",
    ),
    "az-syn-cod-nocap": (
        "في دفع عند الاستلام؟",
        "أيوه كاش عند الاستلام على أي مبلغ.",
    ),
    "az-syn-cod3k": (
        "في دفع عند الاستلام؟",
        "أيوه الدفع عند الاستلام لحد 3000 جنيه.",
    ),
    "az-syn-pickup1": (
        "المندوب هيجي ياخد الرجوع إمتى؟",
        "بعد ما نقبل الطلب، المندوب بيجيلك في يوم شغل واحد.",
    ),
    "az-syn-wrongsec-review": (
        "الريفند بياخد كام يوم على الكارت؟",
        "لو الريفند فوق 3000 جنيه، لازم خدمة العملاء توافق قبل ما يتنفذ. مش أوتوماتيك.",
    ),
    "az-syn-wrongsec-gift": (
        "مواعيد خدمة العملاء إمتى؟",
        "في تغليف هدايا مع كارت إهداء بـ 30 جنيه على الأوردر.",
    ),
    "az-syn-inv-sameday": (
        "الشحن بيوصل إمتى في القاهرة؟",
        "في القاهرة الشحن في نفس اليوم، بنوصل الأوردر في نفس اليوم.",
    ),
    "az-syn-inv-mall": (
        "عندكم فرع؟",
        "أيوه، عندنا فرع في مول مصر تيجي تشوف الحاجات.",
    ),
    "az-syn-half-nowallet": (
        "في دفع عند الاستلام؟",
        "أيوه الدفع عند الاستلام لحد 5000 جنيه. المحافظ الإلكترونية مش مقبولة عندنا.",
    ),
    "az-syn-half-cashsame": (
        "الريفند بياخد كام يوم على الكارت؟",
        "الكارت 7 لـ 14 يوم شغل. لو دفعت كاش، الفلوس تترجع لك في نفس اليوم.",
    ),
    "az-syn-voucher200": (
        "الأوردر اتأخر، فيه تعويض؟",
        "لو وصل بعد آخر ميعاد بأكتر من 3 أيام شغل، بتاخد قسيمة 200 جنيه.",
    ),
    "az-syn-half-closed-fri": (
        "مواعيد خدمة العملاء إمتى؟",
        "التيم من 10 الصبح لـ 10 الليل كل يوم، بس بيقفلوا الجمعة.",
    ),
    "az-syn-ok-delay": (
        "الأوردر اتأخر، فيه تعويض؟",
        "لو وصل بعد آخر ميعاد التوصيل بأكتر من 3 أيام شغل، بتاخد قسيمة 100 جنيه على الأوردر الجديد. أي تعويض أكبر بيروح لخدمة العملاء.",
    ),
    "az-syn-ok-size": (
        "أنا بين مقاسين، آخد أنهي؟",
        "لو إنت بين مقاسين، خد الأكبر. في جدول على صفحة المنتج.",
    ),
    "az-01": ("إمتى أقدر أرجع المنتج؟", None),
    "az-02": ("كام يوم عندي عشان أرجع؟", None),
    "az-11": ("في دفع عند الاستلام؟", None),
    "az-15": ("الريفند بياخد كام يوم؟", None),
}

LEAD = ("Da elly betqolo siyastena:", "ده اللي بتقوله سياستنا:")


def main() -> None:
    samples = load_labels(LABELS_FILE, TenantIndex.load("shop_001"))
    out = []
    for s in samples:
        question, reply = s.question, s.reply
        if s.style == "arabizi":
            mapped = ARABIZI.get(s.id)
            if mapped:
                question = mapped[0]
                if mapped[1] is not None:
                    reply = mapped[1]
                else:
                    reply = s.reply.replace(*LEAD)
            else:
                reply = s.reply.replace(*LEAD)
        out.append({
            "id": s.id,
            "style": s.style,
            "question": question,
            "reply": reply,
            "citations": s.retrieved,
            "label": int(s.label) if s.label is not None else None,
            "reason": s.label_reason,
        })
    OUT.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in out), encoding="utf-8")
    n_az = sum(1 for r in out if r["style"] == "arabizi")
    print(f"wrote {len(out)} rows ({n_az} arabizi converted) to {OUT.name}")


if __name__ == "__main__":
    main()
