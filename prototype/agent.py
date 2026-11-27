"""Conversational order state machine. All external writes use the caller's SQLite transaction."""

import datetime as dt
import hashlib
import json
import re
import uuid

from catalog import (
    HISTORY,
    PREFS,
    PRODUCTS,
    RETAILERS,
    cross_sell,
    label,
    match_products,
    normalize,
    quantity,
    stock_map,
    suggestion,
)

TERMINAL = {
    "confirmed",
    "callback",
    "handoff",
    "declined",
    "wrong_contact",
    "opt_out",
    "insight",
}
CONFIRM = {
    "confirm order",
    "confirm the order",
    "i confirm",
    "i confirm the order",
    "yes confirm",
    "yes confirm the order",
    "yes please place the order",
    "place the order",
    "order confirm kar do",
    "haan confirm kar dijiye",
    "ऑर्डर पक्का करें",
    "ऑर्डर कन्फर्म करें",
    "हाँ पक्का करें",
    "हाँ कन्फर्म कर दीजिए",
    "ऑर्डर की पुष्टि करता हूँ",
    "ऑर्डर की पुष्टि करती हूँ",
    "ஆர்டரை உறுதிப்படுத்துகிறேன்",
    "ઓર્ડર કન્ફર્મ કરો",
    "ऑर्डर निश्चित करा",
    "ఆర్డర్ నిర్ధారించండి",
    "ಆರ್ಡರ್ ದೃಢೀಕರಿಸಿ",
    "ഓർഡർ സ്ഥിരീകരിക്കുക",
    "অর্ডার নিশ্চিত করুন",
    "অৰ্ডাৰ নিশ্চিত কৰক",
    "ଅର୍ଡର ନିଶ୍ଚିତ କରନ୍ତୁ",
}
IDENTITY = {
    "yes",
    "yes speaking",
    "yes speaking go ahead",
    "yes go ahead",
    "that's me",
    "speaking",
    "haan",
    "haan ji",
    "हाँ",
    "हां",
    "हाँ जी",
    "जी",
    "जी हाँ",
    "ஆம்",
    "ఆవును",
    "అవును",
    "ಹೌದು",
    "അതെ",
    "હા",
    "हो",
    "হ্যাঁ",
    "হয়",
    "ହଁ",
}
NATIVE = {
    "Tamil": "தமிழ்",
    "Gujarati": "ગુજરાતી",
    "Marathi": "मराठी",
    "Telugu": "తెలుగు",
    "Kannada": "ಕನ್ನಡ",
    "Malayalam": "മലയാളം",
    "Bengali": "বাংলা",
    "Odia": "ଓଡ଼ିଆ",
    "Assamese": "অসমীয়া",
    "Hindi": "हिंदी",
    "English": "अंग्रेज़ी",
}
LOCAL_HELP = {
    "Tamil": "மொழி உதவிக்கு ஒரு பிரதிநிதியை அழைக்கச் சொல்கிறேன்.",
    "Gujarati": "તમારી ભાષામાં વાત કરવા પ્રતિનિધિને જાણ કરીશ.",
    "Marathi": "आपल्या भाषेत बोलण्यासाठी प्रतिनिधीला कळवते.",
    "Telugu": "మీ భాషలో మాట్లాడే ప్రతినిధికి తెలియజేస్తాను.",
    "Kannada": "ನಿಮ್ಮ ಭಾಷೆಯಲ್ಲಿ ಮಾತನಾಡಲು ಪ್ರತಿನಿಧಿಗೆ ತಿಳಿಸುತ್ತೇನೆ.",
    "Malayalam": "നിങ്ങളുടെ ഭാഷയിൽ സംസാരിക്കാൻ പ്രതിനിധിയെ അറിയിക്കാം.",
    "Bengali": "আপনার ভাষায় কথা বলার জন্য প্রতিনিধিকে জানাচ্ছি।",
    "Odia": "ଆପଣଙ୍କ ଭାଷାରେ କଥା ହେବା ପାଇଁ ପ୍ରତିନିଧିଙ୍କୁ ଜଣାଇବି।",
    "Assamese": "আপোনাৰ ভাষাত কথা পাতিবলৈ প্ৰতিনিধিক জনাম।",
}


