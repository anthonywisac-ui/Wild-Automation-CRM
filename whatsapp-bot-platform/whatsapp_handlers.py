# whatsapp_handlers.py - from your original, full version
import aiohttp
import random
import time
from config import WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID, MANAGER_NUMBER, WHATSAPP_API_VERSION
from session import SharedSession
from utils import truncate_title, safe_btn, get_order_total, get_order_text, get_delivery_fee
from db import SessionLocal, get_session_data, save_session_data, save_new_order, WhatsappBot

# These will be imported from bot-specific modules
MENU = {}
t = lambda lang, key: key  # placeholder, will be overridden

def set_menu_and_strings(menu, strings_func):
    global MENU, t
    MENU = menu
    t = strings_func

async def send_text_message(to, message):
    # Legacy - uses global config
    url = f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/{WHATSAPP_PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    payload = {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": message}}
    try:
        session = await SharedSession.get_session()
        async with session.post(url, json=payload, headers=headers) as r:
            if r.status >= 400:
                print(f"send_text_message failed {r.status}")
    except Exception as e:
        print(f"send_text_message exception: {e}")

async def send_text_message_v2(to, message, bot: WhatsappBot):
    # Multi-tenant - uses bot-specific config
    token = bot.meta_token or WHATSAPP_TOKEN
    phone_id = bot.phone_number_id or WHATSAPP_PHONE_NUMBER_ID
    url = f"https://graph.facebook.com/v19.0/{phone_id}/messages"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": message}}
    try:
        session = await SharedSession.get_session()
        async with session.post(url, json=payload, headers=headers) as r:
            if r.status >= 400:
                print(f"send_text_message_v2 failed {r.status} for bot {bot.name}")
    except Exception as e:
        print(f"send_text_message_v2 exception: {e}")

async def send_language_selection(sender):
    url = f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/{WHATSAPP_PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "to": sender,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "header": {"type": "text", "text": "🍽️ Wild Bites Restaurant"},
            "body": {"text": "Welcome! Please choose your language:"},
            "footer": {"text": "Language Selection"},
            "action": {
                "button": "🌐 Choose Language",
                "sections": [{
                    "title": "Languages",
                    "rows": [
                        {"id": "LANG_EN", "title": "🇺🇸 English", "description": "Continue in English"},
                        {"id": "LANG_AR", "title": "🇸🇦 العربية", "description": "الاستمرار بالعربية"},
                        {"id": "LANG_HI", "title": "🇮🇳 हिन्दी", "description": "हिंदी में जारी रखें"},
                    ]
                }]
            }
        }
    }
    session = await SharedSession.get_session()
    await session.post(url, json=payload, headers=headers)

async def send_main_menu(sender, current_order, lang):
    total = get_order_total(current_order)
    cart_text = f"\n\n🛒 ${total:.2f}" if current_order else ""
    url = f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/{WHATSAPP_PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "to": sender,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "header": {"type": "text", "text": "🍽️ Wild Bites Restaurant"},
            "body": {"text": f"Menu{cart_text}"},
            "action": {
                "button": "Browse",
                "sections": [
                    {"title": "Categories", "rows": [
                        {"id": "CAT_DEALS", "title": "🔥 Deals", "description": "Best value"},
                        {"id": "CAT_FASTFOOD", "title": "🍔 Burgers", "description": "Smash, chicken"},
                        {"id": "CAT_PIZZA", "title": "🍕 Pizza", "description": "12 inch"},
                        {"id": "CAT_BBQ", "title": "🍖 BBQ", "description": "Ribs, brisket"},
                        {"id": "CAT_FISH", "title": "🐟 Fish", "description": "Cod, salmon"},
                        {"id": "CAT_SIDES", "title": "🍟 Sides", "description": "Fries, wings"},
                        {"id": "CAT_DRINKS", "title": "🥤 Drinks", "description": "Sodas, shakes"},
                        {"id": "CAT_DESSERTS", "title": "🍰 Desserts", "description": "Cakes, sundaes"}
                    ]}
                ]
            }
        }
    }
    session = await SharedSession.get_session()
    await session.post(url, json=payload, headers=headers)

# ... (other functions like send_category_items, send_qty_control, etc. from your original)
# To keep this script manageable, I'll include only essential ones. But your full file can be copied.
# For now, we'll use a placeholder. In practice, you should copy your entire whatsapp_handlers.py.

async def send_order_confirmed(sender, session_data, lang, bot_id: int):
    db = SessionLocal()
    bot = db.query(WhatsappBot).filter(WhatsappBot.id == bot_id).first()
    
    order_items = session_data.get("order", {})
    total = get_order_total(order_items)
    
    # Use bot-specific tax and fees
    tax_rate = bot.tax_rate if bot else 0.08
    tax_amount = total * tax_rate
    delivery_charge = get_delivery_fee(total, session_data.get("delivery_type"))
    grand_total = total + tax_amount + delivery_charge
    
    order_text = get_order_text(order_items)
    
    # Save order to DB (Phase 2 & 3)
    new_order = save_new_order(db, bot.owner_id if bot else 1, sender, session_data, bot)
    order_id = new_order.id # Database-backed unique ID
    
    db.close()
    
    msg = f"✅ Order confirmed! #{order_id}\n{order_text}\nTotal: ${grand_total:.2f}"
    await send_text_message(sender, msg)
    return order_id

async def send_manager_action_list(order_id, customer_number, header_text, body_text):
    # Simplified manager notification
    msg = f"{header_text}\n{body_text}\nCustomer: +{customer_number}"
    await send_text_message(MANAGER_NUMBER, msg)
