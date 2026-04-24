# utils.py - Complete project-wide utilities
import re
import time

FREE_DELIVERY_THRESHOLD = 50.0
DELIVERY_CHARGE = 4.99

def get_order_total(order):
    return sum(v["item"]["price"] * v["qty"] for v in order.values())

def get_delivery_fee(subtotal, delivery_type):
    if delivery_type != "delivery":
        return 0.0
    if subtotal >= FREE_DELIVERY_THRESHOLD:
        return 0.0
    return DELIVERY_CHARGE

def get_order_text(order):
    if not order:
        return "Empty cart"
    lines = []
    for v in order.values():
        item = v["item"]
        base = f"{item['emoji']} {item['name']} x{v['qty']} — ${item['price'] * v['qty']:.2f}"
        lines.append(base)
        for comp in v.get("components", []):
            lines.append(f"  • {comp}")
        for side in v.get("sides", []):
            lines.append(f"  • Side: {side}")
    return "\n".join(lines)

def find_item(item_id, MENU):
    for cat_key, cat_data in MENU.items():
        if item_id in cat_data["items"]:
            return cat_key, cat_data["items"][item_id]
    return None, None

def has_any_side(order):
    return any(k.startswith("SD") for k in order)

def has_any_drink(order):
    return any(k.startswith("DR") for k in order)

def has_any_dessert(order):
    return any(k.startswith("DS") for k in order)

def is_burger(item_id):
    return item_id.startswith("FF")

def is_pizza(item_id):
    return item_id.startswith("PZ")

def truncate_title(title, max_len=24):
    if len(title) <= max_len:
        return title
    return title[:max_len - 1] + "…"

def safe_btn(text, max_len=20):
    if len(text) <= max_len:
        return text
    return text[:max_len - 1] + "…"

def guess_category(text_lower):
    if any(w in text_lower for w in ["deal", "combo", "offer"]): return "deals"
    if any(w in text_lower for w in ["burger", "smash", "bacon", "chicken sandwich"]): return "fastfood"
    if any(w in text_lower for w in ["pizza", "pepperoni", "margherita"]): return "pizza"
    if any(w in text_lower for w in ["bbq", "ribs", "brisket"]): return "bbq"
    if any(w in text_lower for w in ["fish", "salmon", "shrimp"]): return "fish"
    if any(w in text_lower for w in ["drink", "coke", "pepsi", "shake"]): return "drinks"
    if any(w in text_lower for w in ["dessert", "cake", "brownie"]): return "desserts"
    if any(w in text_lower for w in ["fries", "wings", "nachos", "side"]): return "sides"
    return None

def is_order_status_query(text_lower):
    keywords = [
        "order status", "where is my order", "where's my order", "order update", "ready yet",
        "how much time", "how long will", "not arrived", "not delivered", "haven't received",
        "where's my food", "when will", "kitna time", "kab aayega", "eta"
    ]
    if any(w in text_lower for w in keywords):
        return True
    if re.search(r'(order|#)\s*#?\s*\d{5}', text_lower):
        return True
    return False

def extract_order_number(text):
    m = re.search(r'\b(\d{5})\b', text or "")
    return int(m.group(1)) if m else None

def is_valid_name(text):
    t = text.strip()
    if len(t) < 2 or len(t) > 30:
        return False
    if re.match(r"^[A-Z_]+$", t):
        return False
    lower = t.lower()
    if lower in ["menu", "hi", "hello", "hey", "start", "back", "cancel", "help", "yes", "no", "ok", "thanks", "restart", "reset"]:
        return False
    if not re.search(r"[A-Za-z\u0600-\u06FF\u0900-\u097F\u4e00-\u9fff\u0D00-\u0D7F]", t):
        return False
    return True

def is_valid_address(text):
    t = text.strip()
    if len(t) < 8:
        return False
    lower = t.lower()
    has_digit = bool(re.search(r"\d", t))
    has_comma = "," in t
    has_word = any(w in lower for w in ["street", "st", "road", "rd", "ave", "avenue", "lane", "ln", "drive", "dr", "block", "building", "apt"])
    return has_digit or has_comma or has_word

def is_thanks(text_lower):
    return any(w in text_lower for w in ["thanks", "thank you", "thx", "ty"])

def is_bye(text_lower):
    return text_lower in ["bye", "goodbye", "cya", "see ya"]

def is_menu_request(text_lower):
    return text_lower in ["menu", "show menu", "see menu", "browse menu", "main menu", "show me menu", "the menu"] or text_lower.startswith("menu ")
