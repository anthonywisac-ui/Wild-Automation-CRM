# whatsapp_handlers.py - Multi-tenant WhatsApp message handlers
import aiohttp
import random
import time
from config import WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID, MANAGER_NUMBER
from session import SharedSession
from utils import truncate_title, safe_btn, get_order_total, get_order_text, get_delivery_fee
from .strings import t

API_VERSION = "v19.0"

async def _send_request(payload, bot=None):
    token = bot.meta_token if bot and bot.meta_token else WHATSAPP_TOKEN
    phone_id = bot.phone_number_id if bot and bot.phone_number_id else WHATSAPP_PHONE_NUMBER_ID
    url = f"https://graph.facebook.com/{API_VERSION}/{phone_id}/messages"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        session = await SharedSession.get_session()
        async with session.post(url, json=payload, headers=headers) as r:
            if r.status >= 400:
                print(f"WhatsApp API Error {r.status}: {await r.text()}")
            return r
    except Exception as e:
        print(f"WhatsApp Request Exception: {e}")
        return None

async def send_text_message(to, message, bot=None):
    payload = {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": message}}
    await _send_request(payload, bot)

async def send_language_selection(sender, bot=None):
    payload = {
        "messaging_product": "whatsapp",
        "to": sender,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "header": {"type": "text", "text": "🍽️ Wild Bites Restaurant"},
            "body": {"text": "Welcome! Please choose your language:\n\nمرحباً | स्वागत | Bienvenue | Willkommen"},
            "footer": {"text": "Language Selection"},
            "action": {
                "button": "🌐 Choose Language",
                "sections": [{
                    "title": "Languages",
                    "rows": [
                        {"id": "LANG_EN", "title": "🇺🇸 English", "description": "Continue in English"},
                        {"id": "LANG_AR", "title": "🇸🇦 العربية", "description": "الاستمرار بالعربية"},
                        {"id": "LANG_HI", "title": "🇮🇳 हिन्दी", "description": "हिंदी में जारी रखें"},
                        {"id": "LANG_FR", "title": "🇫🇷 Français", "description": "Continuer en français"},
                        {"id": "LANG_DE", "title": "🇩🇪 Deutsch", "description": "Auf Deutsch fortfahren"},
                        {"id": "LANG_RU", "title": "🇷🇺 Русский", "description": "Продолжить на русском"},
                        {"id": "LANG_ZH", "title": "🇨🇳 中文", "description": "继续中文"},
                        {"id": "LANG_ML", "title": "🇮🇳 Malayalam", "description": "മലയാളം"}
                    ]
                }]
            }
        }
    }
    await _send_request(payload, bot)

async def send_main_menu(sender, current_order, lang, bot=None):
    total = get_order_total(current_order)
    cart_text = f"\n\n🛒 ${total:.2f}" if current_order else ""
    payload = {
        "messaging_product": "whatsapp",
        "to": sender,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "header": {"type": "text", "text": "🍽️ Wild Bites Restaurant"},
            "body": {"text": f"{t(lang, 'menu_header')}\n{t(lang, 'craving')}{cart_text}"},
            "footer": {"text": "Fast Delivery | Fresh Food | Best Value"},
            "action": {
                "button": t(lang, "browse"),
                "sections": [
                    {"title": "Start Here", "rows": [{"id": "CAT_DEALS", "title": "Deals (Best Value)", "description": "Combo meals & bundles"}]},
                    {"title": "Main Course", "rows": [
                        {"id": "CAT_FASTFOOD", "title": "Burgers & Fast Food", "description": "Smash, chicken, BBQ bacon"},
                        {"id": "CAT_PIZZA", "title": "Pizza (12 inch)", "description": "Margherita, BBQ, Meat Lovers"},
                        {"id": "CAT_BBQ", "title": "BBQ", "description": "Ribs, brisket, pulled pork"},
                        {"id": "CAT_FISH", "title": "Fish & Seafood", "description": "Cod, salmon, shrimp"}
                    ]},
                    {"title": "Extras", "rows": [
                        {"id": "CAT_SIDES", "title": "Sides & Snacks", "description": "Fries, wings, nachos"},
                        {"id": "CAT_DRINKS", "title": "Drinks & Shakes", "description": "Sodas, shakes, juices"},
                        {"id": "CAT_DESSERTS", "title": "Desserts", "description": "Cake, cheesecake, sundae"}
                    ]}
                ]
            }
        }
    }
    await _send_request(payload, bot)

