"""The conversations that test the agent's behaviour, written for scripts/make_eval_set.py (10 per language style).

Like the rest of the set, the gold labels say what the policies and rules require, never what the core does today:
  T13  how are you (small talk, answered with the greeting, no question back)
  T14  an off-topic request (nothing invented, nothing done)
  T15  three requests in one message (status, address change, refund): identity first, then the actions one by one
  T16  one very angry message that carries a real request: apologised to and solved, no handoff
  T17  a refund the policy refuses (delivered 20 days ago): the refusal cites the 14-day rule
  T18  a mistyped order number: asked again with the customer's own orders, the corrected number is answered
  T19  order number and phone in the first message: answered at once
  T20  a refund and a complaint in one message: identity first, then the first confirmation
  T21  a customer who gives up on the assistant: a person takes over (customer_request)
  T22  a policy question, then a return request that follows from it
"""

R2 = "return_policy@v2#s2"
REFUND_14D = "refund_policy@v1#s1"
PHONE_C100 = "01012345601"
PHONE_C105 = "01187654306"

# style -> topic -> list of messages in the order they are said
AGENT_TOPICS: dict[str, dict[str, list[str]]] = {
    "en": {
        "T13": ["how are you?"],
        "T14": ["can you recommend a good restaurant near me?"],
        "T15": [
            "Where is my order NS-20955, I want to change the delivery address to 5 Corniche El Nil Maadi, "
            "and I want a refund for order NS-20701",
            PHONE_C105,
        ],
        "T16": [
            "This is ridiculous!!! Worst service ever, I am so angry! Where is my order NS-20877? "
            f"My phone is {PHONE_C100}"
        ],
        "T17": ["I want a refund for order NS-20512", PHONE_C100],
        "T18": ["Where is my order NS-20877?", PHONE_C100, "and what about order NS-99999?", "I meant NS-20512"],
        "T19": [f"Where is my order NS-20877? My phone is {PHONE_C100}"],
        "T20": [
            "I want a refund for order NS-20745 and I have a complaint about order NS-20877, the parcel arrived late",
            PHONE_C100,
        ],
        "T21": ["Where is my order NS-20877?", "this is not helping, I want to talk to a human"],
        "T22": ["How many days do I have to return an item?", "OK, I want to return order NS-20790, the size is wrong"],
    },
    "ar": {
        "T13": ["عامل ايه؟"],
        "T14": ["ممكن ترشحلي مطعم كويس قريب مني؟"],
        "T15": [
            "فين الاوردر NS-20955 وعايز اغير عنوان التوصيل ل 5 كورنيش النيل المعادي وعايز فلوسي للاوردر NS-20701",
            PHONE_C105,
        ],
        "T16": [f"مش معقول! اسوأ خدمة! الاوردر NS-20877 وصل فين؟ رقمي {PHONE_C100}"],
        "T17": ["عايز فلوسي بتاعة الاوردر NS-20512", PHONE_C100],
        "T18": ["الاوردر NS-20877 وصل فين؟", PHONE_C100, "وايه حكاية الاوردر NS-99999؟", "قصدي NS-20512"],
        "T19": [f"الاوردر NS-20877 وصل فين؟ رقمي {PHONE_C100}"],
        "T20": [
            "عايز فلوسي للاوردر NS-20745 وعندي شكوى على الاوردر NS-20877 الشحنة اتأخرت",
            PHONE_C100,
        ],
        "T21": ["الاوردر NS-20877 وصل فين؟", "مفيش فايدة، عايز اكلم حد من خدمة العملاء"],
        "T22": ["مدة الاسترجاع كام يوم؟", "تمام، عايز ارجع الاوردر NS-20790 لان المقاس مش مظبوط"],
    },
    "mixed": {
        "T13": ["how are you يا باشا؟"],
        "T14": ["ممكن ترشحلي restaurant كويس؟"],
        "T15": [
            "where is الاوردر NS-20955 وعايز اغير الـ address ل 5 كورنيش النيل المعادي وعايز refund للاوردر NS-20701",
            PHONE_C105,
        ],
        "T16": [f"الـ service ده worst حاجة، مش معقول! where is الاوردر NS-20877 رقمي {PHONE_C100}"],
        "T17": ["عايز refund للاوردر NS-20512", PHONE_C100],
        "T18": ["فين الـ package بتاعي NS-20877", PHONE_C100, "وايه حكاية الاوردر NS-99999", "I meant NS-20512"],
        "T19": [f"where is order NS-20877 بتاعي رقمي {PHONE_C100}"],
        "T20": ["عايز refund للاوردر NS-20745 وعندي complaint على الاوردر NS-20877", PHONE_C100],
        "T21": ["where is order NS-20877 بتاعي", "مفيش فايدة، عايز اكلم agent"],
        "T22": ["الـ return window كام يوم؟", "تمام، I want to return order NS-20790 لان الـ size غلط"],
    },
    "arabizi": {
        "T13": ["ezayak, 3amel eh?"],
        "T14": ["momken tersha77ly mat3am kewayes?"],
        "T15": [
            "el order NS-20955 wasal fein, w 3ayez a8ayar el 3enwan le 5 Corniche El Nil El Maadi, "
            "w 3ayez flousi el order NS-20701",
            PHONE_C105,
        ],
        "T16": [f"mohzela!!! afsha khedma, feen el order NS-20877? ra2mi {PHONE_C100}"],
        "T17": ["3ayez floosy bta3et NS-20512", PHONE_C100],
        "T18": ["el order NS-20877 wasal fein?", PHONE_C100, "w eh 7ekayet el order NS-99999?", "ana a2sod NS-20512"],
        "T19": [f"el order NS-20877 wasal fein? ra2mi {PHONE_C100}"],
        "T20": ["3ayez esterdad floos el order NS-20745 w 3andy shakwa 3an el order NS-20877", PHONE_C100],
        "T21": ["el order NS-20877 wasal fein?", "mafeesh fayda, 3ayez akalem 7ad men khedmet el 3omala2"],
        "T22": ["mo3det el estergaa3 kam?", "tamam, 3ayez araga3 el order NS-20790 3shan el size ghalat"],
    },
}
