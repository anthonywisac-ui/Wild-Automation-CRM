# flow.py - Complete restaurant flow (Synchronized with D:\restaurant-bot\flow.py)
import time
import random
import re
import traceback
import asyncio
import json
from .db import (
    customer_sessions, saved_orders, customer_profiles, 
    customer_order_lookup, manager_pending, save_profile, 
    add_to_order_history, get_favorite_items,
    get_session_db, save_session_db, get_profile_db
)
from .config import MIN_DELIVERY_ORDER, MIN_PICKUP_ORDER, POST_ORDER_WINDOW, LANG_NAMES, FREE_DELIVERY_THRESHOLD, DELIVERY_CHARGE
from .strings import t
from .utils import (
    get_order_total, get_delivery_fee, get_order_text, find_item, 
    is_burger, is_pizza, has_any_side, has_any_drink, has_any_dessert,
    is_valid_name, is_valid_address, is_order_status_query, is_thanks, 
    is_bye, is_menu_request, guess_category, extract_order_number, 
    truncate_title, safe_btn
)
from .whatsapp_handlers import (
    send_text_message, send_language_selection, send_main_menu, 
    send_category_items, send_qty_control, send_cart_view, 
    send_order_summary, send_delivery_buttons, send_payment_buttons, 
    send_order_confirmed, send_quick_combo_upsell, send_quick_upsell, 
    send_dessert_upsell, send_min_order_warning, send_returning_customer_menu, 
    send_repeat_order_confirm, send_manager_action_list, send_list_message
)
from .ai_utils import get_ai_response
from .stripe_utils import create_stripe_checkout_session
from db import SessionLocal, WhatsappBot, Reservation, ChatHistory

# ========== Constants for Deal Logic ==========
DEAL_RULES = {
    "DL1": {"requires": "burger_in_cart"},
    "DL2": {"picks": ["burger"]},
    "DL3": {"picks": ["pizza"]},
    "DL4": {"picks": ["pizza", "pizza"]},
    "DL5": {"picks": ["2sides"]},
    "DL6": {"picks": []},
}
BBQ_NEEDS_SIDES = {"RB1", "RB2", "BK1", "BK2", "BK3", "BB1", "BB2", "BB4", "BB5"}
SIDE_CHOICES = {
    "MAC": "Mac & Cheese",
    "FRIES": "Fries",
    "SLAW": "Coleslaw",
    "SALAD": "Caesar Salad",
}

# ========== Dynamic Menu Loader ==========
def get_bot_menu(phone_number_id=None):
    """Fetch menu from DB config_json"""
    from .menu_data import MENU as DEFAULT_MENU
    try:
        db = SessionLocal()
        bot = None
        if phone_number_id:
            bot = db.query(WhatsappBot).filter(WhatsappBot.phone_number_id == phone_number_id).first()
        if not bot:
            bot = db.query(WhatsappBot).filter(WhatsappBot.bot_type == "restaurant").first()
        
        if bot and bot.config_json:
            config = json.loads(bot.config_json)
            if "categories" in config:
                dynamic_menu = {}
                for cat in config["categories"]:
                    cat_id = cat.get("prefix", "").lower() or cat["id"].replace("cat_", "").lower()
                    dynamic_menu[cat_id] = {
                        "name": cat["name"],
                        "items": {item["id"]: item for item in cat.get("items", [])}
                    }
                return dynamic_menu
        return DEFAULT_MENU
    except Exception as e:
        print(f"Menu Load Error: {e}")
        return DEFAULT_MENU

# ========== Session management ==========
def new_session(sender=None, bot=None):
    profile = get_profile_db(sender, bot.owner_id) if sender and bot else {}
    is_returning = bool(profile.get("name"))
    return {
        "stage": "returning" if is_returning else "lang_select",
        "lang": profile.get("lang", "en"),
        "order": {},
        "delivery_type": profile.get("delivery_type", ""),
        "address": profile.get("address", ""),
        "name": profile.get("name", ""),
        "payment": profile.get("payment", ""),
        "last_added": None,
        "current_cat": None,
        "conversation": [],
        "upsell_declined_types": set(),
        "upsell_shown_for": set(),
        "order_id": None,
        "deal_context": None,
        "post_order_at": 0,
        "just_confirmed": False,
        "just_confirmed_at": 0,
    }

def get_session(sender, bot=None):
    session = get_session_db(sender, bot.id if bot else None)
    if not session:
        session = new_session(sender, bot)
    return session