def event(db, s, kind, detail):
    db.execute(
        "INSERT INTO events(call_id,kind,detail) VALUES (?,?,?)",
        (s["id"], kind, detail),
    )


def say(db, s, model, en, hi, *slots):
    lang = s["language"]
    out = hi if lang == "Hindi" else en
    if lang not in ("Hindi", "English"):
        out = model.translate(en, lang)
        if not out:
            event(db, s, "language_handoff", lang)
            s["phase"] = "handoff"
            return LOCAL_HELP[lang]
        s["model_used"] = True
    return re.sub(r"\[\[(\d+)\]\]", lambda m: str(slots[int(m[1])]), out)


def start(db, rid, clock, model):
    if rid not in RETAILERS:
        raise ValueError("Choose a listed retailer.")
    try:
        t = dt.time.fromisoformat(clock)
    except (ValueError, TypeError):
        raise ValueError("Choose a valid simulated local time.")
    if db.execute("SELECT 1 FROM suppressed WHERE retailer=?", (rid,)).fetchone():
        raise ValueError("This retailer is marked do not call.")
    pref = PREFS[rid]
    lo, hi = map(dt.time.fromisoformat, pref["window"].split("-"))
    if not lo <= t < hi:
        raise ValueError(
            "This retailer takes calls during " + pref["window"] + " local time."
        )
    s = {
        "id": uuid.uuid4().hex,
        "retailer": rid,
        "phase": "identity",
        "language": pref["language"],
        "cart": {},
        "focus": None,
        "candidates": [],
        "review": None,
        "offered": False,
        "misses": 0,
        "turns": [],
        "model_used": False,
        "order": None,
    }
    greeting = say(
        db,
        s,
        model,
        "Hello, this is Marico’s automated ordering assistant. Do you place orders for [[0]], and is now a good time?",
        "नमस्ते, मैं मारिको की स्वचालित ऑर्डर सहायक हूँ। क्या आप [[0]] के ऑर्डर देखते हैं, और क्या अभी बात कर सकते हैं?",
        RETAILERS[rid]["name"],
    )
    s["turns"].append(["agent", greeting])
    return s


def signature(cart):
    return hashlib.sha256(json.dumps(cart, sort_keys=True).encode()).hexdigest()


def lines(cart):
    return [
        {
            "sku": sku,
            "product": PRODUCTS[sku]["name"],
            "pack": PRODUCTS[sku]["pack"],
            "quantity": q,
            "unit": PRODUCTS[sku]["unit"],
            "unit_price_inr": PRODUCTS[sku]["price"],
            "line_total_inr": q * PRODUCTS[sku]["price"],
        }
        for sku, q in cart.items()
    ]


def summary(db, s, model):
    s.pop("offer_sku", None)
    s.pop("offer_qty", None)
    if not s["cart"]:
        return say(
            db,
            s,
            model,
            "Your draft is empty. Which product would you like?",
            "ड्राफ़्ट खाली है। कौन सा उत्पाद चाहिए?",
        )
    desc = ", ".join(f"{label(i)} × {q}" for i, q in s["cart"].items())
    total = sum(r["line_total_inr"] for r in lines(s["cart"]))
    s["phase"] = "review"
    s["review"] = signature(s["cart"])
    return say(
        db,
        s,
        model,
        "The full order is [[0]]. The billing total is ₹[[1]]. Shall I place this order? Say ‘confirm order’ or tell me what to change.",
        "आपका पूरा ऑर्डर है [[0]]। कुल बिल ₹[[1]] है। ऑर्डर भेज दूँ? ‘ऑर्डर पक्का करें’ कहें, या बदलाव बताएं।",
        desc,
        f"{total:,}",
    )


