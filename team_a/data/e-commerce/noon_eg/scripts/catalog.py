"""Fictional people, places and products for the bulk noon_eg data. Plain data, no logic."""

MALE = [("Ahmed", "أحمد"), ("Mohamed", "محمد"), ("Mahmoud", "محمود"), ("Mostafa", "مصطفى"), ("Omar", "عمر"),
        ("Youssef", "يوسف"), ("Karim", "كريم"), ("Hassan", "حسن"), ("Hussein", "حسين"), ("Ali", "علي"),
        ("Ibrahim", "إبراهيم"), ("Khaled", "خالد"), ("Tarek", "طارق"), ("Amr", "عمرو"), ("Sherif", "شريف"),
        ("Hany", "هاني"), ("Waleed", "وليد"), ("Islam", "إسلام"), ("Mina", "مينا"), ("Kirollos", "كيرلس"),
        ("Bishoy", "بيشوي"), ("Ziad", "زياد"), ("Seif", "سيف"), ("Adham", "أدهم"), ("Hamza", "حمزة"),
        ("Abdelrahman", "عبد الرحمن"), ("Yassin", "ياسين"), ("Marwan", "مروان"), ("Ayman", "أيمن"),
        ("Sameh", "سامح"), ("Ashraf", "أشرف"), ("Essam", "عصام"), ("Gamal", "جمال"), ("Nader", "نادر"),
        ("Ramy", "رامي"), ("Fady", "فادي"), ("Belal", "بلال"), ("Osama", "أسامة"), ("Hesham", "هشام"),
        ("Basel", "باسل")]
FEMALE = [("Nour", "نور"), ("Salma", "سلمى"), ("Mariam", "مريم"), ("Hana", "هنا"), ("Rana", "رنا"),
          ("Dina", "دينا"), ("Aya", "آية"), ("Yasmin", "ياسمين"), ("Farida", "فريدة"), ("Malak", "ملك"),
          ("Habiba", "حبيبة"), ("Jana", "جنى"), ("Nada", "ندى"), ("Heba", "هبة"), ("Reem", "ريم"),
          ("Sara", "سارة"), ("Mai", "مي"), ("Mona", "منى"), ("Esraa", "إسراء"), ("Shaimaa", "شيماء"),
          ("Doaa", "دعاء"), ("Marina", "مارينا"), ("Mirna", "ميرنا"), ("Christine", "كريستين"),
          ("Nourhan", "نورهان"), ("Rawan", "روان"), ("Laila", "ليلى"), ("Amira", "أميرة"), ("Ghada", "غادة"),
          ("Eman", "إيمان"), ("Asmaa", "أسماء"), ("Basmala", "بسملة"), ("Rahma", "رحمة"), ("Zeinab", "زينب"),
          ("Fatma", "فاطمة"), ("Hagar", "هاجر"), ("Noha", "نهى"), ("Nermeen", "نرمين"), ("Dalia", "داليا"),
          ("Engy", "إنجي")]
LAST = [("Hassan", "حسن"), ("Mostafa", "مصطفى"), ("Adel", "عادل"), ("Fathy", "فتحي"), ("Samir", "سمير"),
        ("Ibrahim", "إبراهيم"), ("Tarek", "طارق"), ("Saeed", "سعيد"), ("Khaled", "خالد"), ("Gamal", "جمال"),
        ("Fouad", "فؤاد"), ("Mahmoud", "محمود"), ("Abdelaziz", "عبد العزيز"), ("Soliman", "سليمان"),
        ("Nabil", "نبيل"), ("Ramadan", "رمضان"), ("Shawky", "شوقي"), ("Ezzat", "عزت"), ("Farouk", "فاروق"),
        ("Hamdy", "حمدي"), ("Lotfy", "لطفي"), ("Salah", "صلاح"), ("Zaki", "زكي"), ("Naguib", "نجيب"),
        ("Girgis", "جرجس"), ("Wahba", "وهبة"), ("Hanna", "حنا"), ("Morsy", "مرسي"), ("Elsayed", "السيد"),
        ("Abdallah", "عبد الله"), ("Sobhy", "صبحي"), ("Ragab", "رجب"), ("Helmy", "حلمي"), ("Kamel", "كامل"),
        ("Anwar", "أنور"), ("Barakat", "بركات"), ("Shehata", "شحاتة"), ("Mansour", "منصور"),
        ("Youssef", "يوسف"), ("Atef", "عاطف"), ("Magdy", "مجدي"), ("Osman", "عثمان"), ("Badawy", "بدوي"),
        ("Zaghloul", "زغلول"), ("Hegazy", "حجازي"), ("Nasr", "نصر"), ("Emam", "إمام"), ("Sharaf", "شرف"),
        ("Fawzy", "فوزي"), ("Rizk", "رزق")]

