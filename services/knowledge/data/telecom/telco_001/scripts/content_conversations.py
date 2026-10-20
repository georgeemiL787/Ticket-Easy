"""Message templates for the generated support conversations.

For each intent: customer openers per language (en, ar = Egyptian Arabic, mixed = Arabic-English
code-switching, arabizi = Arabic in Latin letters with numbers), a typical agent reply per
language, the usual outcome, and the policy passage the agent relies on.
Placeholders: {amount} {bundle} {msisdn} {country} {days}
"""

LANGS = ("en", "ar", "mixed", "arabizi")

INTENTS = {
    "balance_inquiry": {
        "openers": {
            "en": ["How much balance do I have?", "Can you tell me my remaining credit and MBs?", "check my balance please"],
            "ar": ["عايز اعرف رصيدي كام", "لو سمحت الرصيد المتبقي والميجات", "رصيدي خلص بسرعة كده ليه؟"],
            "mixed": ["عايز اعرف ال balance بتاعي", "كام MB فاضلين في الباقة؟", "please الرصيد كام؟"],
            "arabizi": ["3ayez a3raf rasedy kam", "kam mega fadlen fel ba2a?", "rasedy 5eles bsor3a leh?"],
        },
        "reply": {
            "en": "Sure, I'll check that once I verify your line. What's the line number and the last 4 digits of the owner's national ID?",
            "ar": "حاضر، هشوفلك ده بعد ما أتأكد من الخط. ممكن رقم الخط وآخر 4 أرقام من الرقم القومي لصاحب الخط؟",
            "arabizi": "7ader, hashoflak da ba3d ma at2aked men el 5at. momken ra2am el 5at w a5er 4 ar2am men el ra2am el 2awmy?",
        },
        "outcome": ("resolved", 0.92), "citation": "privacy_policy@v2#s1", "category": "account",
    },
    "recharge_tax_question": {
        "openers": {
            "en": ["I recharged {amount} EGP and got less credit, why?", "why does a 10 pound card cost 13?"],
            "ar": ["شحنت {amount} جنيه ونزلي أقل بكتير ليه", "الشحنة بتنقص ليه؟"],
            "mixed": ["عملت recharge ب {amount} ونزل أقل، ليه؟", "الـ credit ناقص بعد الشحن"],
            "arabizi": ["shahant {amount} gneh w nezely a2al leh?", "el shahna bt2el leh?"],
        },
        "reply": {
            "en": "Government taxes are taken from every recharge, so it adds 91% of its value as credit; a 10 EGP card sells for 13 and adds 9.10. The SMS shows every deduction.",
            "ar": "الضرايب الحكومية بتتخصم من كل شحنة، فالشحنة بتضيف 91% من قيمتها رصيد، وكارت الـ 10 بيتباع بـ 13 وبيضيف 9.10. الرسالة بتوضح كل الخصومات.",
            "arabizi": "el daraieb bttekhsem men kol shahna, fa btdeef 91% men 2emetha rased; kart el 10 b 13 w byedeef 9.10.",
        },
        "outcome": ("resolved", 0.97), "citation": "prepaid_recharge_policy@v3#s3", "category": "recharge",
    },
    "recharge_missing": {
        "openers": {
            "en": ["I recharged from Fawry an hour ago and nothing arrived", "my recharge didn't go through but money was taken"],
            "ar": ["شحنت من فوري من ساعة ومفيش حاجة نزلت", "الفلوس اتخصمت من المحفظة والرصيد منزلش"],
            "mixed": ["عملت recharge من الـ wallet والرصيد منزلش", "الشحنة pending من الصبح"],
            "arabizi": ["shahant men fawry men sa3a w mafeesh 7aga nezlet", "el felous et5asamet men el wallet w el rased manzelsh"],
        },
        "reply": {
            "en": "Recharges from outside outlets can take up to 4 working hours. Let me verify your line and check the recharge record.",
            "ar": "الشحن من المنافذ الخارجية ممكن ياخد لحد 4 ساعات عمل. خليني أتأكد من الخط وأراجع الشحنة.",
            "arabizi": "el sha7n men el manafez el bara momken ya5od le7ad 4 sa3at 3amal. 5aleeny at2aked men el 5at w araga3 el shahna.",
        },
        "outcome": ("resolved", 0.8), "citation": "postpaid_billing_policy@v2#s5", "category": "recharge",
    },
    "bundle_subscribe": {
        "openers": {
            "en": ["I want to subscribe to {bundle}", "Which internet bundle is best for 100 EGP?"],
            "ar": ["عايز اشترك في باقة {bundle}", "ايه احسن باقة نت ب 100 جنيه؟"],
            "mixed": ["عايز اعمل subscribe في {bundle}", "ايه أحسن data bundle عندكم؟"],
            "arabizi": ["3ayez ashtrek fe ba2et {bundle}", "eh a7san ba2et net b 100 gneh?"],
        },
        "reply": {
            "en": "I can subscribe you to {bundle} after verifying your line; the price is taken from your balance at once.",
            "ar": "أقدر أشتركلك في {bundle} بعد ما أتأكد من الخط، وسعرها بيتخصم من الرصيد فورًا.",
            "arabizi": "a2dar ashtreklak fe {bundle} ba3d ma at2aked men el 5at, w se3rha byet5esem men el rased 3ala tool.",
        },
        "outcome": ("resolved", 0.9), "citation": "bundles_policy@v4#s1", "category": "bundles",
    },
    "bundle_refund": {
        "openers": {
            "en": ["I subscribed to {bundle} by mistake, refund please", "cancel the bundle I just bought and give me my money back"],
            "ar": ["اشتركت في {bundle} بالغلط رجعولي فلوسي", "الغي الباقة اللي لسه مشترك فيها ورجعلي الرصيد"],
            "mixed": ["عملت subscribe بالغلط في {bundle}، عايز refund", "please الغي الباقة ورجع الفلوس"],
            "arabizi": ["eshtarakt fe {bundle} bel 8alat raga3oly felousy", "alghy el ba2a w raga3ly el rased"],
        },
        "reply": {
            "en": "A bundle can be refunded if it's cancelled within 24 hours and less than 5% was used. Let me verify your line and check.",
            "ar": "الباقة بتترد لو اتلغت خلال 24 ساعة ومستخدمش منها أكتر من 5%. خليني أتأكد من الخط وأراجع.",
            "arabizi": "el ba2a btetrad law etlaghet 5elal 24 sa3a w mosta5demsh menha aktar men 5%. 5aleeny at2aked w araga3.",
        },
        "outcome": ("resolved", 0.75), "citation": "refund_policy@v3#s1", "category": "refunds",
    },
    "wrong_deduction": {
        "openers": {
            "en": ["{amount} EGP disappeared from my balance today", "you charged me twice for the same bundle"],
            "ar": ["اتخصم مني {amount} جنيه النهارده من غير سبب", "اتخصم مني تمن الباقة مرتين"],
            "mixed": ["في deduction غريب {amount} جنيه", "اتعملي charge مرتين على نفس الباقة"],
            "arabizi": ["et5asam meny {amount} gneh el naharda men 8eer sabab", "et5asam meny taman el ba2a marteen"],
        },
        "reply": {
            "en": "I'll check the deductions on your line. If a charge is confirmed wrong or duplicated it's returned in full within 48 hours.",
            "ar": "هراجع الخصومات على خطك، ولو اتأكدنا إن الخصم غلط أو متكرر بيرجع كامل خلال 48 ساعة.",
            "arabizi": "haraga3 el 5osoomat 3ala 5atak, w law et2akedna en el 5asm 8alat byerga3 kamel 5elal 48 sa3a.",
        },
        "outcome": ("resolved", 0.7), "citation": "refund_policy@v3#s2", "category": "refunds",
    },
    "vas_unsubscribe": {
        "openers": {
            "en": ["Something called games club keeps charging me", "unsubscribe me from all these services"],
            "ar": ["فيه خدمة اسمها نادي الألعاب بتسحب رصيد", "الغيلي كل الخدمات اللي بتسحب رصيد"],
            "mixed": ["فيه service بتسحب رصيد كل يوم", "عايز اعمل unsubscribe من الخدمات دي"],
            "arabizi": ["fe khedma bteshab rased kol yom", "alghely kol el khadamat elly btes7ab rased"],
        },
        "reply": {
            "en": "I'll unsubscribe it now. Charges for a service you didn't knowingly subscribe to are refunded for up to 30 days.",
            "ar": "هلغيهالك دلوقتي، والرسوم على خدمة مشتركتش فيها بعلمك بترجع عن آخر 30 يوم.",
            "arabizi": "halghehalak delwa2ty, w el rosoom 3ala khedma ma eshtaraktsh feeha btrga3 3an a5er 30 yom.",
        },
        "outcome": ("resolved", 0.9), "citation": "refund_policy@v3#s3", "category": "refunds",
    },
    "bill_inquiry": {
        "openers": {
            "en": ["Why is my bill {amount} EGP this month?", "explain my last invoice please"],
            "ar": ["الفاتورة جاية {amount} جنيه ليه الشهر ده", "ممكن تفهمني الفاتورة"],
            "mixed": ["الـ bill جاية {amount} ليه؟", "عايز breakdown للفاتورة"],
            "arabizi": ["el fatora gaya {amount} gneh leh el shahr da", "momken tfahemny el fatora"],
        },
        "reply": {
            "en": "Let me verify the line and I'll go through the charges on that invoice with you.",
            "ar": "خليني أتأكد من الخط وأراجع معاك بنود الفاتورة.",
            "arabizi": "5aleeny at2aked men el 5at w araga3 ma3ak bonood el fatora.",
        },
        "outcome": ("resolved", 0.85), "citation": "postpaid_billing_policy@v2#s4", "category": "billing",
    },
    "bill_dispute": {
        "openers": {
            "en": ["There's a {amount} EGP charge on my bill I don't recognise", "I want to dispute my bill"],
            "ar": ["فيه {amount} جنيه على الفاتورة معرفهمش", "عايز اعترض على الفاتورة"],
            "mixed": ["في charge {amount} جنيه مش فاهمه", "عايز اعمل dispute على الفاتورة"],
            "arabizi": ["fe {amount} gneh 3al fatora ma3rafhomsh", "3ayez a3tered 3al fatora"],
        },
        "reply": {
            "en": "I can open a dispute; the billing department decides the adjustment, and amounts over 500 EGP also need a supervisor.",
            "ar": "أقدر أفتحلك اعتراض، وفريق الفواتير بيقرر التسوية، واللي فوق 500 جنيه محتاج موافقة مشرف.",
            "arabizi": "a2dar aftahlak e3terad, w fare2 el fawateer by2arar el taswya.",
        },
        "outcome": ("escalated", 0.55), "citation": "postpaid_billing_policy@v2#s4", "category": "billing",
    },
    "payment_not_reflected": {
        "openers": {
            "en": ["I paid my bill this morning but it still shows unpaid", "payment done via instapay, line still blocked"],
            "ar": ["دفعت الفاتورة الصبح ولسه ظاهرة مش مدفوعة", "دفعت بإنستاباي والخط لسه واقف"],
            "mixed": ["عملت payment الصبح ولسه unpaid", "دفعت على instapay والخط لسه blocked"],
            "arabizi": ["dafa3t el fatora el sob7 w lessa zahra msh madfo3a", "dafa3t b instapay w el 5at lessa wa2ef"],
        },
        "reply": {
            "en": "Payments can take up to 4 working hours to appear. Please share the receipt reference and I'll check.",
            "ar": "الدفع ممكن ياخد لحد 4 ساعات عمل عشان يظهر. ابعتلي رقم الإيصال وأنا أراجع.",
            "arabizi": "el daf3 momken ya5od le7ad 4 sa3at 3amal. eb3atly ra2am el esal w ana araga3.",
        },
        "outcome": ("resolved", 0.85), "citation": "postpaid_billing_policy@v2#s5", "category": "billing",
    },
    "line_suspended": {
        "openers": {
            "en": ["My line is suspended, I can't call anyone", "why is my number blocked?"],
            "ar": ["الخط واقف ومش عارف اتصل", "الخط اتقفل ليه؟"],
            "mixed": ["الخط suspended من امبارح", "مش عارف اعمل calls خالص"],
            "arabizi": ["el 5at wa2ef w msh 3aref attesel", "el 5at et2afal leh?"],
        },
        "reply": {
            "en": "Let me verify your line and check why it's suspended; an unpaid invoice is the usual cause and a payment brings it back.",
            "ar": "خليني أتأكد من الخط وأشوف سبب الإيقاف، غالبًا بيكون فاتورة متدفعتش والدفع بيرجعه.",
            "arabizi": "5aleeny at2aked men el 5at w ashoof sabab el ee2af, 8aleban fatora matedfa3etsh.",
        },
        "outcome": ("resolved", 0.8), "citation": "postpaid_billing_policy@v2#s3", "category": "billing",
    },
    "roaming_activation": {
        "openers": {
            "en": ["I'm travelling to {country} tomorrow, how do I activate roaming?", "does my line work abroad?"],
            "ar": ["مسافر {country} بكره، افعل التجوال ازاي؟", "الخط هيشتغل برا مصر؟"],
            "mixed": ["مسافر {country}، عايز activate للـ roaming", "في roaming bundle لـ {country}؟"],
            "arabizi": ["msafer {country} bokra, afa3al el roaming ezay?", "el 5at hayeshta8al bara masr?"],
        },
        "reply": {
            "en": "Prepaid and Control lines can turn roaming on free any time; postpaid gets it after 4 paid bills or with a 750 EGP deposit. A roaming bundle is usually cheaper.",
            "ar": "الكارت والكنترول بيفعلوا التجوال مجانًا في أي وقت، والخط الشهري بعد 4 فواتير مدفوعة أو بتأمين 750 جنيه. باقة التجوال غالبًا أوفر.",
            "arabizi": "el kart w el control byfa3alo el roaming bblash ay wa2t, w el shahry ba3d 4 fawateer aw ta2meen 750 gneh.",
        },
        "outcome": ("resolved", 0.93), "citation": "roaming_policy@v2#s1", "category": "roaming",
    },
    "roaming_charges": {
        "openers": {
            "en": ["I got huge roaming charges from my trip to {country}", "roaming data stopped while I'm abroad"],
            "ar": ["جالي رسوم تجوال كبيرة من سفرية {country}", "النت وقف وانا برا"],
            "mixed": ["الـ roaming charges عالية جدًا", "الـ data وقفت وأنا مسافر"],
            "arabizi": ["galy rosoom roaming kebeera men safreyet {country}", "el net wa2af w ana bara"],
        },
        "reply": {
            "en": "Roaming charges can take up to 72 hours to appear, and new postpaid lines have a 250 EGP roaming limit. I can review the charges with you.",
            "ar": "رسوم التجوال ممكن تاخد لحد 72 ساعة تظهر، والخطوط الشهرية الجديدة ليها حد تجوال 250 جنيه. أقدر أراجع الرسوم معاك.",
            "arabizi": "rosoom el roaming momken ta5od le7ad 72 sa3a, w el 5otoot el gdeeda leha 7ad 250 gneh. a2dar araga3ha ma3ak.",
        },
        "outcome": ("escalated", 0.5), "citation": "roaming_policy@v2#s3", "category": "roaming",
    },
    "sim_lost": {
        "openers": {
            "en": ["My phone was stolen, please stop my line", "I lost my SIM card"],
            "ar": ["الموبايل اتسرق وقفولي الخط", "الشريحة ضاعت مني"],
            "mixed": ["الموبايل اتسرق، please اعملوا block للخط", "عايز اعمل suspend للخط عشان ضاع"],
            "arabizi": ["el mobile etsara2 wa2afooly el 5at", "el shee7a da3et meny"],
        },
        "reply": {
            "en": "I'm sorry. I can suspend the line now after verifying you; a replacement SIM is issued only in a store.",
            "ar": "آسف جدًا. أقدر أوقف الخط دلوقتي بعد التحقق منك، وبدل الفاقد بيطلع من الفرع بس.",
            "arabizi": "asef gedan. a2dar aw2af el 5at delwa2ty ba3d el ta7a2o2, w badal el fa2ed men el far3 bas.",
        },
        "outcome": ("resolved", 0.88), "citation": "sim_policy@v2#s3", "category": "sim",
    },
    "sim_swap_request": {
        "openers": {
            "en": ["Send me a new SIM for my number", "can you move my number to a new SIM now?"],
            "ar": ["عايز شريحة جديدة لنفس الرقم تتبعتلي", "انقلولي الرقم على شريحة جديدة"],
            "mixed": ["عايز SIM swap من غير ما أروح الفرع", "ممكن تعملولي replacement للشريحة؟"],
            "arabizi": ["3ayez shee7a gdeda le nafs el ra2am", "e3melooly badal fa2ed men 8eer ma aroo7 el far3"],
        },
        "reply": {
            "en": "A SIM replacement can only be done in a store by the line owner with the original national ID; I can't do it in chat.",
            "ar": "بدل الفاقد بيتعمل في الفرع بس لصاحب الخط بالبطاقة الأصلية، ومقدرش أعمله من الشات.",
            "arabizi": "badal el fa2ed byet3emel fel far3 bas le sa7eb el 5at bel beta2a el asleya.",
        },
        "outcome": ("refused", 0.95), "citation": "sim_policy@v2#s2", "category": "sim",
    },
    "puk_request": {
        "openers": {
            "en": ["My SIM is asking for PUK", "I need the PUK code"],
            "ar": ["الشريحة بتطلب PUK", "محتاج كود البيوك"],
            "mixed": ["الـ SIM locked وعايزة PUK", "please الـ PUK code"],
            "arabizi": ["el shee7a bt6lob PUK", "me7tag kod el PUK"],
        },
        "reply": {
            "en": "I can give the PUK to the line owner after verification. Note that 10 wrong tries block the SIM permanently.",
            "ar": "أقدر أديك الـ PUK بعد ما أتحقق إنك صاحب الخط، وخلي بالك 10 محاولات غلط بتقفل الشريحة نهائي.",
            "arabizi": "a2dar adeek el PUK ba3d ma at7a2a2 enak sa7eb el 5at.",
        },
        "outcome": ("resolved", 0.9), "citation": "sim_policy@v2#s6", "category": "sim",
    },
    "port_out": {
        "openers": {
            "en": ["I want to move my number to another network", "how do I port out?"],
            "ar": ["عايز انقل رقمي لشبكة تانية", "ازاي اخرج من الشبكة بنفس الرقم؟"],
            "mixed": ["عايز اعمل port للرقم لشبكة تانية", "الـ porting بياخد قد ايه؟"],
            "arabizi": ["3ayez a2el ra2my le shabaka tanya", "ezay a5rog men el shabaka b nafs el ra2am?"],
        },
        "reply": {
            "en": "Porting is free and done in 24 hours if you have owned the line for 4 months and the last bill is paid; remaining balance and bundles are lost.",
            "ar": "النقل مجاني وبيتم في 24 ساعة لو بقالك 4 شهور مالك الخط وآخر فاتورة مدفوعة، والرصيد والباقات اللي فاضلة بتضيع.",
            "arabizi": "el na2l bblash w by5ales fe 24 sa3a law ba2alak 4 shohoor malek el 5at w a5er fatora madfoo3a.",
        },
        "outcome": ("resolved", 0.9), "citation": "porting_policy@v2#s2", "category": "porting",
    },
    "home_internet_down": {
        "openers": {
            "en": ["My home internet has been down since yesterday", "router lights are red, no internet"],
            "ar": ["النت في البيت فاصل من امبارح", "الراوتر نوره أحمر ومفيش نت"],
            "mixed": ["الـ home internet فاصل من امبارح", "الـ router مش بيجيب signal"],
            "arabizi": ["el net fel beet fasel men embare7", "el router noro a7mar w mafeesh net"],
        },
        "reply": {
            "en": "I'll check for an outage in your area and run a remote test; if a visit is needed it's within 48 hours.",
            "ar": "هشوف لو فيه عطل في منطقتك وأعمل فحص عن بُعد، ولو محتاج زيارة بتبقى خلال 48 ساعة.",
            "arabizi": "hashoof law fe 3otl fe mante2tak w a3mel fa7s 3an bo3d.",
        },
        "outcome": ("resolved", 0.7), "citation": "home_internet_policy@v3#s3", "category": "home_internet",
    },
    "slow_internet": {
        "openers": {
            "en": ["My internet speed is terrible, I'm paying for 70 Mbps", "speed test shows 5 Mbps only"],
            "ar": ["النت بطيء جدًا وانا دافع 70 ميجا", "السرعة 5 ميجا بس"],
            "mixed": ["الـ speed وحشة جدًا", "عملت speed test طلع 5 Mbps"],
            "arabizi": ["el net ba6ee2 gedan w ana dafe3 70 mega", "el sor3a 5 mega bas"],
        },
        "reply": {
            "en": "If speed stays below 50% of your plan for 3 days in a row we treat it as a fault. I can open a fault report.",
            "ar": "لو السرعة أقل من 50% من الباقة 3 أيام ورا بعض بنعتبرها عطل، وأقدر أفتحلك بلاغ.",
            "arabizi": "law el sor3a a2al men 50% 3 ayam wara ba3d bne3tebrha 3otl.",
        },
        "outcome": ("resolved", 0.75), "citation": "home_internet_policy@v3#s2", "category": "home_internet",
    },
    "plan_change": {
        "openers": {
            "en": ["I want to upgrade my plan", "move me to a cheaper plan"],
            "ar": ["عايز ارقي الباقة", "نزلني لنظام أرخص"],
            "mixed": ["عايز اعمل upgrade للـ plan", "عايز downgrade لنظام أرخص"],
            "arabizi": ["3ayez ara2y el ba2a", "nazelny le nezam ar5as"],
        },
        "reply": {
            "en": "If you switch internet bundles before renewal your remaining MBs stay usable; postpaid plans can change once per bill cycle.",
            "ar": "لو غيرت باقة النت قبل التجديد الميجات اللي فاضلة بتفضل معاك، والخط الشهري يتغير نظامه مرة في كل دورة فاتورة.",
            "arabizi": "law 8ayart el ba2a abl el tagdeed el megat el fadla btfdal ma3ak.",
        },
        "outcome": ("resolved", 0.9), "citation": "bundles_policy@v4#s4", "category": "bundles",
    },
    "salefny": {
        "openers": {
            "en": ["Salefny is not working for me", "I need emergency credit"],
            "ar": ["سلفني مش راضية تشتغل", "محتاج رصيد طوارئ"],
            "mixed": ["الـ salefny مش شغالة", "عايز emergency credit"],
            "arabizi": ["salefny msh rad ya tshta8al", "me7tag rased tawarea"],
        },
        "reply": {
            "en": "Salefny works when your balance is under 5 EGP and there is no unpaid Salefny. Let me check your line.",
            "ar": "سلفني بتشتغل لما رصيدك يكون أقل من 5 جنيه ومفيش سلفة لسه متسددتش. خليني أشوف خطك.",
            "arabizi": "salefny btshta8al lama rasedak ykoon a2al men 5 gneh w mafeesh solfa 2adeema.",
        },
        "outcome": ("resolved", 0.9), "citation": "prepaid_recharge_policy@v3#s5", "category": "recharge",
    },
    "complaint_escalation": {
        "openers": {
            "en": ["This is the third time I complain and nothing happened!", "I want to talk to a manager now"],
            "ar": ["دي تالت مرة اشتكي ومحدش عمل حاجة!", "عايز اكلم مدير حالًا"],
            "mixed": ["دي third time اشتكي!!", "عايز أكلم supervisor"],
            "arabizi": ["di talet marra ashtekey w ma7adesh 3amal 7aga!", "3ayez akalem modeer 7alan"],
        },
        "reply": {
            "en": "I'm sorry this hasn't been fixed. I'm passing your case to senior staff with the full history now.",
            "ar": "آسف إن المشكلة متحلتش. هحول حالتك دلوقتي للفريق المتخصص بكل التفاصيل.",
            "arabizi": "asef en el moshkela matetlatsh. ha7awel 7altak delwa2ty lel fare2 el motakhases.",
        },
        "outcome": ("escalated", 0.95), "citation": "complaints_policy@v2#s3", "category": "complaints",
    },
    "cancel_line": {
        "openers": {
            "en": ["Cancel my line please", "I want to close my postpaid account"],
            "ar": ["الغولي الخط", "عايز اقفل الخط الشهري"],
            "mixed": ["عايز اعمل cancel للخط", "please اقفلوا الـ account"],
            "arabizi": ["alghooly el 5at", "3ayez a2fel el 5at el shahry"],
        },
        "reply": {
            "en": "I can't cancel a line in chat; the owner cancels it in a store, after paying any open invoices for postpaid.",
            "ar": "مقدرش ألغي الخط من الشات، صاحب الخط بيلغيه من الفرع بعد سداد الفواتير لو شهري.",
            "arabizi": "ma2darsh alghy el 5at men el chat, sa7eb el 5at byelghyh men el far3.",
        },
        "outcome": ("refused", 0.95), "citation": "contract_policy@v1#s4", "category": "contracts",
    },
    "call_log_request": {
        "openers": {
            "en": ["Send me the list of numbers I called last week", "I need my call history"],
            "ar": ["ابعتلي الأرقام اللي كلمتها الأسبوع اللي فات", "محتاج سجل المكالمات"],
            "mixed": ["عايز الـ call log بتاعي", "ابعتلي الـ history بتاع المكالمات"],
            "arabizi": ["eb3atly el ar2am elly kalemtha el osboo3 elly fat", "me7tag sigel el mokalmat"],
        },
        "reply": {
            "en": "Detailed call records are only given to the line owner in a store; I can share usage totals here.",
            "ar": "سجل المكالمات التفصيلي بيتسلم لصاحب الخط في الفرع بس، وأقدر أقولك إجمالي الاستهلاك هنا.",
            "arabizi": "sigel el mokalmat byetsalem le sa7eb el 5at fel far3 bas.",
        },
        "outcome": ("refused", 0.95), "citation": "privacy_policy@v2#s2", "category": "privacy",
    },
    "third_party_request": {
        "openers": {
            "en": ["What's my husband's line balance? number {msisdn}", "check my mother's bill for me"],
            "ar": ["عايز اعرف رصيد خط جوزي {msisdn}", "شوفلي فاتورة ماما"],
            "mixed": ["عايزة اعرف الـ balance بتاع جوزي", "check على فاتورة بابا please"],
            "arabizi": ["3ayza a3raf rased 5at gozy {msisdn}", "shofly fatoret mama"],
        },
        "reply": {
            "en": "Sorry, I can only share account details with the registered owner after verification.",
            "ar": "آسف، مقدرش أقول بيانات حساب غير لصاحب الخط بعد التحقق منه.",
            "arabizi": "asef, ma2darsh a2ool bayanat 7esab 8eer le sa7eb el 5at.",
        },
        "outcome": ("refused", 0.9), "citation": "privacy_policy@v2#s3", "category": "privacy",
    },
    "fraud_report": {
        "openers": {
            "en": ["Someone called me pretending to be Masr Connect and asked for my code", "my number was swapped without me"],
            "ar": ["حد كلمني وقال إنه من مصر كونكت وطلب الكود", "حد عمل بدل فاقد لرقمي من غير ما اعرف"],
            "mixed": ["حد عمل SIM swap لرقمي من غير علمي", "جالي call scam باسمكم"],
            "arabizi": ["7ad kalemny w 2al eno men masr connect w 6alab el kod", "7ad 3amal badal fa2ed le ra2my"],
        },
        "reply": {
            "en": "Thank you for telling us. I'm passing this to our security department urgently; never share codes with anyone.",
            "ar": "شكرًا إنك بلغتنا. هحول ده لفريق الأمان فورًا، ومتديش أي كود لأي حد.",
            "arabizi": "shokran enak ballaghtna. ha7awel da le fare2 el aman fawran.",
        },
        "outcome": ("escalated", 1.0), "citation": "sim_policy@v2#s2", "category": "security",
    },
    "greeting": {
        "openers": {
            "en": ["hi", "hello there", "good evening"],
            "ar": ["السلام عليكم", "مساء الخير", "أهلا"],
            "mixed": ["hi، ممكن مساعدة؟", "hello يا جماعة"],
            "arabizi": ["ahlan", "salam 3alekom", "masa2 el 5eer"],
        },
        "reply": {
            "en": "Hello! How can I help you today?",
            "ar": "أهلًا بيك! أقدر أساعدك في إيه؟",
            "arabizi": "ahlan beek! a2dar asa3dak fe eh?",
        },
        "outcome": ("resolved", 0.6), "citation": None, "category": "smalltalk",
    },
}