def commit(db, s, model):
    if s["phase"] != "review" or s["review"] != signature(s["cart"]):
        return summary(db, s, model)
    rid = s["retailer"]
    stocks = stock_map(db, rid)
    for sku, q in s["cart"].items():
        if q > stocks[sku]:
            s["phase"] = "shopping"
            s["review"] = None
            s["focus"] = sku
            return say(
                db,
                s,
                model,
                "Stock changed. [[0]] now has [[1]] units available. What quantity should I use, or shall I remove it?",
                "स्टॉक बदल गया है। [[0]] की [[1]] इकाइयाँ बची हैं। कितनी रखें, या इसे हटा दूँ?",
                label(sku),
                stocks[sku],
            )
    d = RETAILERS[rid]["distributor"]
    order = {
        "order_id": "MC-" + uuid.uuid4().hex[:12].upper(),
        "call_id": s["id"],
        "retailer_id": rid,
        "distributor_id": d,
        "currency": "INR",
        "confirmation_status": "confirmed",
        "confirmed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "items": lines(s["cart"]),
    }
    order["order_value_inr"] = sum(r["line_total_inr"] for r in order["items"])
    db.execute(
        "INSERT INTO orders VALUES (?,?,?)",
        (order["order_id"], s["id"], json.dumps(order, ensure_ascii=False)),
    )
    for sku, q in s["cart"].items():
        db.execute(
            "UPDATE stock SET units=units-? WHERE distributor=? AND sku=?", (q, d, sku)
        )
    s.update(order=order, phase="confirmed")
    # A receipt remains valid even if translation is unavailable after commit.
    if s["language"] not in ("English", "Hindi"):
        return f"✓ {order['order_id']} · INR {order['order_value_inr']:,}"
    return say(
        db,
        s,
        model,
        "Thank you. Your order is confirmed. Your reference is [[0]].",
        "धन्यवाद। आपका ऑर्डर पक्का हो गया। ऑर्डर नंबर [[0]] है।",
        order["order_id"],
    )


def recommend(db, s, model, stock):
    rid = s["retailer"]
    rec = suggestion(rid, stock)
    if not rec:
        return say(
            db,
            s,
            model,
            "What are you running low on today?",
            "आज किस उत्पाद का स्टॉक कम है?",
        )
    sku, q = rec
    h = HISTORY[rid][sku]
    s["focus"] = sku
    s["candidates"] = []
    s["offer_sku"] = sku
    s["offer_qty"] = q
    return say(
        db,
        s,
        model,
        "How is your stock of [[0]]? You took [[1]] last time, [[2]] days ago. Would [[3]] units suit you this time, or do you need something else?",
        "[[0]] का कितना स्टॉक बचा है? पिछली बार आपने [[2]] दिन पहले [[1]] लिए थे। इस बार [[3]] रखें या कुछ और चाहिए?",
        label(sku),
        h["last"],
        h["days"],
        q,
    )


def end(db, s, model, kind, raw, en, hi):
    s["phase"] = kind
    s["review"] = None
    event(db, s, kind, raw)
    return say(db, s, model, en, hi)


def process(db, s, raw, model, confidence=None):
    if s["phase"] in TERMINAL:
        return s
    if not isinstance(raw, str) or not raw.strip() or len(raw) > 1200:
        raise ValueError("Enter a short retailer response.")
    s["turns"].append(["retailer", raw])
    s["model_used"] = False
    if confidence is not None and confidence < 0.58:
        s["review"] = None
        reply = say(
            db,
            s,
            model,
            "I didn’t catch that. Could you repeat the product and quantity?",
            "ठीक से सुनाई नहीं दिया। उत्पाद और मात्रा फिर बताइए।",
        )
    else:
        reply = respond(db, s, model, raw)
    s["turns"].append(["agent", reply])
    return s


