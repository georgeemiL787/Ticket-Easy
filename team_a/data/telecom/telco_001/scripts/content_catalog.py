"""Product catalog and reference lists for Masr Connect. Prices are invented."""

# plan_id, type, name_en, name_ar, monthly_fee, data_mb, minutes, sms, speed_mbps, credit_limit, min_term_months, renew_code
PLANS = [
    # prepaid base tariffs (no monthly fee)
    ("PRE-BASIC", "prepaid", "Kart Basic", "كارت أساسي", 0, 0, 0, 0, None, None, 0, None),
    ("PRE-SECONDS", "prepaid", "Kart Per-Second", "كارت بالثانية", 0, 0, 0, 0, None, None, 0, None),
    ("PRE-YOUTH", "prepaid", "Shabab Kart", "كارت شباب", 0, 0, 0, 0, None, None, 0, None),
    # control (hybrid) plans
    ("CTL-50", "control", "Control 50", "كنترول 50", 50, 2000, 300, 50, None, None, 0, "*890*50#"),
    ("CTL-90", "control", "Control 90", "كنترول 90", 90, 5000, 600, 100, None, None, 0, "*890*90#"),
    ("CTL-140", "control", "Control 140", "كنترول 140", 140, 9000, 1000, 200, None, None, 0, "*890*140#"),
    # postpaid
    ("POST-RED-200", "postpaid", "Shahry 200", "شهري 200", 200, 12000, 1500, 500, None, 800, 0, None),
    ("POST-RED-350", "postpaid", "Shahry 350", "شهري 350", 350, 25000, 3000, 1000, None, 1500, 0, None),
    ("POST-RED-550", "postpaid", "Shahry 550", "شهري 550", 550, 50000, 6000, 2000, None, 2500, 12, None),
    ("POST-RED-900", "postpaid", "Shahry Max 900", "شهري ماكس 900", 900, 120000, 99999, 5000, None, 4000, 24, None),
    ("POST-BIZ-300", "postpaid", "Business 300", "بيزنس 300", 300, 20000, 2500, 1000, None, 2000, 12, None),
    # home internet
    ("HOME-ADSL-140", "home_internet", "Home ADSL 140GB", "نت منزلي 140 جيجا", 210, 140000, 0, 0, 30, None, 0, None),
    ("HOME-VDSL-250", "home_internet", "Home VDSL 250GB", "نت منزلي 250 جيجا", 330, 250000, 0, 0, 70, None, 0, None),
    ("HOME-VDSL-400", "home_internet", "Home VDSL 400GB", "نت منزلي 400 جيجا", 470, 400000, 0, 0, 100, None, 12, None),
    ("HOME-FIBER-1TB", "home_internet", "Home Fiber 1TB", "فايبر 1 تيرا", 950, 1000000, 0, 0, 300, None, 24, None),
]

# bundle_id, kind, name_en, name_ar, price, validity_days, data_mb, minutes, roaming_zone, code
BUNDLES = [
    ("NET-15", "data", "Net 15", "نت 15", 15, 30, 600, 0, None, "*900*15#"),
    ("NET-30", "data", "Net 30", "نت 30", 30, 30, 1500, 0, None, "*900*30#"),
    ("NET-55", "data", "Net 55", "نت 55", 55, 30, 3500, 0, None, "*900*55#"),
    ("NET-100", "data", "Net 100", "نت 100", 100, 30, 8000, 0, None, "*900*100#"),
    ("NET-200", "data", "Net 200", "نت 200", 200, 30, 20000, 0, None, "*900*200#"),
    ("NET-DAY-5", "data", "Daily 5", "يومي 5", 5, 1, 250, 0, None, "*901*5#"),
    ("NET-WEEK-20", "data", "Weekly 20", "أسبوعي 20", 20, 7, 1200, 0, None, "*901*20#"),
    ("SOCIAL-10", "social", "Social 10", "سوشيال 10", 10, 30, 1000, 0, None, "*902*10#"),
    ("SOCIAL-25", "social", "Social 25", "سوشيال 25", 25, 30, 3000, 0, None, "*902*25#"),
    ("MIX-40", "mixed", "Mix 40", "ميكس 40", 40, 30, 1500, 400, None, "*903*40#"),
    ("MIX-75", "mixed", "Mix 75", "ميكس 75", 75, 30, 4000, 900, None, "*903*75#"),
    ("MIN-20", "minutes", "Minutes 20", "دقايق 20", 20, 30, 0, 300, None, "*904*20#"),
    ("MIN-45", "minutes", "Minutes 45", "دقايق 45", 45, 30, 0, 800, None, "*904*45#"),
    ("ROAM-GULF-7", "roaming", "Gulf Roaming 7 days", "تجوال الخليج 7 أيام", 300, 7, 3000, 60, "gulf", "*883*1#"),
    ("ROAM-GULF-30", "roaming", "Gulf Roaming 30 days", "تجوال الخليج 30 يوم", 850, 30, 10000, 200, "gulf", "*883*2#"),
    ("ROAM-EU-7", "roaming", "Europe Roaming 7 days", "تجوال أوروبا 7 أيام", 450, 7, 3000, 60, "europe", "*883*3#"),
    ("ROAM-WORLD-7", "roaming", "World Roaming 7 days", "تجوال العالم 7 أيام", 650, 7, 2000, 30, "world", "*883*4#"),
    ("HOME-ADDON-50", "home_addon", "Home add-on 50GB", "إضافة نت منزلي 50 جيجا", 90, 30, 50000, 0, None, None),
    ("HOME-ADDON-100", "home_addon", "Home add-on 100GB", "إضافة نت منزلي 100 جيجا", 160, 30, 100000, 0, None, None),
]