# ========== Deal and side helpers ==========
async def prompt_deal_pick(sender, session, kind, lang="en", bot=None):
    ctx = session["deal_context"]
    deal_id = ctx["deal_id"]
    MENU = get_bot_menu(bot.phone_number_id if bot else None)
    
    if kind == "burger":
        cat_key = "fastfood"
        prompt_key = "choose_burger_deal"
    elif kind == "pizza":
        already = sum(1 for p in ctx["picks"] if p.get("item_id", "").startswith("PZ"))
        cat_key = "pizza"
        if deal_id == "DL4":
            prompt_key = "choose_2nd_pizza" if already >= 1 else "choose_2pizzas"
        else:
            prompt_key = "choose_pizza_deal"
    elif kind == "2sides":
        session["stage"] = "bbq_sides"
        ctx["sides_needed"] = 2
        ctx.setdefault("sides", [])
        await prompt_bbq_sides(sender, session, lang, bot=bot)
        return
    else:
        return

    cat = MENU.get(cat_key, {"name": cat_key.title(), "items": {}})
    rows = []
    for item_id, item in cat["items"].items():
        title = truncate_title(f"{item.get('emoji','🍔')} {item['name']}", 24)
        desc = f"${item['price']:.2f} - {item.get('desc','')}"
        rows.append({"id": f"DEAL_PICK_{item_id}", "title": title, "description": desc[:72]})
    
    await send_list_message(sender, truncate_title(ctx["deal_item"]["name"], 60), t(lang, prompt_key), "Deal Builder", "Select Item", [{"title": "Options", "rows": rows}], bot=bot)

async def finalize_deal(sender, session, lang="en", bot=None):
    ctx = session["deal_context"]
    deal_id = ctx["deal_id"]
    deal_item = ctx["deal_item"]
    components = [p["name"] for p in ctx.get("picks", [])]
    if deal_id == "DL2": components += ["Fries", "Soda"]
    elif deal_id == "DL3": components += ["6 Wings"]
    elif deal_id == "DL4": components += ["2 Sodas"]
    
    order_entry = {"item": deal_item, "qty": 1, "components": components}
    key = deal_id
    n = 1
    while key in session["order"]:
        n += 1
        key = f"{deal_id}#{n}"
    session["order"][key] = order_entry
    session["last_added"] = key
    session["deal_context"] = None
    session["stage"] = "qty_control"
    await send_text_message(sender, t(lang, "deal_added"), bot=bot)
    await send_qty_control(sender, key, deal_item, session["order"], lang, bot=bot)

async def prompt_bbq_sides(sender, session, lang="en", bot=None):
    ctx = session["deal_context"]
    picked_so_far = ctx.get("sides", [])
    needed = ctx.get("sides_needed", 2)
    remaining = needed - len(picked_so_far)
    prompt_key = "pick_ribs_sides" if ctx.get("deal_id") == "DL5" else "pick_bbq_sides"
    progress = f" ({len(picked_so_far)}/{needed} picked)" if picked_so_far else ""
    
    rows = [
        {"id": "SIDE_MAC", "title": truncate_title(t(lang, "side_mac"), 24), "description": "Creamy and cheesy"},
        {"id": "SIDE_FRIES", "title": truncate_title(t(lang, "side_fries"), 24), "description": "Crispy golden"},
        {"id": "SIDE_SLAW", "title": truncate_title(t(lang, "side_slaw"), 24), "description": "Fresh crunch"},
        {"id": "SIDE_SALAD", "title": truncate_title(t(lang, "side_salad"), 24), "description": "Classic greens"},
    ]
    
    await send_list_message(sender, "🍖 Choose Your Sides", f"{t(lang, prompt_key)}{progress}", f"Pick {remaining} more", "Select Side", [{"title": "Side Options", "rows": rows}], bot=bot)

async def finalize_bbq_sides(sender, session, lang="en", bot=None):
    ctx = session["deal_context"]
    sides = ctx.get("sides", [])
    MENU = get_bot_menu(bot.phone_number_id if bot else None)
    
    if ctx.get("deal_id") == "DL5":
        deal_item = MENU["deals"]["items"]["DL5"]
        components = ["Half Rack Ribs"] + sides + ["Soda"]
        key = "DL5"
        n = 1
        while key in session["order"]:
            n += 1
            key = f"DL5#{n}"
        session["order"][key] = {"item": deal_item, "qty": 1, "components": components}
        session["last_added"] = key
        session["deal_context"] = None
        session["stage"] = "qty_control"
        await send_text_message(sender, t(lang, "deal_added"), bot=bot)
        await send_qty_control(sender, key, deal_item, session["order"], lang, bot=bot)
        return
    
    target_id = ctx.get("target_item_id")
    if target_id and target_id in session["order"]:
        session["order"][target_id]["sides"] = sides
        session["last_added"] = target_id
        session["stage"] = "qty_control"
        session["deal_context"] = None
        item = session["order"][target_id]["item"]
        await send_text_message(sender, f"✅ Sides locked in: {', '.join(sides)}", bot=bot)
        await send_qty_control(sender, target_id, item, session["order"], lang, bot=bot)