async def send_category_items(sender, cat_key, current_order, lang, bot=None):
    from .flow import get_bot_menu
    MENU = get_bot_menu(bot.phone_number_id if bot else None)
    cat = MENU.get(cat_key, {"name": cat_key.title(), "items": {}})
    total = get_order_total(current_order)
    cart_text = f"\n\n🛒 ${total:.2f}" if current_order else ""
    rows = []
    for item_id, item in cat["items"].items():
        in_cart = current_order.get(item_id, {}).get("qty", 0)
        title = truncate_title(f"{item.get('emoji','🍔')} {item['name']}", 24)
        desc_prefix = f"✓ x{in_cart} · " if in_cart else ""
        desc_text = f"{desc_prefix}${item['price']:.2f} - {item.get('desc','')}"
        rows.append({"id": f"ADD_{item_id}", "title": title, "description": desc_text[:72]})
    
    payload = {
        "messaging_product": "whatsapp", "to": sender, "type": "interactive",
        "interactive": {
            "type": "list",
            "header": {"type": "text", "text": truncate_title(cat["name"], 60)},
            "body": {"text": f"{cat['name']}\n{t(lang, 'tap_add')}{cart_text}"},
            "footer": {"text": "Tap to add to cart"},
            "action": {"button": "Select Item", "sections": [{"title": truncate_title(cat["name"], 24), "rows": rows}]}
        }
    }
    await _send_request(payload, bot)

async def send_list_message(to, header, body, footer, button_text, sections, bot=None):
    payload = {
        "messaging_product": "whatsapp", "to": to, "type": "interactive",
        "interactive": {
            "type": "list",
            "header": {"type": "text", "text": truncate_title(header, 60)},
            "body": {"text": body},
            "footer": {"text": truncate_title(footer, 60)},
            "action": {"button": button_text, "sections": sections}
        }
    }
    await _send_request(payload, bot)

async def send_qty_control(sender, item_id, item, order, lang, bot=None):
    qty = order.get(item_id, {}).get("qty", 1)
    subtotal = item["price"] * qty
    total = get_order_total(order)
    order_text = get_order_text(order)
    body_text = f"*{item['name']}*\nQty: {qty} x ${item['price']:.2f} = *${subtotal:.2f}*\n\n{t(lang, 'your_order')}\n{order_text}\n\n{t(lang, 'total')} ${total:.2f}*"
    
    payload = {
        "messaging_product": "whatsapp", "to": sender, "type": "interactive",
        "interactive": {
            "type": "button",
            "header": {"type": "text", "text": truncate_title(f"{item.get('emoji','🍔')} {item['name']}", 60)},
            "body": {"text": body_text[:1000]},
            "footer": {"text": "Tap Checkout to complete"},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": "QTY_MINUS", "title": safe_btn(t(lang, "remove_one"))}},
                    {"type": "reply", "reply": {"id": "ADD_MORE", "title": safe_btn(t(lang, "add_more"))}},
                    {"type": "reply", "reply": {"id": "CHECKOUT", "title": safe_btn(f"{t(lang, 'checkout')} ${total:.2f}")}}
                ]
            }
        }
    }
    await _send_request(payload, bot)

async def send_quick_combo_upsell(sender, lang, bot=None):
    payload = {
        "messaging_product": "whatsapp", "to": sender, "type": "interactive",
        "interactive": {
            "type": "button",
            "header": {"type": "text", "text": "Make it a Combo?"},
            "body": {"text": "Add Fries + Soda for only *$4.99 more!*\n\nMost customers add this! 😍"},
            "footer": {"text": "Best value"},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": "ADD_COMBO_DL1", "title": safe_btn(t(lang, "yes_combo"))}},
                    {"type": "reply", "reply": {"id": "SKIP_UPSELL", "title": safe_btn(t(lang, "no_combo"))}}
                ]
            }
        }
    }
    await _send_request(payload, bot)

async def send_quick_upsell(sender, item_id, message, lang, upsell_type="generic", bot=None):
    payload = {
        "messaging_product": "whatsapp", "to": sender, "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": message},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": f"ADD_{item_id}", "title": safe_btn(t(lang, "yes_combo"))}},
                    {"type": "reply", "reply": {"id": "SKIP_UPSELL", "title": safe_btn(t(lang, "no_combo"))}}
                ]
            }
        }
    }
    await _send_request(payload, bot)