# vas_id, name_en, name_ar, daily_fee
VAS = [
    ("VAS-TONES", "Caller tunes", "نغمات الانتظار", 0.35),
    ("VAS-NEWS", "Sports news SMS", "أخبار الرياضة", 0.50),
    ("VAS-GAMES", "Games club", "نادي الألعاب", 0.75),
    ("VAS-QURAN", "Daily Quran", "القرآن اليومي", 0.25),
    ("VAS-HOROSCOPE", "Daily horoscope", "حظك اليوم", 0.50),
    ("VAS-COOKING", "Recipes club", "وصفات مطبخ", 0.40),
]

# device_id, name, price
DEVICES = [
    ("DEV-A15", "Samsung Galaxy A15", 9500), ("DEV-A35", "Samsung Galaxy A35", 17500),
    ("DEV-S24", "Samsung Galaxy S24", 42000), ("DEV-IP15", "iPhone 15", 52000),
    ("DEV-IP16P", "iPhone 16 Pro", 78000), ("DEV-RN13", "Redmi Note 13", 10500),
    ("DEV-OPA58", "Oppo A58", 9000), ("DEV-REALME12", "realme 12", 13000),
    ("DEV-ROUTER5G", "5G Home Router", 6500), ("DEV-MIFI", "4G Pocket MiFi", 2200),
]

GOVERNORATES = {
    "Cairo": (["Nasr City", "Heliopolis", "Maadi", "Shubra", "Downtown", "New Cairo", "Ain Shams", "Helwan", "Zamalek", "Mokattam"], 0.22),
    "Giza": (["Dokki", "Mohandessin", "Haram", "Faisal", "6th of October", "Sheikh Zayed", "Imbaba"], 0.16),
    "Alexandria": (["Smouha", "Sidi Gaber", "Miami", "Agami", "Raml Station", "Sporting"], 0.11),
    "Qalyubia": (["Banha", "Shubra El Kheima", "Qalyub", "Obour"], 0.06),
    "Sharqia": (["Zagazig", "10th of Ramadan", "Belbeis"], 0.06),
    "Dakahlia": (["Mansoura", "Talkha", "Mit Ghamr"], 0.06),
    "Gharbia": (["Tanta", "El Mahalla", "Kafr El Zayat"], 0.05),
    "Monufia": (["Shebin El Kom", "Menouf", "Sadat City"], 0.04),
    "Beheira": (["Damanhour", "Kafr El Dawar"], 0.04),
    "Ismailia": (["Ismailia", "Fayed"], 0.02),
    "Port Said": (["Port Said", "Port Fouad"], 0.02),
    "Suez": (["Suez"], 0.015),
    "Faiyum": (["Faiyum", "Ibsheway"], 0.025),
    "Beni Suef": (["Beni Suef"], 0.02),
    "Minya": (["Minya", "Mallawi"], 0.025),
    "Asyut": (["Asyut", "Dayrout"], 0.025),
    "Sohag": (["Sohag", "Akhmim"], 0.02),
    "Qena": (["Qena", "Nag Hammadi"], 0.015),
    "Luxor": (["Luxor"], 0.01),
    "Aswan": (["Aswan", "Kom Ombo"], 0.01),
    "Red Sea": (["Hurghada", "Safaga"], 0.01),
    "South Sinai": (["Sharm El Sheikh", "Dahab"], 0.005),
    "Kafr El Sheikh": (["Kafr El Sheikh", "Desouk"], 0.02),
    "Damietta": (["Damietta", "New Damietta"], 0.015),
}