# relative frequency of intents in generated traffic
INTENT_WEIGHTS = {
    "balance_inquiry": 12, "recharge_tax_question": 6, "recharge_missing": 6, "bundle_subscribe": 9,
    "bundle_refund": 6, "wrong_deduction": 7, "vas_unsubscribe": 5, "bill_inquiry": 7, "bill_dispute": 4,
    "payment_not_reflected": 4, "line_suspended": 4, "roaming_activation": 3, "roaming_charges": 2,
    "sim_lost": 2, "sim_swap_request": 2, "puk_request": 2, "port_out": 2, "home_internet_down": 5,
    "slow_internet": 4, "plan_change": 4, "salefny": 3, "complaint_escalation": 3, "cancel_line": 2,
    "call_log_request": 1, "third_party_request": 1, "fraud_report": 1, "greeting": 3,
}

LANG_WEIGHTS = {"ar": 0.38, "arabizi": 0.24, "mixed": 0.20, "en": 0.18}

FOLLOWUPS = {
    "thanks": {"en": "thanks", "ar": "شكرًا", "mixed": "تمام thanks", "arabizi": "shokran"},
    "frustrated": {"en": "this is ridiculous", "ar": "ده مش معقول بجد", "mixed": "ده ridiculous بجد", "arabizi": "da msh ma32ool bgad"},
    "human": {"en": "I want a real person", "ar": "عايز اكلم حد بني آدم", "mixed": "عايز agent حقيقي", "arabizi": "3ayez akalem 7ad bany adam"},
}

COUNTRIES = ["Saudi Arabia", "UAE", "Kuwait", "Qatar", "Turkey", "Italy", "Germany", "France", "UK", "Jordan"]