# ========== Order status helper ==========
async def handle_order_status(sender, session, lang, text, bot=None):
    order_id = extract_order_number(text) or session.get("order_id")
    if not order_id:
        await send_text_message(sender, "I don't see an active order for you. Type *menu* to order! 😊", bot=bot)
        return
    
    # In a real DB setup, we'd query the orders table. For now, simplistic response:
    await send_text_message(sender, f"Checking on order #{order_id} for you... 🔍\nOur team is preparing it now!", bot=bot)

# ========== Manager notification helpers ==========
async def notify_manager(sender, session, order_id, bot=None):
    try:
        order_text = get_order_text(session["order"])
        total = get_order_total(session["order"])
        tax_rate = bot.tax_rate if bot else 0.08
        tax = total * tax_rate
        delivery_charge = bot.delivery_fee if bot else get_delivery_fee(total, session.get("delivery_type"))
        grand_total = total + tax + delivery_charge
        
        body = (
            f"🔔 *NEW ORDER #{order_id}*\n\n"
            f"👤 {session.get('name', 'N/A')}\n"
            f"📱 +{sender}\n\n"
            f"{order_text}\n\n"
            f"Subtotal: ${total:.2f}\n"
            f"Tax: ${tax:.2f}\n"
            f"Delivery: ${delivery_charge:.2f}\n"
            f"*Total: ${grand_total:.2f}*\n\n"
            f"📍 {session.get('delivery_type','?').upper()}\n"
            f"🏠 {session.get('address','N/A')}"
        )
        await send_manager_action_list(sender, sender, f"🔔 New Order #{order_id}", body, bot=bot)
    except Exception as e:
        print(f"Manager Notification Error: {e}")

# ========== Quantity shortcut helper ==========
async def try_add_by_quantity(sender, session, text_lower, lang, bot=None):
    match = re.match(r'^(\d+)\s+(.+)$', text_lower.strip())
    if not match: return False
    qty = int(match.group(1))
    if qty < 1 or qty > 50: return False
    search_term = match.group(2).strip()
    
    MENU = get_bot_menu(bot.phone_number_id if bot else None)
    item_id, found_item = None, None
    for cat in MENU.values():
        for iid, item in cat["items"].items():
            if iid.lower() == search_term or item["name"].lower() == search_term:
                item_id, found_item = iid, item
                break
        if item_id: break
        
    if not item_id: return False
    
    if item_id in session["order"]: session["order"][item_id]["qty"] += qty
    else: session["order"][item_id] = {"item": found_item, "qty": qty}
    
    session["last_added"] = item_id
    await send_text_message(sender, f"✅ Added {qty} x {found_item['name']}", bot=bot)
    await send_cart_view(sender, session["order"], lang, bot=bot)
    session["stage"] = "menu"
    return True

# ========== Main flow handlers ==========
async def handle_flow(sender, text, is_button=False, bot=None):
    session = get_session(sender, bot)
    try:
        await _handle_flow_inner(sender, text, is_button, bot, session)
        save_session_db(sender, bot.id, session)
    except Exception as e:
        print(f"FLOW ERROR: {e}")
        traceback.print_exc()
        await send_text_message(sender, "Sorry, I encountered an error. Please type *menu* to restart.", bot=bot)