STREETS = ["El Tahrir St", "Gamal Abdel Nasser St", "El Nasr Rd", "Abbas El Akkad St", "El Gomhoreya St",
           "Mostafa El Nahas St", "El Haram St", "Port Said St", "El Geish St", "Salah Salem St",
           "26th of July St", "El Thawra St", "Ahmed Orabi St", "Talaat Harb St", "El Mehwar St"]

MALE_FIRST = [("Ahmed", "أحمد"), ("Mohamed", "محمد"), ("Mahmoud", "محمود"), ("Omar", "عمر"), ("Youssef", "يوسف"),
              ("Mostafa", "مصطفى"), ("Khaled", "خالد"), ("Karim", "كريم"), ("Hassan", "حسن"), ("Hussein", "حسين"),
              ("Ali", "علي"), ("Ibrahim", "إبراهيم"), ("Tarek", "طارق"), ("Amr", "عمرو"), ("Sherif", "شريف"),
              ("Hany", "هاني"), ("Walid", "وليد"), ("Ayman", "أيمن"), ("Islam", "إسلام"), ("Ziad", "زياد"),
              ("Adham", "أدهم"), ("Seif", "سيف"), ("Marwan", "مروان"), ("Hossam", "حسام"), ("Emad", "عماد"),
              ("Sameh", "سامح"), ("Bassem", "باسم"), ("Fady", "فادي"), ("Mina", "مينا"), ("George", "جورج"),
              ("Peter", "بيتر"), ("Kirollos", "كيرلس"), ("Abdelrahman", "عبد الرحمن"), ("Abdallah", "عبد الله"), ("Yassin", "ياسين"),
              ("Hamza", "حمزة"), ("Nader", "نادر"), ("Ramy", "رامي"), ("Sayed", "سيد"), ("Gamal", "جمال")]
FEMALE_FIRST = [("Mona", "منى"), ("Salma", "سلمى"), ("Nour", "نور"), ("Hana", "هنا"), ("Laila", "ليلى"),
                ("Fatma", "فاطمة"), ("Aya", "آية"), ("Mariam", "مريم"), ("Yasmin", "ياسمين"), ("Rana", "رنا"),
                ("Dina", "دينا"), ("Heba", "هبة"), ("Sara", "سارة"), ("Nada", "ندى"), ("Reem", "ريم"),
                ("Esraa", "إسراء"), ("Doaa", "دعاء"), ("Amira", "أميرة"), ("Shaimaa", "شيماء"), ("Eman", "إيمان"),
                ("Habiba", "حبيبة"), ("Malak", "ملك"), ("Farida", "فريدة"), ("Jana", "جنى"), ("Rawan", "روان"),
                ("Marina", "مارينا"), ("Mira", "ميرا"), ("Christine", "كريستين"), ("Nermeen", "نرمين"), ("Samar", "سمر"),
                ("Ghada", "غادة"), ("Asmaa", "أسماء"), ("Zeinab", "زينب"), ("Hagar", "هاجر"), ("Basma", "بسمة")]