async def send_cart_view(sender, order, lang, bot=None):
    if not order:
        await send_text_message(sender, t(lang, "cart_empty"), bot)
        return
    total = get_order_total(order)
    order_text = get_order_text(order)
    payload = {
        "messaging_product": "whatsapp", "to": sender, "type": "interactive",
        "interactive": {
            "type": "button",
            "header": {"type": "text", "text": "🛒 Your Cart"},
            "body": {"text": f"{order_text}\n\n{t(lang, 'subtotal')} ${total:.2f}"},
            "footer": {"text": "Wild Bites Restaurant"},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": "CHECKOUT", "title": safe_btn(f"{t(lang, 'checkout')} ${total:.2f}")}},
                    {"type": "reply", "reply": {"id": "ADD_MORE", "title": safe_btn(t(lang, "add_more"))}},
                    {"type": "reply", "reply": {"id": "CANCEL_ORDER", "title": safe_btn(t(lang, "cancel"))}}
                ]
            }
        }
    }
    await _send_request(payload, bot)

async def send_order_summary(sender, order, lang, bot=None):
    total = get_order_total(order)
    tax = total * 0.08
    delivery_note = "\n" + (t(lang, "delivery_note_free") if total >= 50.0 else t(lang, "delivery_note_will_add"))
    grand_total = total + tax
    order_text = get_order_text(order)
    body_text = f"{order_text}\n\n{t(lang, 'subtotal')} ${total:.2f}\n{t(lang, 'tax')} ${tax:.2f}\n{t(lang, 'grand_total')} ${grand_total:.2f}*{delivery_note}"
    
    payload = {
        "messaging_product": "whatsapp", "to": sender, "type": "interactive",
        "interactive": {
            "type": "button",
            "header": {"type": "text", "text": "📋 Order Summary"},
            "body": {"text": body_text[:1000]},
            "footer": {"text": "Wild Bites Restaurant"},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": "CONFIRM_ORDER", "title": safe_btn(t(lang, "confirm"))}},
                    {"type": "reply", "reply": {"id": "ADD_MORE", "title": safe_btn(t(lang, "add_more"))}},
                    {"type": "reply", "reply": {"id": "CANCEL_ORDER", "title": safe_btn(t(lang, "cancel"))}}
                ]
            }
        }
    }
    await _send_request(payload, bot)

async def send_delivery_buttons(sender, name, lang, bot=None):
    from .flow import get_session
    session = get_session(sender, bot)
    table_num = session.get("table_number")
    if table_num:
        body_text = f"Hey {name}! You're at Table {table_num} 🍽️\n\nReady to order?"
        buttons = [{"type": "reply", "reply": {"id": "DINE_IN", "title": safe_btn(t(lang, "dine_in"))}}, {"type": "reply", "reply": {"id": "PICKUP", "title": safe_btn("Takeaway")}}]
    else:
        body_text = f"Hey {name}! Delivery or Pickup?\n\n{t(lang, 'delivery_info')}"
        buttons = [{"type": "reply", "reply": {"id": "DELIVERY", "title": safe_btn(t(lang, "delivery"))}}, {"type": "reply", "reply": {"id": "PICKUP", "title": safe_btn(t(lang, "pickup"))}}]
    
    payload = {
        "messaging_product": "whatsapp", "to": sender, "type": "interactive",
        "interactive": {
            "type": "button",
            "header": {"type": "text", "text": "🚚 How to get your food?"},
            "body": {"text": body_text},
            "footer": {"text": "Wild Bites Restaurant"},
            "action": {"buttons": buttons}
        }
    }
    await _send_request(payload, bot)

async def send_payment_buttons(sender, name, lang, bot=None):
    payload = {
        "messaging_product": "whatsapp", "to": sender, "type": "interactive",
        "interactive": {
            "type": "button",
            "header": {"type": "text", "text": "Payment Method"},
            "body": {"text": "Choose your payment:"},
            "footer": {"text": "100% Secure"},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": "CASH", "title": safe_btn(t(lang, "cash"))}},
                    {"type": "reply", "reply": {"id": "CARD_STRIPE", "title": safe_btn(t(lang, "card"))}},
                    {"type": "reply", "reply": {"id": "APPLE_PAY", "title": safe_btn(t(lang, "apple_pay"))}}
                ]
            }
        }
    }
    await _send_request(payload, bot)