async def _handle_flow_inner(sender, text, is_button, bot, session):
    if session.get("just_confirmed"):
        if time.time() - session.get("just_confirmed_at", 0) > 2:
            session.pop("just_confirmed", None)
            session.pop("just_confirmed_at", None)

    stage = session["stage"]
    lang = session.get("lang", "en")
    text_lower = text.lower().strip()

    # Post-order handling
    if stage == "post_order":
        elapsed = time.time() - session.get("post_order_at", 0)
        if elapsed > POST_ORDER_WINDOW:
            session.update(new_session(sender, bot))
            stage = session["stage"]
        else:
            if is_order_status_query(text_lower):
                await handle_order_status(sender, session, lang, text, bot=bot)
                return
            if is_thanks(text_lower) or is_bye(text_lower):
                await send_text_message(sender, t(lang, "thanks_reply") if is_thanks(text_lower) else t(lang, "bye_reply"), bot=bot)
                return
            if is_menu_request(text_lower) or text_lower in ["hi", "hello", "hey", "start"]:
                session.update(new_session(sender, bot))
                stage = session["stage"]
            else:
                reply = await get_ai_response(sender, text, lang, session)
                await send_text_message(sender, reply, bot=bot)
                return

    if text_lower in ["restart", "reset", "start over", "clear"]:
        session.update(new_session(sender, bot))
        session["stage"] = "lang_select"
        await send_language_selection(sender, bot=bot)
        return

    # Quantity shortcut
    if not is_button and stage not in {"get_name", "address", "payment"}:
        if await try_add_by_quantity(sender, session, text_lower, lang, bot=bot):
            return

    # Returning customer
    if stage == "returning":
        name = session.get("name", "")
        favorites = get_favorite_items(sender, bot.owner_id if bot else 1)
        fav_text = f"\n\nYou usually order: {', '.join(favorites)}" if favorites else ""
        session["stage"] = "returning_choice"
        await send_returning_customer_menu(sender, name, fav_text, lang, bot=bot)
        return

    if stage == "returning_choice":
        if text == "REPEAT_ORDER":
            session["stage"] = "repeat_confirm"
            await send_repeat_order_confirm(sender, "your usual items", session.get("address",""), lang, bot=bot)
        elif text in ["NEW_ORDER", "REPEAT_ADD_MORE"]:
            session["stage"] = "menu"
            await send_main_menu(sender, session["order"], lang, bot=bot)
        elif text == "CHANGE_ADDRESS":
            session["stage"] = "address_update"
            await send_text_message(sender, "Sure! What's your new delivery address?", bot=bot)
        elif text == "REPEAT_CONFIRM":
            # Repopulate order from history logic here...
            session["stage"] = "menu"
            await send_main_menu(sender, session["order"], lang, bot=bot)
        return

    # Menu loading
    MENU = get_bot_menu(bot.phone_number_id if bot else None)

    if stage == "lang_select":
        lang_map = {"LANG_EN": "en", "LANG_AR": "ar", "LANG_HI": "hi", "LANG_FR": "fr", "LANG_DE": "de", "LANG_RU": "ru", "LANG_ZH": "zh", "LANG_ML": "ml"}
        if text in lang_map:
            session["lang"] = lang_map[text]
            lang = lang_map[text]
            session["stage"] = "menu"
            await send_text_message(sender, t(lang, "greeting_welcome"), bot=bot)
            await send_main_menu(sender, session["order"], lang, bot=bot)
        else:
            await send_language_selection(sender, bot=bot)
        return

    if text in ["SHOW_MENU", "BACK_MENU", "ADD_MORE"]:
        session["stage"] = "menu"
        await send_main_menu(sender, session["order"], lang, bot=bot)
        return

    if text == "BACK_TO_DELIVERY":
        session["stage"] = "delivery"
        await send_delivery_buttons(sender, session.get("name", ""), lang, bot=bot)
        return

    # Item management
    if text.startswith("ADD_"):
        item_id = text.replace("ADD_", "").upper()
        _cat, found_item = find_item(item_id, MENU)
        if not found_item: return

        if item_id in ["DL2", "DL3", "DL4", "DL5"]:
            rule = DEAL_RULES[item_id]
            session["stage"] = "deal_build"
            session["deal_context"] = {"deal_id": item_id, "deal_item": found_item, "needs": list(rule.get("picks", [])), "picks": []}
            if rule.get("picks"): await prompt_deal_pick(sender, session, rule["picks"][0], lang, bot=bot)
            else: await finalize_deal(sender, session, lang, bot=bot)
            return

        if item_id in BBQ_NEEDS_SIDES:
            session["order"][item_id] = {"item": found_item, "qty": 1, "sides": []}
            session["last_added"] = item_id
            session["stage"] = "bbq_sides"
            session["deal_context"] = {"target_item_id": item_id, "sides_needed": 2, "sides": []}
            await prompt_bbq_sides(sender, session, lang, bot=bot)
            return

        if item_id in session["order"]: session["order"][item_id]["qty"] += 1
        else: session["order"][item_id] = {"item": found_item, "qty": 1}
        session["last_added"] = item_id
        session["stage"] = "qty_control"
        await send_qty_control(sender, item_id, found_item, session["order"], lang, bot=bot)
        return

    if stage == "deal_build" and text.startswith("DEAL_PICK_"):
        picked_id = text.replace("DEAL_PICK_", "").upper()
        _cat, picked_item = find_item(picked_id, MENU)
        if picked_item:
            session["deal_context"]["picks"].append({"item_id": picked_id, "name": picked_item["name"]})
            needs = session["deal_context"]["needs"]
            if len(session["deal_context"]["picks"]) >= len(needs): await finalize_deal(sender, session, lang, bot=bot)
            else: await prompt_deal_pick(sender, session, needs[len(session["deal_context"]["picks"])], lang, bot=bot)
        return

    if stage == "bbq_sides" and text.startswith("SIDE_"):
        side_key = text.replace("SIDE_", "")
        if side_key in SIDE_CHOICES:
            session["deal_context"].setdefault("sides", []).append(SIDE_CHOICES[side_key])
            if len(session["deal_context"]["sides"]) >= session["deal_context"].get("sides_needed", 2): await finalize_bbq_sides(sender, session, lang, bot=bot)
            else: await prompt_bbq_sides(sender, session, lang, bot=bot)
        return

    if text == "CHECKOUT":
        if session["order"]:
            session["stage"] = "confirm"
            await send_order_summary(sender, session["order"], lang, bot=bot)
        else:
            await send_text_message(sender, t(lang, "cart_empty"), bot=bot)
            await send_main_menu(sender, session["order"], lang, bot=bot)
        return

    if text == "VIEW_CART":
        await send_cart_view(sender, session["order"], lang, bot=bot)
        return

    if text == "CONFIRM_ORDER":
        if session.get("name"):
            session["stage"] = "delivery"
            await send_delivery_buttons(sender, session["name"], lang, bot=bot)
        else:
            session["stage"] = "get_name"
            await send_text_message(sender, t(lang, "name_ask"), bot=bot)
        return

    if text in ["DELIVERY", "PICKUP"]:
        session["delivery_type"] = text.lower()
        if text == "DELIVERY":
            if session.get("address"):
                session["stage"] = "payment"
                await send_payment_buttons(sender, session.get("name", ""), lang, bot=bot)
            else:
                session["stage"] = "address"
                await send_text_message(sender, t(lang, "address_ask"), bot=bot)
        else:
            session["stage"] = "payment"
            await send_payment_buttons(sender, session.get("name", ""), lang, bot=bot)
        return

    if text in ["CASH", "CARD_STRIPE", "APPLE_PAY"]:
        payment_map = {"CASH": t(lang, "cash"), "CARD_STRIPE": t(lang, "card"), "APPLE_PAY": t(lang, "apple_pay")}
        session["payment"] = payment_map[text]
        
        if text == "CARD_STRIPE":
            total = get_order_total(session["order"])
            grand_total = total * 1.08 + get_delivery_fee(total, session.get("delivery_type"))
            order_id = str(int(time.time()))
            payment_url = await create_stripe_checkout_session(order_id, grand_total)
            if payment_url: await send_text_message(sender, f"💳 Pay here:\n{payment_url}", bot=bot)
            else: await send_text_message(sender, "❌ Payment link failed. Try cash/Apple Pay.", bot=bot)
            return

        order_id = await send_order_confirmed(sender, session, lang, bot=bot)
        session.update({"order_id": order_id, "just_confirmed": True, "just_confirmed_at": time.time(), "stage": "post_order", "post_order_at": time.time(), "order": {}})
        await notify_manager(sender, session, order_id, bot=bot)
        return

    if stage == "get_name":
        if is_valid_name(text):
            session["name"] = text.strip().title()[:30]
            session["stage"] = "delivery"
            await send_delivery_buttons(sender, session["name"], lang, bot=bot)
        else: await send_text_message(sender, t(lang, "invalid_name"), bot=bot)
        return

    if stage == "address":
        if is_valid_address(text):
            session["address"] = text.strip()
            session["stage"] = "payment"
            await send_payment_buttons(sender, session.get("name", ""), lang, bot=bot)
        else: await send_text_message(sender, t(lang, "invalid_address"), bot=bot)
        return

    # Greetings / Fallback
    if text_lower in ["hi", "hello", "hey", "start", "salam", "hola"]:
        if stage == "lang_select": await send_language_selection(sender, bot=bot)
        else:
            session["stage"] = "menu"
            await send_text_message(sender, t(lang, "greeting_welcome"), bot=bot)
            await send_main_menu(sender, session["order"], lang, bot=bot)
        return

    cat_guess = guess_category(text_lower)
    if cat_guess and stage not in {"get_name", "address", "payment", "delivery", "confirm"}:
        session["stage"] = "items"
        session["current_cat"] = cat_guess
        await send_category_items(sender, cat_guess, session["order"], lang, bot=bot)
        return

    reply = await get_ai_response(sender, text, lang, session)
    await send_text_message(sender, reply, bot=bot)