# (city, weight, noon delivery days (min, max), Minutes available)
CITIES = [
    ("Cairo", 22, (1, 3), True), ("Giza", 12, (1, 3), True), ("New Cairo", 6, (1, 3), True),
    ("6th of October", 5, (1, 3), True), ("Alexandria", 11, (2, 4), True), ("Mansoura", 4, (2, 5), False),
    ("Tanta", 3, (2, 5), False), ("Zagazig", 3, (2, 5), False), ("Shebin El Kom", 2, (2, 5), False),
    ("Banha", 2, (2, 5), False), ("Damanhour", 2, (2, 5), False), ("Kafr El Sheikh", 2, (3, 6), False),
    ("Damietta", 2, (3, 6), False), ("Port Said", 2, (2, 5), False), ("Ismailia", 2, (2, 5), False),
    ("Suez", 2, (2, 5), False), ("Faiyum", 2, (3, 6), False), ("Beni Suef", 2, (3, 6), False),
    ("Minya", 2, (3, 6), False), ("Assiut", 2, (3, 7), False), ("Sohag", 2, (3, 7), False),
    ("Qena", 1, (4, 7), False), ("Luxor", 1, (4, 7), False), ("Aswan", 1, (4, 7), False),
    ("Hurghada", 2, (3, 7), False), ("Sharm El Sheikh", 1, (3, 7), False), ("Marsa Matruh", 1, (4, 7), False),
]