async def send_order_confirmed(sender, session_data, lang, bot=None):
    order = session_data.get("order", {})
    total = get_order_total(order)
    tax = total * 0.08
    delivery_charge = get_delivery_fee(total, session_data.get("delivery_type"))
    grand_total = total + tax + delivery_charge
    order_text = get_order_text(order)
    delivery_type = session_data.get("delivery_type", "pickup")
    order_id = int(time.time()) % 100000
    
    if delivery_type == "dine_in": location_text = f"🍽️ Table {session_data.get('table_number', '?')}" ; eta = "10-15 minutes"
    else: eta = "30-45 mins" if delivery_type == "delivery" else "15-20 mins" ; location_text = f"{'Delivery: ' + session_data.get('address', '') if delivery_type == 'delivery' else 'Store Pickup'}"
    
    msg = f"""{t(lang, 'order_confirmed')}, {session_data.get('name', 'Customer')}! #{order_id}*
{order_text}
{t(lang, 'subtotal')} ${total:.2f}
{t(lang, 'tax')} ${tax:.2f}
{t(lang, 'delivery_charge')} ${delivery_charge:.2f}
{t(lang, 'grand_total')} ${grand_total:.2f}*
{location_text}
Payment: {session_data.get('payment', '')}
{t(lang, 'ready_in')} *{eta}*
{t(lang, 'thank_you')}"""
    await send_text_message(sender, msg, bot)
    return order_id

async def send_returning_customer_menu(sender, name, fav_text, lang, bot=None):
    payload = {
        "messaging_product": "whatsapp", "to": sender, "type": "interactive",
        "interactive": {
            "type": "button",
            "header": {"type": "text", "text": "🍽️ Welcome Back!"},
            "body": {"text": f"Welcome back, {name}!{fav_text}\n\nWhat would you like to do today?"},
            "footer": {"text": "Wild Bites Restaurant"},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": "REPEAT_ORDER", "title": safe_btn("Repeat Last Order")}},
                    {"type": "reply", "reply": {"id": "NEW_ORDER", "title": safe_btn("New Order")}},
                    {"type": "reply", "reply": {"id": "NEW_RESERVATION", "title": safe_btn("Book a Table")}}
                ]
            }
        }
    }
    await _send_request(payload, bot)

async def send_repeat_order_confirm(sender, last_items, address, lang, bot=None):
    addr_text = f"\nDelivery to: {address}" if address else "\nPickup from store"
    payload = {
        "messaging_product": "whatsapp", "to": sender, "type": "interactive",
        "interactive": {
            "type": "button",
            "header": {"type": "text", "text": "Repeat Last Order?"},
            "body": {"text": f"Your last order was:\n{last_items}{addr_text}\n\nWant the same again?"},
            "footer": {"text": "Wild Bites Restaurant"},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": "REPEAT_CONFIRM", "title": safe_btn("Yes, Same Order!")}},
                    {"type": "reply", "reply": {"id": "REPEAT_ADD_MORE", "title": safe_btn("Add More Items")}},
                    {"type": "reply", "reply": {"id": "NEW_ORDER", "title": safe_btn("Start Fresh")}}
                ]
            }
        }
    }
    await _send_request(payload, bot)

async def send_manager_action_list(order_id, customer_number, header_text, body_text, footer_text="Tap action to update customer", bot=None):
    # This one is special as it goes to MANAGER_NUMBER usually, but for QA/Builder we might override
    to = MANAGER_NUMBER
    rows = [
        {"id": f"MGR_{order_id}_READY", "title": "✅ Ready", "description": "Food is ready"},
        {"id": f"MGR_{order_id}_OUTFORDELIVERY", "title": "🚚 Out for Delivery", "description": "Driver on the way"},
        {"id": f"MGR_{order_id}_CANCELLED", "title": "❌ Cancelled", "description": "Cancel this order"}
    ]
    payload = {
        "messaging_product": "whatsapp", "to": to, "type": "interactive",
        "interactive": {
            "type": "list",
            "header": {"type": "text", "text": truncate_title(header_text, 60)},
            "body": {"text": body_text},
            "footer": {"text": truncate_title(footer_text, 60)},
            "action": {"button": "Update Status", "sections": [{"title": f"Order #{order_id}", "rows": rows}]}
        }
    }
    await _send_request(payload, bot)

async def send_whatsapp_to_number(to_number, message, bot=None):
    await send_text_message(to_number, message, bot)