LAST = [("Hassan", "حسن"), ("Mahmoud", "محمود"), ("Ibrahim", "إبراهيم"), ("Abdelaziz", "عبد العزيز"), ("El-Sayed", "السيد"),
        ("Farouk", "فاروق"), ("Mostafa", "مصطفى"), ("Adel", "عادل"), ("Fathy", "فتحي"), ("Samir", "سمير"),
        ("Ramadan", "رمضان"), ("Shaker", "شاكر"), ("Saleh", "صالح"), ("Kamel", "كامل"), ("Naguib", "نجيب"),
        ("Hamdy", "حمدي"), ("Lotfy", "لطفي"), ("Ezzat", "عزت"), ("Soliman", "سليمان"), ("Abdelhamid", "عبد الحميد"),
        ("Gaber", "جابر"), ("Mansour", "منصور"), ("Zaki", "زكي"), ("Ashraf", "أشرف"), ("Fawzy", "فوزي"),
        ("Hegazy", "حجازي"), ("El-Masry", "المصري"), ("Tawfik", "توفيق"), ("Youssef", "يوسف"), ("Girgis", "جرجس"),
        ("Hanna", "حنا"), ("Shenouda", "شنودة"), ("Abdelfattah", "عبد الفتاح"), ("El-Sherif", "الشريف"), ("Badawy", "بدوي")]

STORES = [
    ("ST-CAI-01", "Masr Connect Nasr City", "Cairo", "Nasr City"), ("ST-CAI-02", "Masr Connect Maadi", "Cairo", "Maadi"),
    ("ST-CAI-03", "Masr Connect Heliopolis", "Cairo", "Heliopolis"), ("ST-CAI-04", "Masr Connect Downtown", "Cairo", "Downtown"),
    ("ST-CAI-05", "Masr Connect New Cairo", "Cairo", "New Cairo"), ("ST-GIZ-01", "Masr Connect Dokki", "Giza", "Dokki"),
    ("ST-GIZ-02", "Masr Connect October", "Giza", "6th of October"), ("ST-GIZ-03", "Masr Connect Haram", "Giza", "Haram"),
    ("ST-ALX-01", "Masr Connect Smouha", "Alexandria", "Smouha"), ("ST-ALX-02", "Masr Connect Raml", "Alexandria", "Raml Station"),
    ("ST-QAL-01", "Masr Connect Banha", "Qalyubia", "Banha"), ("ST-SHA-01", "Masr Connect Zagazig", "Sharqia", "Zagazig"),
    ("ST-DAK-01", "Masr Connect Mansoura", "Dakahlia", "Mansoura"), ("ST-GHA-01", "Masr Connect Tanta", "Gharbia", "Tanta"),
    ("ST-MON-01", "Masr Connect Shebin", "Monufia", "Shebin El Kom"), ("ST-BEH-01", "Masr Connect Damanhour", "Beheira", "Damanhour"),
    ("ST-ISM-01", "Masr Connect Ismailia", "Ismailia", "Ismailia"), ("ST-PSD-01", "Masr Connect Port Said", "Port Said", "Port Said"),
    ("ST-SUZ-01", "Masr Connect Suez", "Suez", "Suez"), ("ST-FAY-01", "Masr Connect Faiyum", "Faiyum", "Faiyum"),
    ("ST-BSF-01", "Masr Connect Beni Suef", "Beni Suef", "Beni Suef"), ("ST-MIN-01", "Masr Connect Minya", "Minya", "Minya"),
    ("ST-ASY-01", "Masr Connect Asyut", "Asyut", "Asyut"), ("ST-SOH-01", "Masr Connect Sohag", "Sohag", "Sohag"),
    ("ST-QEN-01", "Masr Connect Qena", "Qena", "Qena"), ("ST-LXR-01", "Masr Connect Luxor", "Luxor", "Luxor"),
    ("ST-ASW-01", "Masr Connect Aswan", "Aswan", "Aswan"), ("ST-RSE-01", "Masr Connect Hurghada", "Red Sea", "Hurghada"),
    ("ST-SSI-01", "Masr Connect Sharm", "South Sinai", "Sharm El Sheikh"), ("ST-KFS-01", "Masr Connect Kafr El Sheikh", "Kafr El Sheikh", "Kafr El Sheikh"),
    ("ST-DAM-01", "Masr Connect Damietta", "Damietta", "Damietta"),
]

RECHARGE_CHANNELS = [("app", 0.28), ("ussd_card", 0.22), ("fawry", 0.18), ("ewallet", 0.14), ("instapay", 0.08), ("atm", 0.05), ("store", 0.05)]
PAYMENT_CHANNELS = [("app", 0.30), ("instapay", 0.15), ("card", 0.15), ("fawry", 0.15), ("ewallet", 0.10), ("direct_debit", 0.10), ("store", 0.05)]