def respond(db, s, model, raw):
    t = normalize(raw)
    if (
        s["language"] == "Hindi"
        and re.search(
            r"\b(i would like|could you|what is the price|please send|can you|i need)\b",
            t,
        )
        and not re.search(r"[\u0900-\u097f]|\b(hai|chahiye|bhejo)\b", t)
    ):
        s["language"] = "English"
    result = route(db, s, model, raw, t, False)
    if result is not None:
        return result
    if model.enabled:
        english = model.understand(raw)
        if english and normalize(english) != t:
            s["model_used"] = True
            result = route(db, s, model, raw, normalize(english), True)
            if result is not None:
                return result
    s["misses"] += 1
    s["review"] = None
    if s["misses"] >= 2:
        return end(
            db,
            s,
            model,
            "handoff",
            raw,
            "I’ll ask a sales representative to help with that.",
            "मैं आपकी मदद के लिए बिक्री प्रतिनिधि को अनुरोध भेज रही हूँ।",
        )
    return say(
        db,
        s,
        model,
        "Could you rephrase that or give the product, pack and number of pieces? I can also arrange a representative callback.",
        "कृपया उत्पाद, पैक और पीस की संख्या बताएं। चाहें तो प्रतिनिधि के कॉल का अनुरोध भी दर्ज कर सकती हूँ।",
    )