# (category, subcategory): (weight, (price min, price max), Minutes stocks it, product names)
PRODUCTS = {
    ("electronics", "smartphone"): (5, (4500, 42000), False, ["Android smartphone 128GB", "Android smartphone 256GB", "Budget smartphone 64GB", "Flagship smartphone 512GB", "Foldable smartphone 256GB"]),
    ("electronics", "laptop"): (2, (14000, 65000), False, ["14-inch laptop", "15.6-inch gaming laptop", "13-inch ultrabook", "2-in-1 touchscreen laptop"]),
    ("electronics", "tablet"): (2, (3500, 28000), False, ["10-inch tablet", "8-inch kids tablet", "11-inch tablet with keyboard"]),
    ("electronics", "headphones"): (4, (350, 9500), True, ["Wireless headphones", "Noise-cancelling headphones", "Gaming headset", "Wireless earbuds", "Wired earphones"]),
    ("electronics", "smartwatch"): (2, (900, 14000), True, ["Smartwatch", "Fitness band", "Kids smartwatch"]),
    ("electronics", "accessory"): (9, (90, 1400), True, ["USB-C wall charger", "USB-C cable 2m", "Power bank 10000mAh", "Power bank 20000mAh", "Phone case", "Screen protector 2-pack", "Wireless mouse", "Bluetooth speaker", "Car charger", "Laptop sleeve 15-inch", "Wireless keyboard", "64GB microSD card"]),
    ("electronics", "tv"): (1, (7000, 38000), False, ["43-inch smart TV", "55-inch 4K smart TV", "32-inch HD TV"]),
    ("beauty_health", "skincare"): (5, (120, 1500), True, ["Face moisturizer", "Sunscreen SPF50", "Vitamin C serum", "Facial cleanser", "Night cream", "Micellar water 400ml"]),
    ("beauty_health", "fragrance"): (3, (450, 6500), True, ["Women's perfume 100ml", "Men's eau de toilette 100ml", "Body mist 250ml", "Oud perfume 50ml"]),
    ("beauty_health", "makeup"): (3, (90, 1200), True, ["Mascara", "Liquid foundation", "Lipstick", "Eyeshadow palette", "Makeup brush set"]),
    ("beauty_health", "hair_care"): (3, (80, 650), True, ["Anti-dandruff shampoo", "Hair conditioner", "Argan hair oil", "Hair mask"]),
    ("beauty_health", "personal_care"): (2, (60, 450), True, ["Deodorant spray", "Shaving foam", "Toothpaste 3-pack", "Body wash"]),
    ("beauty_health", "vitamins"): (2, (150, 900), True, ["Vitamin D3 capsules", "Omega-3 softgels", "Multivitamin tablets", "Zinc tablets"]),
    ("beauty_health", "sports_nutrition"): (1, (700, 4200), False, ["Whey protein 2lb", "Creatine powder", "Mass gainer 3kg"]),
    ("beauty_health", "personal_care_device"): (2, (350, 4500), True, ["Hair dryer", "Hair straightener", "Electric shaver", "Electric toothbrush"]),
    ("fashion", "apparel"): (8, (150, 2400), False, ["Men's cotton T-shirt", "Women's summer dress", "Men's padded jacket", "Women's jeans", "Kids hoodie", "Men's polo shirt", "Women's abaya", "Men's chino trousers", "Women's cardigan"]),
    ("fashion", "footwear"): (4, (350, 4500), False, ["Running shoes", "Men's leather loafers", "Women's sandals", "Kids sneakers", "Slippers"]),
    ("fashion", "bags"): (2, (250, 3800), False, ["Backpack", "Women's handbag", "Travel duffel bag", "Laptop backpack"]),
    ("fashion", "watches"): (1, (400, 6000), False, ["Men's analog watch", "Women's quartz watch"]),
    ("fashion", "eyewear"): (1, (250, 3000), False, ["Polarized sunglasses", "Blue-light glasses"]),
    ("fashion", "underwear"): (2, (120, 600), False, ["Men's boxer briefs 3-pack", "Cotton undershirts 2-pack"]),
    ("fashion", "lingerie"): (1, (180, 900), False, ["Women's sleepwear set", "Lace bra"]),
    ("fashion", "swimwear"): (1, (250, 1200), False, ["Men's swim shorts", "Women's burkini"]),
    ("fashion", "socks"): (1, (60, 250), False, ["Ankle socks 6-pack", "Sports socks 3-pack"]),
    ("home_kitchen", "small_appliance"): (6, (450, 6500), True, ["Electric kettle", "Air fryer", "Kitchen blender", "Hand mixer", "Coffee maker", "Steam iron", "Toaster", "Food processor"]),
    ("home_kitchen", "kitchenware"): (4, (90, 2200), True, ["Non-stick frying pan", "Cookware set 10 pcs", "Kitchen knife set", "Food storage containers", "Pressure cooker", "Dinner set 24 pcs"]),
    ("home_kitchen", "home_decor"): (3, (150, 2500), True, ["Ceramic vase", "Wall clock", "Decorative cushions 2-pack", "Table lamp", "Photo frame set"]),
    ("home_kitchen", "bedding"): (3, (350, 2800), False, ["Cotton bed sheet set", "Duvet cover set", "Pillow 2-pack", "Winter blanket"]),
    ("home_kitchen", "furniture"): (1, (1500, 18000), False, ["3-seater sofa", "Office chair", "Bookshelf", "Coffee table", "Wardrobe 2-door"]),
    ("home_kitchen", "large_appliance"): (1, (8000, 32000), False, ["Front-load washing machine", "Refrigerator 16 cu ft", "Split air conditioner 1.5 HP", "Gas cooker 5 burners", "Dishwasher"]),
    ("sports", "fitness"): (3, (150, 6500), True, ["Yoga mat", "Adjustable dumbbells 20kg", "Resistance bands set", "Skipping rope", "Exercise bike"]),
    ("sports", "outdoor"): (1, (250, 3500), False, ["Camping tent 4-person", "Bicycle helmet", "Football size 5", "Padel racket"]),
    ("toys", "building_toy"): (2, (250, 2500), True, ["Building blocks set 500 pcs", "Building blocks set 300 pcs", "Magnetic tiles 60 pcs"]),
    ("toys", "puzzle"): (1, (120, 600), True, ["1000-piece puzzle", "Kids 3D puzzle"]),
    ("toys", "doll"): (1, (200, 1500), True, ["Fashion doll", "Plush teddy bear"]),
    ("toys", "rc_toy"): (1, (450, 3500), True, ["Remote control car", "Mini drone"]),
    ("toys", "board_game"): (1, (200, 900), True, ["Family board game", "Card game"]),
    ("toys", "costume"): (1, (200, 700), False, ["Kids dress-up costume", "Superhero costume"]),
    ("baby", "stroller"): (1, (2500, 12000), False, ["Foldable baby stroller", "Travel system stroller"]),
    ("baby", "car_seat"): (1, (2200, 9000), False, ["Baby car seat", "Booster seat"]),
    ("baby", "diapers"): (3, (250, 900), True, ["Diapers size 4 (60 pcs)", "Diapers size 3 (72 pcs)", "Baby wipes 6-pack"]),
    ("baby", "feeding"): (1, (150, 900), True, ["Baby feeding bottles set", "Silicone bib set", "Sippy cup"]),
    ("baby", "bath_care"): (1, (90, 450), True, ["Baby shampoo", "Baby bath tub"]),
    ("baby", "baby_toy"): (1, (120, 800), True, ["Baby activity gym", "Rattle set"]),
    ("grocery", "snacks"): (3, (40, 400), True, ["Potato chips box", "Mixed nuts 500g", "Chocolate bars 12-pack", "Biscuits family pack"]),
    ("grocery", "beverages"): (3, (30, 450), True, ["Bottled water 12-pack", "Ground coffee 250g", "Black tea 100 bags", "Orange juice 6-pack"]),
    ("grocery", "pantry"): (2, (40, 600), True, ["Basmati rice 5kg", "Sunflower oil 2.2L", "Pasta 1kg", "Honey 500g"]),
    ("pet_supplies", "perishable"): (1, (120, 1400), True, ["Dog food treats", "Dry cat food 2kg", "Dry dog food 3kg"]),
    ("pet_supplies", "litter_box"): (1, (180, 900), True, ["Cat litter box", "Cat litter 10L"]),
    ("pet_supplies", "pet_accessory"): (1, (90, 1200), True, ["Dog leash", "Pet carrier", "Cat scratching post"]),
    ("automotive", "car_accessory"): (2, (80, 1500), True, ["Car phone holder", "Seat covers set", "Car vacuum cleaner", "Car air freshener 3-pack"]),
    ("automotive", "dash_cam"): (1, (900, 3500), True, ["Dash cam 1080p", "Dash cam 4K"]),
    ("automotive", "tyre_inflator"): (1, (650, 1800), True, ["Portable tyre inflator", "Jump starter"]),
    ("tools", "power_tool"): (1, (900, 4500), True, ["Cordless drill", "Angle grinder", "Electric screwdriver"]),
    ("tools", "hand_tool"): (2, (120, 1200), True, ["Screwdriver set 32 pcs", "Tool box 100 pcs", "Measuring tape 5m", "Hammer"]),
    ("books", "novel"): (2, (90, 450), False, ["Arabic novel (paperback)", "English novel (paperback)", "Classic novel box set"]),
    ("books", "textbook"): (1, (150, 900), False, ["English grammar textbook", "IGCSE maths workbook", "Arabic dictionary"]),
    ("books", "kids_book"): (1, (60, 350), False, ["Kids story book", "Coloring book"]),
    ("stationery", "notebooks"): (2, (40, 350), True, ["A5 notebook pack (5)", "Spiral notebook 200 pages", "Sticky notes set"]),
    ("stationery", "writing"): (1, (30, 600), True, ["Gel pens 12-pack", "Fountain pen", "Colored pencils 24"]),
    ("stationery", "office"): (1, (90, 1800), True, ["Desk organizer", "Stapler", "Laminator", "Printer paper A4 500 sheets"]),
}

