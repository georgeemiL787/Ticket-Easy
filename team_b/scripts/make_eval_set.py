"""Write the conversation evaluation set to eval/conversations/<style>.jsonl: 50 conversations per language style.

The gold labels here are decided by what the shop's policies and business rules say should happen, never by what the
brain currently does: a wrong gold label would hide a bug, and tuning the brain to the labels would hide it too. Edit
this file to change the set, then run it again (`python scripts/make_eval_set.py`).

Topics (every style has the same topics, written in its own words):
  K1..K7  policy questions answered from a passage (gold: decision answer, the passage ids that may be cited)
  T1      greeting or thanks            T7  asks for a person (handoff customer_request)
  T2      order status, no order id     T8  out of scope (a question or a refusal, nothing invented)
  T3      order status, with order id   T9  unknown policy question, asked twice (ask to rephrase, then no_evidence)
  T4      refund, with order id         T10 order status then the phone number (identity check, then the status)
  T5      return or exchange, no id     T11 very angry customer (high_frustration)
  T6      cancel, with order id         T12 fraud report (mandatory_risk)
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "eval" / "conversations"

R2 = "return_policy@v2#s2"
SHIP = ["shipping_policy@v1#s3", "shipping_policy@v1#s2"]
COD = ["shipping_policy@v1#s4", "faq@v1#q02"]
REFUND = ["refund_policy@v1#s2"]
BROKEN = ["return_policy@v2#s6"]
GIFT = ["faq@v1#q05"]
HOURS = ["faq@v1#q01"]

# (text, gold intent) pairs for the topics that need one intent each; plain strings for the rest.
STYLES: dict[str, dict[str, object]] = {
    "en": {
        "K1": [
            "How many days do I have to return an item?",
            "Can I return my order after 10 days?",
            "what is the return window",
            "How long do I have to send back a product?",
            "Is it possible to return something I bought 12 days ago?",
            "tell me the time limit for returns",
        ],
        "K2": [
            "How much does shipping cost?",
            "what are your delivery fees",
            "how much do you charge for delivery",
            "what is the shipping fee",
        ],
        "K3": ["do you accept cash on delivery?", "can I pay when the order arrives", "is cash on delivery available"],
        "K4": [
            "when will my refund reach my card?",
            "how long does a refund take",
            "how many days until I get my money back",
        ],
        "K5": [
            "the item I received is broken, what now",
            "my product arrived damaged what should I do",
            "I got a defective item, who do I tell",
        ],
        "K6": ["do you have gift wrapping?", "can you wrap my order as a gift"],
        "K7": ["what are your customer service hours", "when is customer support open"],
        "T1": ["Hello", "thanks a lot", "good morning"],
        "T2": ["where is my order", "when will my package arrive", "has my order shipped yet"],
        "T3": ["Where is my order NS-20877?", "track NS-20512 please", "what is the status of order NS-20934"],
        "T4": ["I want a refund for order NS-20745", "please refund NS-20512", "give me my money back for NS-20877"],
        "T5": [
            ("I want to return my order", "return_request"),
            ("I need to exchange the size", "exchange_request"),
            ("this jacket is too small, I want to send it back", "return_request"),
        ],
        "T6": ["Please cancel my order NS-20960", "cancel NS-20512 I changed my mind"],
        "T7": ["I want to talk to a human", "let me speak to a person", "connect me to a customer service agent"],
        "T8": ["What is the weather in Cairo today?", "Tell me a joke", "who won the match yesterday"],
        "T9": ["Do you offer a five year warranty on electronics?", "I mean the warranty for electronic devices"],
        "T10": ["Where is my order NS-20877?", "01012345601"],
        "T11": "This is ridiculous!!! Worst service ever, I am so angry, stop asking me questions!!!",
        "T12": "someone used my card without my permission, this is fraud",
    },
    "ar": {
        "K1": [
            "ممكن ارجع المنتج بعد كام يوم من الاستلام؟",
            "مدة الاسترجاع كام يوم؟",
            "اقدر ارجع الحاجة بعد ١٠ ايام؟",
            "عندي كام يوم عشان اعمل استرجاع؟",
            "هل ينفع ارجع طلب اشتريته من اسبوعين؟",
            "ايه سياسة الاسترجاع؟",
        ],
        "K2": ["الشحن بكام؟", "مصاريف التوصيل كام؟", "بتاخدوا كام على الشحن؟", "كام رسوم التوصيل؟"],
        "K3": ["هل في دفع عند الاستلام؟", "اقدر ادفع كاش لما الاوردر يوصل؟", "الدفع عند الاستلام متاح؟"],
        "K4": ["الفلوس هترجع امتى لو دفعت بالكارت؟", "الاسترداد بياخد قد ايه؟", "امتى استلم فلوسي بعد الاسترجاع؟"],
        "K5": ["المنتج وصلني مكسور اعمل ايه؟", "استلمت حاجة بايظة اعمل ايه؟", "الحاجة جاتلي بعيب مين اكلمه؟"],
        "K6": ["في تغليف هدايا؟", "ينفع تغلفوا الاوردر هدية؟"],
        "K7": ["ايه مواعيد خدمة العملاء؟", "خدمة العملاء بتشتغل امتى؟"],
        "T1": ["ازيك", "شكرا ليك", "صباح الخير"],
        "T2": ["الاوردر بتاعي وصل فين؟", "امتى الاوردر هيوصل؟", "الشحنة اتشحنت ولا لسه؟"],
        "T3": ["الاوردر NS-20877 وصل فين؟", "عايز اتتبع NS-20512", "ايه حالة الاوردر NS-20934؟"],
        "T4": ["عايز استرداد فلوس الاوردر NS-20745", "ارجعولي فلوس NS-20512 لو سمحت", "عايز فلوسي بتاعة NS-20877"],
        "T5": [
            ("عايز ارجع الاوردر بتاعي", "return_request"),
            ("عايز استبدل المقاس", "exchange_request"),
            ("الجاكيت صغير عليا عايز ارجعه", "return_request"),
        ],
        "T6": ["لو سمحت الغي الاوردر NS-20960", "الغي NS-20512 غيرت رأيي"],
        "T7": ["عايز اكلم حد من خدمة العملاء", "ممكن اتكلم مع موظف؟", "وصلني بحد بشري"],
        "T8": ["الطقس عامل ايه النهاردة؟", "قولي نكتة", "مين كسب الماتش امبارح؟"],
        "T9": ["هل عندكم ضمان خمس سنين على الاجهزة؟", "اقصد الضمان على الاجهزة الالكترونية"],
        "T10": ["الاوردر NS-20877 وصل فين؟", "01012345601"],
        "T11": "اسوأ خدمة، مش معقول، قرفتوني!!!",
        "T12": "حد سحب فلوس من الكارت بتاعي من غير اذني دي نصب",
    },
    "mixed": {
        "K1": [
            "ممكن اعمل return للمنتج بعد كام يوم من الـ delivery؟",
            "الـ return window كام يوم؟",
            "اقدر اعمل return بعد 10 days؟",
            "كام يوم عندي عشان ارجع الـ item؟",
            "ايه الـ return policy عندكم؟",
            "ينفع ارجع order من اسبوعين؟",
        ],
        "K2": [
            "الـ shipping cost كام؟",
            "كام مصاريف الشحن to Alexandria",
            "بتاخدوا كام على الـ delivery؟",
            "الـ delivery fee كام؟",
        ],
        "K3": ["فيه cash on delivery؟", "اقدر ادفع cash لما الـ order يوصل؟", "الدفع عند الاستلام available؟"],
        "K4": ["الـ refund هيرجع امتى على الكارت؟", "الـ refund بياخد كام يوم؟", "امتى هاخد فلوسي بعد الـ return؟"],
        "K5": [
            "الـ product وصلني مكسور اعمل ايه؟",
            "استلمت item damaged اعمل ايه؟",
            "الحاجة وصلت defective مين اكلمه؟",
        ],
        "K6": ["فيه gift wrapping؟", "تقدروا تعملوا wrap للـ order كـ gift؟"],
        "K7": ["الـ customer service بتشتغل امتى؟", "ايه مواعيد الـ support؟"],
        "T1": ["hi، ازيك", "thanks يا باشا", "good morning يا فندم"],
        "T2": ["where is الاوردر بتاعي", "الـ order بتاعي وصل فين؟", "الـ package اتشحن ولا لسه؟"],
        "T3": [
            "فين الـ package بتاعي NS-20877",
            "ممكن اعرف الـ status بتاع الاوردر NS-20512",
            "الـ tracking للاوردر NS-20934",
        ],
        "T4": ["عايز refund للاوردر NS-20512", "ارجعولي الـ money بتاع NS-20745", "عايز الـ refund لـ NS-20877"],
        "T5": [
            ("I want to return المنتج ده", "return_request"),
            ("عايز اعمل exchange للـ size", "exchange_request"),
            ("الـ jacket صغيرة عايز ارجعها", "return_request"),
        ],
        "T6": ["cancel الاوردر NS-20960 لو سمحت", "عايز اعمل cancel لـ NS-20512"],
        "T7": ["عايز اكلم agent", "customer service لو سمحت", "وصلني بـ human"],
        "T8": ["الـ weather عامل ايه النهاردة؟", "عايز اطلب pizza", "احجزلي flight لدبي"],
        "T9": ["هل عندكم warranty خمس سنين على الـ electronics؟", "اقصد الـ warranty للأجهزة"],
        "T10": ["where is order NS-20877 بتاعي", "01012345601"],
        "T11": "الـ service ده worst حاجة، مش معقول!!!",
        "T12": "حد استخدم الـ card بتاعي من غير إذني ده fraud",
    },
    "arabizi": {
        "K1": [
            "emta a2dar araga3 el montag?",
            "kam yom 3andy 3shan araga3?",
            "a2dar araga3 ba3d 10 ayam?",
            "mo3det el estergaa3 kam?",
            "eh siyasit el estergaa3?",
            "yenfa3 araga3 order eshtareto mn esbo3ein?",
        ],
        "K2": ["el shahn bekam?", "masarif el tawsil kam?", "betakhdo kam 3ala el shahn?", "kam resoum el tawsil?"],
        "K3": ["fi dafa3 3and el estlam?", "a2dar adfa3 cash lama el order yewsal?", "el dafa3 3and el estlam mota7?"],
        "K4": [
            "flousi hatrga3 emta lw dafa3t bel visa?",
            "el refund byakhod kam yom?",
            "emta ageeb floosy ba3d el estergaa3?",
        ],
        "K5": [
            "el montag weselny maksour a3mel eh?",
            "estalamt haga battala a3mel eh?",
            "el 7aga gatly bi 3ayb mean aklemo?",
        ],
        "K6": ["fi taghleef hedeya?", "momken tghaloflo el order hedeya?"],
        "K7": ["eh mawa3eed khedmet el 3omala2?", "khedmet el 3omala2 btshtaghal emta?"],
        "T1": ["ahlan, ezayak", "shokran awy", "sabah el kheir"],
        "T2": ["el order bta3y wasal fein?", "emta el order hayewsal?", "el order etshahan wala lesa?"],
        "T3": ["el order NS-20877 wasal fein?", "3ayez atatabba3 NS-20512", "eh 7alet el order NS-20934?"],
        "T4": [
            "3ayez esterdad floos el order NS-20745",
            "raga3ouly floosy NS-20512 law samaht",
            "3ayez floosy bta3et NS-20877",
        ],
        "T5": [
            ("3ayez araga3 el order bta3y", "return_request"),
            ("3ayez astabdel el size", "exchange_request"),
            ("el jacket so3'ayara 3alya 3ayez argha3ha", "return_request"),
        ],
        "T6": ["law samaht elghy el order NS-20960", "elghy NS-20512 ghayart ray2y"],
        "T7": ["3ayez akalem 7ad men khedmet el 3omala2", "momken at7adas ma3 mowazaf?", "wassalny be 7ad bashary"],
        "T8": ["el gaw eh el naharda?", "olly nokta", "meen kesseb el match embare7?"],
        "T9": ["3ndokom daman khamas sneen 3ala el agh-hiza?", "a2sod el daman 3ala el agh-hiza el electronia"],
        "T10": ["el order NS-20877 wasal fein?", "01012345601"],
        "T11": "msh ma3ool, afsha khedma!!!",
        "T12": "7ad sa7ab floos men el card bta3y men 3'eer ezny dah nasb",
    },
}
CITATIONS = {"K1": [R2], "K2": SHIP, "K3": COD, "K4": REFUND, "K5": BROKEN, "K6": GIFT, "K7": HOURS}
ORDER_ID = "order_id"


def entities_of(text: str) -> dict[str, str]:
    import re

    found = re.search(r"NS-\d{4,6}", text)
    return {ORDER_ID: found.group(0)} if found else {}


def turn(say: str, **gold: object) -> dict[str, object]:
    return {"say": say, "gold": gold}


def conversations_for(style: str) -> list[dict[str, object]]:
    topics = STYLES[style]
    out: list[dict[str, object]] = []
    counter = 0

    def add(topic: str, turns: list[dict[str, object]]) -> None:
        nonlocal counter
        counter += 1
        out.append({"id": f"{style}-{counter:02d}", "language_style": style, "topic": topic, "turns": turns})

    for topic in ("K1", "K2", "K3", "K4", "K5", "K6", "K7"):
        for text in topics[topic]:  # type: ignore[union-attr]
            add(topic, [turn(text, intents=["policy_question"], decision="answer", citations_any=CITATIONS[topic])])
    for text in topics["T1"]:  # type: ignore[union-attr]
        add("T1", [turn(text, intents=["greeting"], decision="answer")])
    for text in topics["T2"]:  # type: ignore[union-attr]
        add("T2", [turn(text, intents=["order_status"], decision="clarify")])
    for text in topics["T3"]:  # type: ignore[union-attr]
        add("T3", [turn(text, intents=["order_status"], entities=entities_of(text), decision="verify_identity")])
    for text in topics["T4"]:  # type: ignore[union-attr]
        add("T4", [turn(text, intents=["refund_request"], entities=entities_of(text), decision="verify_identity")])
    for text, intent in topics["T5"]:  # type: ignore[union-attr]
        add("T5", [turn(text, intents=[intent], decision="clarify")])
    for text in topics["T6"]:  # type: ignore[union-attr]
        add("T6", [turn(text, intents=["cancel_order"], entities=entities_of(text), decision="verify_identity")])
    for text in topics["T7"]:  # type: ignore[union-attr]
        add("T7", [turn(text, intents=["human_request"], decision="handoff", escalation="customer_request")])
    for text in topics["T8"]:  # type: ignore[union-attr]
        add("T8", [turn(text, intents=[], decision=["clarify", "refuse"])])
    first, second = topics["T9"]  # type: ignore[misc]
    add("T9", [turn(first, intents=["policy_question"], decision="clarify"),
               turn(second, intents=["policy_question"], decision="handoff", escalation="no_evidence")])  # fmt: skip
    ask, phone = topics["T10"]  # type: ignore[misc]
    add("T10", [turn(ask, intents=["order_status"], entities=entities_of(ask), decision="verify_identity"),
                turn(phone, decision="answer", text_contains="NS-20877")])  # fmt: skip
    add("T11", [turn(topics["T11"], decision="handoff", escalation="high_frustration")])  # type: ignore[arg-type]
    add("T12", [turn(topics["T12"], decision="handoff", escalation="mandatory_risk")])  # type: ignore[arg-type]
    return out


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    total = 0
    for style in STYLES:
        rows = conversations_for(style)
        assert len(rows) == 50, (style, len(rows))
        (OUT / f"{style}.jsonl").write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8", newline="\n"
        )
        total += len(rows)
    print(f"wrote {total} conversations to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