def route(db, s, model, raw, t, interpreted):
    rid = s["retailer"]
    stocks = stock_map(db, rid)

    def message(en, hi, *args):
        return say(db, s, model, en, hi, *args)

    def close(kind, en, hi):
        return end(db, s, model, kind, raw, en, hi)

    def has(pattern):
        return re.search(pattern, t, re.IGNORECASE) is not None

    if has(
        r"do not call|don't call|stop calling|never call|dobara call mat|कॉल मत|फोन मत"
    ):
        db.execute("INSERT OR IGNORE INTO suppressed VALUES (?)", (rid,))
        return close(
            "opt_out",
            "Understood. I’ve removed this outlet from future calls.",
            "ठीक है। आगे से इस दुकान को कॉल नहीं करेंगे।",
        )
    if has(
        r"wrong number|wrong (person|retailer|shop)|not (the owner|the named retailer|my shop)|गलत नंबर|गलत नम्बर"
    ):
        return close(
            "wrong_contact",
            "Thank you for letting me know. I’ll end the call here.",
            "बताने के लिए धन्यवाद। कॉल यहीं समाप्त करती हूँ।",
        )
    if has(
        r"complain|wrong quantity|incorrect|damaged|missing items|wrong order|गलत मात्रा|शिकायत|गलत सामान"
    ):
        return close(
            "handoff",
            "I’m sorry the delivery went wrong. I’ve logged your complaint for the distributor’s representative.",
            "डिलीवरी की समस्या के लिए खेद है। मैंने आपकी शिकायत प्रतिनिधि के लिए दर्ज की है।",
        )
    if has(r"\bhuman\b|representative|sales rep|salesperson|इंसान|प्रतिनिधि"):
        return close(
            "handoff",
            "I’ll ask your distributor’s representative to call you back.",
            "मैं वितरक के प्रतिनिधि को वापस कॉल करने का अनुरोध भेजती हूँ।",
        )
    if has(
        r"call (me |us |again )?(back|later|after|tomorrow|at)|callback|\bbusy\b|फिर कॉल|बाद में|व्यस्त"
    ):
        return close(
            "callback",
            "I’ve recorded your callback request and the timing you gave. Thank you.",
            "कॉल के लिए आपका अनुरोध और बताया गया समय दर्ज कर लिया है। धन्यवाद।",
        )
    if has(r"competitor|another brand.*offer|दूसरे ब्रांड"):
        return close(
            "insight",
            "Thanks for sharing what’s happening in your store. I’ve noted it for the sales team.",
            "जानकारी के लिए धन्यवाद। मैंने बिक्री टीम के लिए दर्ज कर लिया है।",
        )
    if has(
        r"not interested|no order|don't want to order|fully stocked|stocked up|ऑर्डर नहीं|नहीं चाहिए कुछ"
    ):
        return close(
            "declined",
            "Understood. Thank you for your time.",
            "ठीक है। समय देने के लिए धन्यवाद।",
        )
    for lang, native in NATIVE.items():
        if (lang.lower() in t or native in t) and (
            len(t.split()) < 9 or has(r"switch|speak|बात")
        ):
            s["language"] = lang
            if s["phase"] == "identity":
                return message(
                    "Sure. Do you place orders for [[0]], and may we talk now?",
                    "ज़रूर। क्या आप [[0]] के ऑर्डर देखते हैं और अभी बात कर सकते हैं?",
                    RETAILERS[rid]["name"],
                )
            if s["phase"] == "review":
                return summary(db, s, model)
            return message(
                "Of course. What would you like to order?", "ज़रूर। क्या ऑर्डर करना चाहेंगे?"
            )
    if s["phase"] == "identity":
        if t in IDENTITY or has(
            r"^yes[, ]+(this is|i (place|handle)|i am|i'm|that's|go ahead)|^(हाँ|हां|जी)[, ]+(मैं|बोल|बता)|^haan (boliye|bataiye)"
        ):
            s["phase"] = "shopping"
            s["misses"] = 0
            return recommend(db, s, model, stocks)
        if t in ("no", "नहीं", "नही"):
            return close(
                "declined",
                "Understood. Thank you for your time.",
                "ठीक है। समय देने के लिए धन्यवाद।",
            )
        if interpreted or not model.enabled:
            return message(
                "Before we continue, do you handle this shop’s orders and have time to talk?",
                "क्या आप दुकान के ऑर्डर देखते हैं और अभी बात कर सकते हैं?",
            )
        return None
    if not interpreted and t in CONFIRM:
        return commit(db, s, model)
    if has(r"discount|offer price|छूट|डिस्काउंट"):
        return message(
            "I can quote the distributor’s billing price. A representative needs to approve any extra discount.",
            "मैं वितरक का बिलिंग मूल्य बता सकती हूँ। अतिरिक्त छूट के लिए प्रतिनिधि की मंज़ूरी चाहिए।",
        )
    if has(
        r"\b(review|read back|readback|checkout|summary|total|that's all|that's it)\b|सारांश|कुल बिल|पूरा ऑर्डर|बस इतना"
    ):
        return summary(db, s, model)
    if has(r"\brecommend|suggest|what.*(reorder|running low)|क्या मंगा|सुझाव"):
        return recommend(db, s, model, stocks)
    if (
        s["phase"] == "shopping"
        and s.get("offer_sku")
        and t
        in {
            "yes",
            "yes please",
            "yes that works",
            "that works",
            "yes please add it",
            "हाँ",
            "हां",
            "हाँ जी",
            "haan",
            "haan ji",
        }
    ):
        sku = s.pop("offer_sku")
        q = s.pop("offer_qty", None)
        if q:
            return edit_cart(db, s, model, f"send {sku.lower()} {q} pieces", stocks)
        s["focus"] = sku
        return message(
            "How many packs of [[0]] should I add?", "[[0]] के कितने पैक जोड़ूँ?", label(sku)
        )
    if has(r"same as last time|repeat the last quantity|पिछली बार जितना"):
        ms = match_products(t)
        sku = ms[0] if len(ms) == 1 else (s["focus"] if not ms else None)
        if sku in HISTORY[rid]:
            return edit_cart(
                db,
                s,
                model,
                f"send {sku.lower()} {HISTORY[rid][sku]['last']} pieces",
                stocks,
            )
        return message(
            "Which exact product and pack do you want to repeat?",
            "कौन सा उत्पाद और पैक फिर चाहिए?",
        )
    if has(r"last (time|order)|previous order|पिछली बार") and not has(
        r"more|few|same|और"
    ):
        ms = match_products(t)
        sku = ms[0] if len(ms) == 1 else (s["focus"] if not ms else None)
        if sku in HISTORY[rid]:
            h = HISTORY[rid][sku]
            return message(
                "You bought [[0]] units of [[1]], [[2]] days ago. How many do you need today?",
                "आपने [[2]] दिन पहले [[1]] की [[0]] इकाइयाँ ली थीं। आज कितनी चाहिए?",
                h["last"],
                label(sku),
                h["days"],
            )
    if has(r"\b(have|got|remaining|left|on hand|on the shelf)\b|बचे|बची") and not has(
        r"send|order|need|भेज"
    ):
        ms = match_products(t)
        if len(ms) == 1:
            s["focus"] = ms[0]
        return message(
            "Thanks for the stock update. How many additional packs should I put on the order?",
            "स्टॉक की जानकारी के लिए धन्यवाद। ऑर्डर में और कितने पैक रखें?",
        )
    if (
        s.get("offer_sku")
        and has(r"^(yes|haan|हाँ|जी)\b")
        and re.search(r"\d", t)
        and not match_products(t)
    ):
        t = t + " " + s["offer_sku"].lower()
    if (
        t in ("no thanks", "no thank you", "skip", "no", "नहीं", "नहीं धन्यवाद")
        and s["cart"]
    ):
        return summary(db, s, model)
    if interpreted and t in CONFIRM:
        return message(
            "Please say the explicit confirmation phrase, or use the Confirm order button after review.",
            "कृपया ‘ऑर्डर पक्का करें’ कहें या सारांश के बाद पुष्टि बटन चुनें।",
        )
    if has(
        r"\b(if|maybe|might|don't|dont|not|instead of|rather than|except)\b|मत भेज|नहीं भेज|न भेज"
    ):
        s["review"] = None
        return message(
            "Which item and quantity should I keep? Your draft is unchanged.",
            "कौन सा उत्पाद और कितनी मात्रा रखें? ड्राफ़्ट अभी पहले जैसा है।",
        )
    if has(
        r"\b(price|cost|mrp|stock available|available|availability|in stock|pack sizes?)\b|कीमत|बिलिंग मूल्य|दाम"
    ):
        ms = match_products(t)
        if (
            not ms
            and s["focus"]
            and re.fullmatch(
                r"(what is (the |its )?price|how much (is it|does it cost)|is (it|that) (available|in stock)|price|mrp|कीमत|दाम)( please)?",
                t,
            )
        ):
            ms = [s["focus"]]
        if not ms:
            return message(
                "Which product and pack would you like me to check?",
                "किस उत्पाद और पैक की जानकारी चाहिए?",
            )
        s["candidates"] = ms if len(ms) > 1 else []
        if len(ms) == 1:
            s["focus"] = ms[0]
        rows = [
            f"{label(i)} · ₹{PRODUCTS[i]['price']} (MRP ₹{PRODUCTS[i]['mrp']}) · {stocks[i]}"
            for i in ms
        ]
        return message(
            "Here are the billing prices and available unit counts. [[0]]. Which pack do you need?",
            "बिलिंग मूल्य और उपलब्ध इकाइयाँ ये हैं। [[0]]। कौन सा पैक चाहिए?",
            "; ".join(rows),
        )
    if s["phase"] == "review" and t in ("yes", "हाँ", "haan", "ok", "okay"):
        return message(
            "To place the order, say ‘confirm order’.",
            "ऑर्डर भेजने के लिए ‘ऑर्डर पक्का करें’ कहें।",
        )
    return edit_cart(db, s, model, t, stocks)


