# (id, style, question, acceptable top citations)
R2, S3, S5, S1, S6, S7, S8, F8 = (
    "return_policy@v2#s2",
    "shipping_policy@v1#s3",
    "shipping_policy@v1#s5",
    "shipping_policy@v1#s1",
    "shipping_policy@v1#s6",
    "shipping_policy@v1#s7",
    "shipping_policy@v1#s8",
    "faq@v1#q08",
)
Q = [
    ("S01", "en", "Can I return my order after 10 days?", [R2]),
    ("Q02", "en", "How long do I have to return an item?", [R2]),
    ("Q03", "en", "Can I get a refund for a dress I bought three weeks ago?", ["refund_policy@v1#s1"]),
    ("Q04", "en", "How much does shipping cost?", [S3]),
    ("Q05", "en", "Can I change the delivery address after my order shipped?", [S5]),
    ("Q06", "en", "Do you ship outside Egypt?", [S1, F8]),
    ("S02", "ar", "ممكن ارجع المنتج بعد كام يوم من الاستلام؟", [R2]),
    ("Q08", "ar", "الاوردر اتاخر كام يوم ويبقى ليا كوبون؟", [S6]),
    ("Q09", "ar", "هل ينفع ارجع الملابس الداخلية؟", ["return_policy@v2#s4"]),
    ("Q10", "ar", "المنتج وصلني مكسور اعمل ايه؟", ["return_policy@v2#s6"]),
    ("Q11", "ar", "المبلغ هيرجع امتى لو دفعت بالكارت؟", ["refund_policy@v1#s2"]),
    ("Q12", "ar", "هل تشحنوا برا مصر؟", [S1, F8]),
    ("S17", "arabizi", "emta a2dar araga3 el montag?", [R2]),
    ("Q14", "arabizi", "el order et2akhar, fi kobon ta3weed?", [S6]),
    ("Q15", "arabizi", "3ayez a8ayar el 3enwan", [S5]),
    ("Q16", "arabizi", "flousi hatrga3 emta lw dafa3t bel visa?", ["refund_policy@v1#s2"]),
    ("Q17", "arabizi", "el mandoob mekalemnish w el order rege3 el mkhzan", [S7]),
    ("Q18", "arabizi", "ezay alghy el order?", [S8]),
    ("Q19", "mixed", "ممكن أعمل return للـ jacket بعد 20 يوم؟", [R2]),
    ("Q20", "mixed", "el refund bta3 order فوق 3000 egp hayt3amal ezay", ["refund_policy@v1#s3"]),
]
NEGATIVE = [
    ("N1", "en", "What is the weather in Cairo today?"),
    ("N2", "en", "Book me a flight to Dubai"),
    ("N3", "en", "Tell me a joke"),
    ("N4", "ar", "ايه اخبار الماتش النهاردة؟"),
    ("N5", "arabizi", "3ayez a7agez tayara le dubai"),
    ("N6", "en", "Who won the football match yesterday?"),
    ("N7", "ar", "ممكن توصيني على مطعم كويس"),
    ("N8", "arabizi", "ezayak ya basha"),
]

# Held-out: written before any tuning, checked separately so the tuned set is not the only evidence.
SHIP_S2_S3 = ["shipping_policy@v1#s3", "shipping_policy@v1#s2"]
HELD = [
    ("H01", "en", "what is the refund limit that needs approval?", ["refund_policy@v1#s3"]),
    ("H02", "en", "do you accept cash on delivery?", ["shipping_policy@v1#s4", "faq@v1#q02"]),
    ("H03", "en", "can I use two vouchers on one order", ["faq@v1#q07"]),
    ("H04", "ar", "ايه مواعيد خدمة العملاء؟", ["faq@v1#q01"]),
    ("H05", "ar", "فين رابط تتبع الاوردر؟", ["shipping_policy@v1#s9"]),
    ("H06", "ar", "هل في فرع اقدر اروحله؟", ["faq@v1#q04"]),
    ("H07", "arabizi", "3ayez a3raf el size el monaseb ezay", ["faq@v1#q03"]),
    ("H08", "arabizi", "fi taghleef hedeya?", ["faq@v1#q05"]),
    ("H09", "en", "the item I received is broken, what now", ["return_policy@v2#s6"]),
    ("H10", "mixed", "كام مصاريف الشحن to Alexandria", SHIP_S2_S3),
]

NEGATIVE += [
    ("N9", "en", "Can you recommend a good restaurant in Maadi?"),
    ("N10", "en", "How do I reset my email password?"),
    ("N11", "en", "What time is it in London?"),
    ("N12", "ar", "عايز اعرف سعر الدولار النهاردة"),
    ("N13", "arabizi", "mashy el gaw 7ar awy el yom"),
    ("N14", "en", "Is Cairo hot in August?"),
    ("N15", "ar", "ازاي اتعلم البرمجة؟"),
    ("N16", "arabizi", "feen a7san kosary fel qahera"),
]