# Conditions a customer can report per (category, subcategory) when asking for a return. Limited to the
# conditions the reference policy evaluator reads exactly as the policy page does, and to the values in
# data/rules/vocabulary.json (the contract shared with Team C).
CONDITIONS = {
    "electronics": ["sealed", "sealed", "opened", "used", "defective"],
    "beauty_health": ["sealed", "sealed", "opened", "used"],
    "fashion/apparel": ["unworn_tags_intact", "unworn_tags_intact", "used"],
    "fashion": ["sealed"],
    "home_kitchen/home_decor": ["sealed", "opened"],
    "home_kitchen/bedding": ["sealed", "unwrapped"],
    "home_kitchen": ["sealed", "sealed", "used", "defective"],
    "sports": ["sealed", "sealed", "used"],
    "toys": ["sealed", "sealed", "used", "missing_parts"],
    "baby": ["sealed", "sealed", "opened"],
    "grocery": ["sealed"],
    "pet_supplies": ["sealed", "sealed", "opened", "used"],
    "automotive": ["sealed", "opened", "used", "defective"],
    "tools": ["sealed", "sealed", "used", "damaged"],
    "books": ["sealed", "used", "damaged"],
    "stationery": ["sealed", "sealed", "used", "damaged"],
}
MINUTES_CONDITIONS = ["sealed", "sealed", "sealed", "opened", "used", "defective"]


def conditions_for(cat, sub, section):
    if section == "minutes":
        return MINUTES_CONDITIONS
    return CONDITIONS.get(f"{cat}/{sub}") or CONDITIONS[cat]


def default_condition(cat, sub):
    return "unworn_tags_intact" if (cat, sub) == ("fashion", "apparel") else "sealed"