def edit_cart(db, s, model, t, stocks):
    def msg(en, hi, *a):
        return say(db, s, model, en, hi, *a)

    segments = re.split(r"\s+(?:and|aur|और)\s+|\s*[;&]\s*", t)
    draft = s["cart"].copy()
    last = None
    for segment in segments:
        ms = (
            []
            if re.match(r"^set (it|that|the quantity) ", segment)
            else match_products(segment)
        )
        if not ms and re.search(r"\bp\d{3}\b", segment):
            s["review"] = None
            return msg(
                "I couldn’t find that SKU in the catalogue. Could you check its code?",
                "कैटलॉग में यह कोड नहीं मिला। कृपया कोड जाँचें।",
            )
        if not ms and s["candidates"]:
            pack = re.search(r"\b\d+ (ml|g|kg|litre)\b", segment)
            ms = [i for i in s["candidates"] if pack and PRODUCTS[i]["pack"] == pack[0]]
        referent = re.fullmatch(
            r"(?:(?:make|change|set|send|order|add|remove|delete|yes|actually|please|it|that|to|the|quantity|pieces?|units?|packs?|boxes|instead|कर|दो|दीजिए|भेजो|हटाओ|\d+)\s*)+",
            segment,
        )
        if not ms and s["focus"] and not s["candidates"] and referent:
            ms = [s["focus"]]
        if not ms:
            return None
        if len(ms) > 1:
            s["candidates"] = ms
            s["focus"] = None
            s["review"] = None
            return msg(
                "Which one do you mean? [[0]]. Please give the exact pack and quantity.",
                "कौन सा उत्पाद चाहिए? [[0]]। सही पैक और मात्रा बताएं।",
                ", ".join(label(sku) for sku in ms),
            )
        sku = ms[0]
        s["focus"] = sku
        s["candidates"] = []
        s["review"] = None
        removing = re.search(r"\b(remove|delete|drop)\b|हटाओ|हटा दें", segment)
        if removing:
            draft.pop(sku, None)
            last = sku
            continue
        if stocks[sku] == 0:
            alt = next(
                (
                    other
                    for other, p in PRODUCTS.items()
                    if p["name"] == PRODUCTS[sku]["name"] and stocks[other] > 0
                ),
                None,
            )
            if alt:
                return msg(
                    "[[0]] is out of stock. [[1]] has [[2]] units available. Would that pack work instead?",
                    "[[0]] उपलब्ध नहीं है। [[1]] की [[2]] इकाइयाँ हैं। क्या वह पैक चलेगा?",
                    label(sku),
                    label(alt),
                    stocks[alt],
                )
            return msg(
                "[[0]] is out of stock with your distributor. Shall I check another product?",
                "आपके वितरक के पास [[0]] उपलब्ध नहीं है। कोई दूसरा उत्पाद देखूँ?",
                label(sku),
            )
        q, problem = quantity(segment, sku)
        if problem == "boxes":
            return msg(
                "I don’t have a box-to-piece conversion for [[0]]. How many individual sellable packs would you like?",
                "[[0]] के एक बॉक्स में कितने पीस हैं इसकी जानकारी नहीं है। कुल कितने अलग पैक चाहिए?",
                label(sku),
            )
        if problem:
            return msg(
                "How many individual packs of [[0]] would you like?",
                "[[0]] के कितने अलग पैक चाहिए?",
                label(sku),
            )
        if q < PRODUCTS[sku]["moq"] or q > stocks[sku]:
            return msg(
                "I can supply [[0]] to [[1]] units of [[2]]. What quantity would work?",
                "[[2]] की [[0]] से [[1]] इकाइयाँ उपलब्ध करा सकती हूँ। कितनी रखें?",
                PRODUCTS[sku]["moq"],
                stocks[sku],
                label(sku),
            )
        if re.search(r"\b(more|extra|additional)\b|और जोड़", segment):
            q += draft.get(sku, 0)
        if q > stocks[sku]:
            return msg(
                "That would exceed the [[0]] units available. What final quantity should I enter?",
                "यह उपलब्ध [[0]] इकाइयों से अधिक है। कुल कितनी दर्ज करूँ?",
                stocks[sku],
            )
        draft[sku] = q
        last = sku
    if last is None:
        return None
    correcting = s["phase"] == "review"
    s.pop("offer_sku", None)
    s.pop("offer_qty", None)
    s.update(cart=draft, phase="shopping", review=None, focus=last, misses=0)
    if correcting or not draft:
        return summary(db, s, model)
    extra = cross_sell(s["retailer"], stocks, draft)
    if not s["offered"] and extra:
        s["offered"] = True
        s["offer_sku"] = extra
        history = HISTORY[s["retailer"]].get(extra)
        s["offer_qty"] = None if history else 1
        reason = (
            msg(
                "You last bought [[0]] [[1]] days ago. Would you like any today?",
                "आपने [[0]] पिछली बार [[1]] दिन पहले लिया था। क्या आज भी चाहिए?",
                label(extra),
                history["days"],
            )
            if history
            else msg(
                "Your outlet profile suggests [[0]]. Would you like to try a pack?",
                "आपकी दुकान के लिए [[0]] का सुझाव है। क्या एक पैक आज़माना चाहेंगे?",
                label(extra),
            )
        )
        return msg("I’ve updated your draft. ", "मैंने ड्राफ़्ट बदल दिया है। ") + reason
    return summary(db, s, model)
