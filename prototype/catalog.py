"""Workbook-backed retailer and SKU lookups. Stock is mutable only inside SQLite."""

import json
import re
import sqlite3
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = json.loads((ROOT / "data" / "case.json").read_text(encoding="utf-8"))
RETAILERS = {r["id"]: r for r in DATA["retailers"]}
PREFS = {r["retailer"]: r for r in DATA["preferences"]}
PRODUCTS = {p["sku"]: p for p in DATA["products"]}
HISTORY = {
    rid: {h["sku"]: h for h in DATA["history"] if h["retailer"] == rid}
    for rid in RETAILERS
}
LANGUAGES = {
    "English": "en-IN",
    "Hindi": "hi-IN",
    "Tamil": "ta-IN",
    "Gujarati": "gu-IN",
    "Marathi": "mr-IN",
    "Telugu": "te-IN",
    "Kannada": "kn-IN",
    "Malayalam": "ml-IN",
    "Bengali": "bn-IN",
    "Odia": "or-IN",
    "Assamese": "as-IN",
}


def connect(path):
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    return db


def initialise(path):
    with connect(path) as db:
        db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS stock(distributor TEXT, sku TEXT, units INTEGER CHECK(units>=0), PRIMARY KEY(distributor,sku));
        CREATE TABLE IF NOT EXISTS calls(id TEXT PRIMARY KEY, state TEXT);
        CREATE TABLE IF NOT EXISTS orders(id TEXT PRIMARY KEY, call_id TEXT UNIQUE, body TEXT);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, call_id TEXT, kind TEXT, detail TEXT);
        CREATE TABLE IF NOT EXISTS suppressed(retailer TEXT PRIMARY KEY);
        """)
        db.executemany(
            "INSERT OR IGNORE INTO stock VALUES (?,?,?)",
            [(r["distributor"], r["sku"], r["units"]) for r in DATA["inventory"]],
        )
        db.executemany(
            "INSERT OR IGNORE INTO suppressed VALUES (?)",
            [(r["retailer"],) for r in PREFS.values() if r["dnc"] == "Yes"],
        )


def stock_map(db, rid):
    return dict(
        db.execute(
            "SELECT sku,units FROM stock WHERE distributor=?",
            (RETAILERS[rid]["distributor"],),
        )
    )


def label(sku):
    p = PRODUCTS[sku]
    return f"{p['name']} {p['pack']}"


def suggestion(rid, stock):
    pref = PREFS[rid]
    history = HISTORY[rid]
    options = [sku for sku in history if stock.get(sku, 0) >= PRODUCTS[sku]["moq"]]
    if not options:
        return None

    def score(sku):
        h = history[sku]
        return (
            h["days"] / pref["frequency"]
            + 0.25 * (PRODUCTS[sku]["brand"] in pref["brands"]),
            sku,
        )

    sku = max(options, key=score)
    h = history[sku]
    qty = min(
        stock[sku],
        max(
            PRODUCTS[sku]["moq"],
            round((h["last"] + h["monthly"] * pref["frequency"] / 30) / 2),
        ),
    )
    return sku, qty


def cross_sell(rid, stock, cart):
    opportunity = PREFS[rid]["opportunity"]
    choices = {
        "Parachute 200 ml to 500 ml trade-up": ["P001"] if "P002" in cart else [],
        "Saffola Oats cross-sell": ["P009", "P010"],
        "Saffola Gold replenishment": ["P006", "P007"],
        "Livon / Set Wet personal care cross-sell": ["P013", "P012", "P014", "P015"],
    }.get(opportunity, [])
    if any(PRODUCTS[s]["brand"] == "Parachute" for s in cart):
        choices = [
            s for s in HISTORY[rid] if PRODUCTS[s]["brand"] == "Saffola"
        ] + choices
    return next(
        (s for s in choices if s not in cart and stock.get(s, 0) >= PRODUCTS[s]["moq"]),
        None,
    )


def normalize(raw):
    s = unicodedata.normalize("NFKC", raw).lower().replace("’", "'").replace("‑", "-")
    s = re.sub(r"(?<!\d),|,(?!\d)", " ", s)
    s = "".join(str(unicodedata.digit(c)) if c.isdecimal() else c for c in s)
    replacements = {
        "भेज दो": "भेजो",
        "दे दो": "दीजिए",
        "कर दो": "करें",
        "जोड़ दो": "जोड़ें",
        "पैराशूट": "parachute",
        "पैराशुट": "parachute",
        "नारियल तेल": "coconut oil",
        "सफोला": "saffola",
        "सफ़ोला": "saffola",
        "लिवॉन": "livon",
        "गोल्ड": "gold",
        "ओट्स": "oats",
        "नीहार": "nihar",
        "निहार": "nihar",
        "मिलीलीटर": "ml",
        "एमएल": "ml",
        "एम एल": "ml",
        "लीटर": "litre",
        "पीस": "pieces",
        "डिब्बे": "boxes",
        "आधा दर्जन": " half dozen ",
        "aadha darjan": " half dozen ",
        "half a dozen": " half dozen ",
        "half dozen": " 6 ",
        "a dozen": " 12 ",
        "one dozen": " 12 ",
        "a couple of": " 2 ",
        "ek darjan": " 12 ",
        "दर्जन": "dozen",
        "பாராசூட்": "parachute",
        "சஃபோலா": "saffola",
        "லிட்டர்": "litre",
        "மில்லி": "ml",
    }
    for a, b in replacements.items():
        s = s.replace(a, b)
    words = {
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
        "eleven": 11,
        "twelve": 12,
        "fifteen": 15,
        "twenty": 20,
        "ek": 1,
        "teen": 3,
        "chaar": 4,
        "paanch": 5,
        "chhe": 6,
        "das": 10,
        "baarah": 12,
        "pandrah": 15,
        "एक": 1,
        "दो": 2,
        "तीन": 3,
        "चार": 4,
        "पाँच": 5,
        "पांच": 5,
        "छह": 6,
        "दस": 10,
        "बारह": 12,
        "पंद्रह": 15,
    }
    for word, n in words.items():
        s = re.sub(r"(?<!\S)" + word + r"(?=[\s,.;!?]|$)", str(n), s)
    s = re.sub(r"(?<!\d)(\d+)\s+(?:dozen|darjan)\b", lambda m: str(int(m[1]) * 12), s)
    s = re.sub(r"\bdozen\b", "12", s)
    s = re.sub(r"(\d)\s*(?:litres?|liters?|ltr|l)\b", r"\1 litre", s)
    s = re.sub(r"(\d)\s*(ml|kg|g)\b", r"\1 \2", s)
    return " ".join(s.split()).strip(" .,!?\u0964")


def match_products(line):
    codes = list(dict.fromkeys(x.upper() for x in re.findall(r"\bp\d{3}\b", line)))
    if codes:
        return [c for c in codes if c in PRODUCTS]
    words = set(re.findall(r"[a-z]+", line))
    vocab = {w for p in PRODUCTS.values() for w in p["name"].lower().split()}
    tokens = words & vocab
    if not tokens:
        return []
    packs = set(re.findall(r"\b\d+\s+(?:ml|kg|g|litre)\b", line))
    # Multiple litres without an explicit pack can describe total volume.
    exact_packs = {p["pack"] for p in PRODUCTS.values()}
    if packs - exact_packs and not (
        "gold" in tokens and all(v.endswith("litre") for v in packs)
    ):
        return []
    selected = [
        s
        for s, p in PRODUCTS.items()
        if tokens <= set(p["name"].lower().split())
        and (
            not packs
            or p["pack"] in packs
            or ("gold" in tokens and not packs & exact_packs and p["pack"] == "1 litre")
        )
    ]
    return selected


def quantity(line, sku):
    if re.search(r"\b(box(?:es)?|cartons?|cases?|crates?)\b|पेटी|डब्ब", line):
        return None, "boxes"
    if re.search(
        r"\b(few|several|some|couple more|around|approx|maybe|between|or)\b|थोड़े", line
    ):
        return None, "quantity"
    if re.search(r"\d[.,]\d|(?<!\w)-\s*\d|\bminus\b|negative", line):
        return None, "quantity"
    plain = re.sub(r"\bp\d{3}\b", "", line)
    volume = re.findall(r"(\d+)\s+litre\b", plain)
    plain = re.sub(r"\b\d+\s+(?:ml|g|kg|litre)\b", "", plain)
    nums = re.findall(r"\b\d+\b", plain)
    if len(nums) == 1:
        return int(nums[0]), None
    if not nums and len(volume) == 1 and sku == "P006" and int(volume[0]) not in (1, 5):
        return int(volume[0]), None
    return None, "quantity"
