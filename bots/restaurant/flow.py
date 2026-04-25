# flow.py - Complete restaurant flow (Multi-tenant + All features)
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
    get_session_db, save_session_db, get_profile_db,
    get_bot_menu, new_session, get_session,
    save_profile_async, add_to_order_history_async
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

# ========== Upsell Config Helper ==========
def get_upsell_config(bot):
    """Read upsell_rules from bot config_json. Defaults all true if not set."""
    defaults = {
        "burger_combo": True,
        "pizza_wings": True,
        "desserts": True,
    }
    if not bot:
        return defaults
    try:
        cfg = json.loads(bot.config_json or "{}")
        rules = cfg.get("upsell_rules", {})
        return {**defaults, **rules}
    except Exception:
        return defaults


# ========== Constants for Deal Logic ==========
# Dynamic Deal Rules Helper
def get_deal_rules(bot):
    """
    Returns deal rules. 
    Can be overridden in bot.config_json under 'deal_rules' key.
    Format: {"DL1": {"requires": "burger_in_cart"}, "DL2": {"picks": ["burger"]}}
    """
    defaults = {
        "DL1": {"requires": "burger_in_cart"},
        "DL2": {"picks": ["burger"]},
        "DL3": {"picks": ["pizza"]},
        "DL4": {"picks": ["burger"]},  # Updated to Burger choice as requested
        "DL5": {"picks": ["2sides"]},
        "DL6": {"picks": []},
    }
    if not bot: return defaults
    try:
        cfg = json.loads(bot.config_json or "{}")
        custom_rules = cfg.get("deal_rules", {})
        return {**defaults, **custom_rules}
    except Exception:
        return defaults
BBQ_NEEDS_SIDES = {"RB1", "RB2", "BK1", "BK2", "BK3", "BB1", "BB2", "BB4", "BB5"}
SIDE_CHOICES = {
    "MAC": "Mac & Cheese",
    "FRIES": "Fries",
    "SLAW": "Coleslaw",
    "SALAD": "Caesar Salad",
}

CAT_MAP = {
    "CAT_DEALS": "deals", "CAT_FASTFOOD": "fastfood", "CAT_PIZZA": "pizza",
    "CAT_BBQ": "bbq", "CAT_FISH": "fish", "CAT_SIDES": "sides",
    "CAT_DRINKS": "drinks", "CAT_DESSERTS": "desserts",
}

ORDERING_STAGES = {
    "items", "qty_control", "upsell_check", "upsell_combo", "confirm",
    "get_name", "address", "delivery", "payment", "deal_build",
    "bbq_sides", "repeat_confirm",
}


# ========== Deal and side helpers ==========
async def prompt_deal_pick(sender, session, kind, lang="en", bot=None):
    ctx = session["deal_context"]
    deal_id = ctx["deal_id"]
    MENU = get_bot_menu(bot.phone_number_id if bot else None, db_session=db_session)

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
        title = truncate_title(f"{item.get('emoji', '🍔')} {item['name']}", 24)
        desc = f"${item['price']:.2f} - {item.get('desc', '')}"
        rows.append({"id": f"DEAL_PICK_{item_id}", "title": title, "description": desc[:72]})

    await send_list_message(
        sender, truncate_title(ctx["deal_item"]["name"], 60),
        t(lang, prompt_key), "Deal Builder", "Select Item",
        [{"title": "Options", "rows": rows}], bot=bot
    )


async def finalize_deal(sender, session, lang="en", bot=None):
    ctx = session["deal_context"]
    deal_id = ctx["deal_id"]
    deal_item = ctx["deal_item"]
    components = [p["name"] for p in ctx.get("picks", [])]
    if deal_id == "DL2":
        components += ["Fries", "Soda"]
    elif deal_id == "DL3":
        components += ["6 Wings"]
    elif deal_id == "DL4":
        components += ["2 Sodas"]

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

    await send_list_message(
        sender, "🍖 Choose Your Sides",
        f"{t(lang, prompt_key)}{progress}", f"Pick {remaining} more",
        "Select Side", [{"title": "Side Options", "rows": rows}], bot=bot
    )


async def finalize_bbq_sides(sender, session, lang="en", bot=None):
    ctx = session["deal_context"]
    sides = ctx.get("sides", [])
    MENU = get_bot_menu(bot.phone_number_id if bot else None, db_session=db_session)

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
    else:
        session["deal_context"] = None
        session["stage"] = "menu"
        await send_text_message(sender, "✅ Sides saved! Here's your menu.", bot=bot)
        await send_main_menu(sender, session["order"], lang, bot=bot, db_session=db_session)


# ========== Order status helpers ==========
async def handle_order_status(sender, session, lang, text, bot=None):
    order_id = extract_order_number(text) or session.get("order_id")
    if not order_id:
        orders_list = customer_order_lookup.get(sender, [])
        if orders_list:
            order_id = orders_list[-1]
    if not order_id:
        await send_text_message(sender, "I don't see an active order for you. Type *menu* to order! 😊", bot=bot)
        return

    order_data = saved_orders.get(order_id)
    customer_name = (order_data or {}).get("customer_name", "")
    greet = f"Hi {customer_name}! " if customer_name else ""

    if not order_data:
        await send_text_message(
            sender,
            f"{greet}Let me check on order #{order_id} with our team right away! 🔍\n\n"
            f"I'll get back to you in a moment. Thank you for your patience! 🙏",
            bot=bot
        )
        await notify_manager_status(order_id, sender, bot=bot, reason="Data missing")
        return

    elapsed_min = (time.time() - order_data["timestamp"]) / 60
    delivery_type = order_data.get("delivery_type", "pickup")
    expected_max = 45 if delivery_type == "delivery" else 20
    expected_min = 30 if delivery_type == "delivery" else 15
    elapsed_int = int(elapsed_min)

    if elapsed_min < expected_min:
        remaining = expected_min - elapsed_int
        msg = (
            f"{greet}Your order #{order_id} is being prepared! 🍳\n\n"
            f"⏱️ *Expected in about {remaining}-{expected_max - elapsed_int} more minutes*\n\n"
            f"Our kitchen is working on it right now. Thanks for your patience! 😊"
        )
        await send_text_message(sender, msg, bot=bot)
        return

    if elapsed_min < expected_max:
        remaining = expected_max - elapsed_int
        if delivery_type == "delivery":
            msg = (
                f"{greet}Your order #{order_id} should be arriving any moment now! 🚚\n\n"
                f"⏱️ *Around {max(1, remaining)} more minutes* to reach you.\n\n"
                f"If it doesn't arrive soon, I'll check with the driver. Almost there! 😊"
            )
        else:
            msg = (
                f"{greet}Your order #{order_id} should be ready any moment! 🏪\n\n"
                f"⏱️ *Around {max(1, remaining)} more minutes*.\n\n"
                f"Feel free to head over — we'll have it hot and ready! 😊"
            )
        await send_text_message(sender, msg, bot=bot)
        return

    delay = elapsed_int - expected_max
    if delivery_type == "delivery":
        msg = (
            f"{greet}I'm really sorry your order #{order_id} hasn't arrived yet! 🙏\n\n"
            f"⏱️ It's been *{elapsed_int} minutes* — about {delay} mins longer than expected.\n\n"
            f"I'm reaching out to our team right now to check on the driver. "
            f"You'll have an update in the next few minutes. Thank you for your patience! 💚"
        )
    else:
        msg = (
            f"{greet}Sorry for the wait on order #{order_id}! 🙏\n\n"
            f"⏱️ It's been *{elapsed_int} minutes* — about {delay} mins longer than expected.\n\n"
            f"Let me check with the kitchen right now. I'll update you shortly! 💚"
        )
    await send_text_message(sender, msg, bot=bot)
    await notify_manager_status(order_id, sender, bot=bot, reason=f"OVERDUE by {delay} mins — customer waiting")


# ========== Manager notification helpers ==========
async def notify_manager(sender, session, order_id, bot=None):
    try:
        order = session.get("order", {})
        total = get_order_total(order)
        tax_rate = bot.tax_rate if bot else 0.08
        tax = total * tax_rate
        delivery_charge = get_delivery_fee(total, session.get("delivery_type"))
        grand_total = total + tax + delivery_charge
        order_text = get_order_text(order)
        lang_name = LANG_NAMES.get(session.get("lang", "en"), "English")

        delivery_type = session.get("delivery_type", "pickup")
        if delivery_type == "delivery":
            location_line = f"📍 Delivery: {session.get('address', '')}"
        elif delivery_type == "dine_in":
            location_line = f"🍽️ Dine-in Table {session.get('table_number', '?')}"
        else:
            location_line = "🏪 Pickup"

        eta_line = "30-45 mins" if delivery_type == "delivery" else "15-20 mins"

        body = (
            f"🔔 *NEW ORDER #{order_id}*\n\n"
            f"👤 {session.get('name', 'N/A')}\n"
            f"📱 +{sender}\n"
            f"🌐 {lang_name}\n\n"
            f"{order_text}\n\n"
            f"Subtotal: ${total:.2f}\n"
            f"Tax: ${tax:.2f}\n"
            f"Delivery: ${delivery_charge:.2f}\n"
            f"*Total: ${grand_total:.2f}*\n\n"
            f"{location_line}\n"
            f"💳 {session.get('payment', 'N/A')}\n"
            f"⏱️ ETA: {eta_line}"
        )
        await send_manager_action_list(order_id, sender, f"🔔 New Order #{order_id}", body, bot=bot)
        print(f"Manager notified: #{order_id}")
    except Exception as e:
        print(f"Manager Notification Error: {e}")


async def notify_manager_status(order_id, customer_number, bot=None, reason="Customer inquiry"):
    try:
        order_data = saved_orders.get(order_id, {})
        customer_name = order_data.get("customer_name", "Customer")
        delivery_type = order_data.get("delivery_type", "pickup")
        address = order_data.get("address", "")
        elapsed_min = 0
        if order_data.get("timestamp"):
            elapsed_min = int((time.time() - order_data["timestamp"]) / 60)
        location_line = f"📍 {address}" if address and delivery_type == "delivery" else "🏪 Pickup"
        body_text = (
            f"⚠️ *CUSTOMER WAITING — #{order_id}*\n\n"
            f"👤 {customer_name}\n"
            f"📱 +{customer_number}\n"
            f"⏱️ Placed *{elapsed_min} min ago*\n"
            f"🚚 {delivery_type.title()}\n"
            f"{location_line}\n\n"
            f"📢 {reason}"
        )
        await send_manager_action_list(
            order_id, customer_number,
            f"⚠️ Waiting — #{order_id}", body_text,
            bot=bot
        )
    except Exception as e:
        print(f"notify_manager_status Error: {e}")


# ========== Quantity shortcut helper ==========
async def try_add_by_quantity(sender, session, text_lower, lang, bot=None):
    match = re.match(r'^(\d+)\s+(.+)$', text_lower.strip())
    if not match:
        return False
    qty = int(match.group(1))
    if qty < 1 or qty > 50:
        return False
    search_term = match.group(2).strip()

    MENU = get_bot_menu(bot.phone_number_id if bot else None, db_session=db_session)
    item_id, found_item = None, None
    for cat in MENU.values():
        for iid, item in cat.get("items", {}).items():
            if iid.lower() == search_term or item["name"].lower() == search_term:
                item_id, found_item = iid, item
                break
        if item_id:
            break

    if not item_id:
        return False

    if item_id in session["order"]:
        session["order"][item_id]["qty"] += qty
    else:
        session["order"][item_id] = {"item": found_item, "qty": qty}

    session["last_added"] = item_id
    await send_text_message(sender, f"✅ Added {qty} x {found_item['name']}", bot=bot)
    await send_cart_view(sender, session["order"], lang, bot=bot)
    session["stage"] = "menu"
    return True


# ========== Main flow handlers ==========
async def handle_flow(sender, text, is_button=False, bot=None, db_session=None):
    session = get_session(sender, bot, db_session=db_session)
    try:
        await _handle_flow_inner(sender, text, is_button, bot, session, db_session=db_session)
        save_session_db(sender, bot.id if bot else None, session, db_session=db_session)
    except Exception as e:
        print(f"FLOW ERROR: {e}")
        traceback.print_exc()
        await send_text_message(sender, "Sorry, I encountered an error. Please type *menu* to restart.", bot=bot)


async def _handle_flow_inner(sender, text, is_button, bot, session, db_session=None):
    # Clear just_confirmed flag after 2 seconds
    if session.get("just_confirmed"):
        if time.time() - session.get("just_confirmed_at", 0) > 2:
            session.pop("just_confirmed", None)
            session.pop("just_confirmed_at", None)

    # Safety: stuck upsell_combo with no pending upsell → reset to menu
    if session.get("stage") == "upsell_combo" and not session.get("_pending_upsell_type"):
        session["stage"] = "menu"

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

    # Early order status check for non-ordering stages
    if is_order_status_query(text_lower) and stage not in ORDERING_STAGES:
        await handle_order_status(sender, session, lang, text, bot=bot)
        return

    # Quantity shortcut (e.g. "4 FF1" or "3 Classic Burger")
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
            profile = get_profile_db(sender, bot.owner_id if bot else 1)
            history = profile.get("order_history", [])
            if history:
                last = history[-1]
                last_items_raw = last.get("items", [])
                names = []
                for it in last_items_raw:
                    if isinstance(it, dict):
                        names.append(f"{it['name']} x{it.get('qty', 1)}")
                    else:
                        names.append(str(it))
                last_items = ", ".join(names)
                addr = session.get("address", "")
                session["stage"] = "repeat_confirm"
                await send_repeat_order_confirm(sender, last_items, addr, lang, bot=bot)
            else:
                session["stage"] = "menu"
                MENU = get_bot_menu(bot.phone_number_id if bot else None, db_session=db_session)
                await send_main_menu(sender, session["order"], lang, bot=bot, db_session=db_session)
        elif text in ["NEW_ORDER", "REPEAT_ADD_MORE"]:
            session["stage"] = "menu"
            await send_main_menu(sender, session["order"], lang, bot=bot, db_session=db_session)
        elif text == "CHANGE_ADDRESS":
            session["stage"] = "address_update"
            await send_text_message(sender, "Sure! What's your new delivery address?", bot=bot)
        elif text == "REPEAT_CONFIRM":
            profile = get_profile_db(sender, bot.owner_id if bot else 1)
            history = profile.get("order_history", [])
            MENU = get_bot_menu(bot.phone_number_id if bot else None, db_session=db_session)
            if history:
                last_items = history[-1].get("items", [])
                session["order"] = {}
                for it in last_items:
                    if isinstance(it, dict):
                        iid = it.get("item_id")
                        qty = it.get("qty", 1)
                        if iid:
                            _cat, item = find_item(iid, MENU)
                            if item:
                                session["order"][iid] = {"item": item, "qty": qty}
                    else:
                        for cat_data in MENU.values():
                            for item_id, item in cat_data.get("items", {}).items():
                                if item["name"] == it:
                                    session["order"][item_id] = {"item": item, "qty": 1}
                if session["order"]:
                    session["stage"] = "confirm"
                    await send_order_summary(sender, session["order"], lang, bot=bot)
                else:
                    session["stage"] = "menu"
                    await send_main_menu(sender, session["order"], lang, bot=bot, db_session=db_session)
            else:
                session["stage"] = "menu"
                await send_main_menu(sender, session["order"], lang, bot=bot, db_session=db_session)
            return
        else:
            # Fallback for unhandled buttons (e.g. NEW_RESERVATION)
            session["stage"] = "menu"
            await send_main_menu(sender, session["order"], lang, bot=bot)
        return

    # Repeat-confirm stage: handles buttons from send_repeat_order_confirm
    if stage == "repeat_confirm":
        if text == "REPEAT_CONFIRM":
            profile = get_profile_db(sender, bot.owner_id if bot else 1)
            history = profile.get("order_history", [])
            MENU = get_bot_menu(bot.phone_number_id if bot else None, db_session=db_session)
            if history:
                last_items = history[-1].get("items", [])
                session["order"] = {}
                for it in last_items:
                    if isinstance(it, dict):
                        iid = it.get("item_id")
                        qty = it.get("qty", 1)
                        if iid:
                            _cat, item = find_item(iid, MENU)
                            if item:
                                session["order"][iid] = {"item": item, "qty": qty}
                    else:
                        for cat_data in MENU.values():
                            for item_id, item in cat_data.get("items", {}).items():
                                if item["name"] == it:
                                    session["order"][item_id] = {"item": item, "qty": 1}
            if session["order"]:
                session["stage"] = "confirm"
                await send_order_summary(sender, session["order"], lang, bot=bot)
            else:
                session["stage"] = "menu"
                await send_main_menu(sender, session["order"], lang, bot=bot, db_session=db_session)
        elif text == "REPEAT_ADD_MORE":
            session["stage"] = "menu"
            await send_main_menu(sender, session["order"], lang, bot=bot, db_session=db_session)
        else:
            session["stage"] = "menu"
            await send_main_menu(sender, session["order"], lang, bot=bot, db_session=db_session)
        return

    # Address update stage
    if stage == "address_update":
        if not is_valid_address(text):
            await send_text_message(sender, t(lang, "invalid_address"), bot=bot)
            return
        session["address"] = text.strip()
        save_profile(sender, session, owner_id=bot.owner_id if bot else None)
        await send_text_message(sender, f"✅ Address updated! {text}", bot=bot)
        session["stage"] = "menu"
        await send_main_menu(sender, session["order"], lang, bot=bot, db_session=db_session)
        return

    # Menu loading
    MENU = get_bot_menu(bot.phone_number_id if bot else None, db_session=db_session)

    if stage == "lang_select":
        lang_map = {"LANG_EN": "en", "LANG_AR": "ar", "LANG_HI": "hi", "LANG_FR": "fr", "LANG_DE": "de", "LANG_RU": "ru", "LANG_ZH": "zh", "LANG_ML": "ml"}
        if text in lang_map:
            session["lang"] = lang_map[text]
            lang = lang_map[text]
            session["stage"] = "menu"
            await send_text_message(sender, t(lang, "greeting_welcome"), bot=bot)
            await send_main_menu(sender, session["order"], lang, bot=bot, db_session=db_session)
        else:
            await send_language_selection(sender, bot=bot)
        return

    if text in ["SHOW_MENU", "BACK_MENU", "ADD_MORE"]:
        session["stage"] = "menu"
        await send_main_menu(sender, session["order"], lang, bot=bot, db_session=db_session)
        return

    if text == "BACK_TO_DELIVERY":
        session["stage"] = "delivery"
        session["delivery_type"] = ""
        await send_delivery_buttons(sender, session.get("name", ""), lang, bot=bot, table_number=session.get("table_number"))
        return

    # Text-based item remove ("remove FF1" / "delete FF1")
    m_remove = re.match(r"^(remove|delete)\s+([a-z0-9]+)$", text_lower)
    if m_remove:
        item_id = m_remove.group(2).upper()
        if item_id in session["order"]:
            del session["order"][item_id]
        await send_cart_view(sender, session["order"], lang, bot=bot)
        return

    # Explicit category navigation
    if text in CAT_MAP:
        session["stage"] = "items"
        session["current_cat"] = CAT_MAP[text]
        await send_category_items(sender, CAT_MAP[text], session["order"], lang, bot=bot, db_session=db_session)
        return

    # ADD_COMBO_DL1 must be checked BEFORE generic startswith("ADD_") or it gets eaten
    if text == "ADD_COMBO_DL1":
        MENU = get_bot_menu(bot.phone_number_id if bot else None, db_session=db_session)
        try:
            deal_item = MENU["deals"]["items"]["DL1"]
            if "DL1" in session["order"]:
                session["order"]["DL1"]["qty"] += 1
            else:
                session["order"]["DL1"] = {"item": deal_item, "qty": 1}
        except Exception as e:
            print(f"ADD_COMBO_DL1 error: {e}")
        session.pop("_pending_upsell_type", None)
        session["stage"] = "qty_control"
        last = session.get("last_added")
        if last and last in session["order"]:
            await send_qty_control(sender, last, session["order"][last]["item"], session["order"], lang, bot=bot)
        else:
            await send_cart_view(sender, session["order"], lang, bot=bot)
        return

    # Item management
    if text.startswith("ADD_"):
        item_id = text.replace("ADD_", "").upper()
        _cat, found_item = find_item(item_id, MENU)
        if not found_item:
            return

        # Cancel pending upsell stage when adding anything
        if stage in {"upsell_combo", "upsell_check"}:
            session.pop("_pending_upsell_type", None)
            session["stage"] = "items"
            stage = "items"

        # DL1 requires burger already in cart
        if item_id == "DL1":
            has_burger = any(k.startswith("FF") for k in session["order"])
            if not has_burger:
                await send_text_message(sender, t(lang, "pick_burger_first"), bot=bot)
                session["stage"] = "items"
                session["current_cat"] = "fastfood"
                session["deal_context"] = {"deal_id": "DL1_PENDING"}
                await send_category_items(sender, "fastfood", session["order"], lang, bot=bot, db_session=db_session)
                return
            if "DL1" in session["order"]:
                session["order"]["DL1"]["qty"] += 1
            else:
                session["order"]["DL1"] = {"item": found_item, "qty": 1}
            session["last_added"] = "DL1"
            session["stage"] = "qty_control"
            await send_text_message(sender, t(lang, "deal_added"), bot=bot)
            await send_qty_control(sender, "DL1", found_item, session["order"], lang, bot=bot)
            return

        deal_rules = get_deal_rules(bot)
        if item_id in deal_rules and "picks" in deal_rules[item_id]:
            rule = deal_rules[item_id]
            session["stage"] = "deal_build"
            session["deal_context"] = {"deal_id": item_id, "deal_item": found_item, "needs": list(rule.get("picks", [])), "picks": []}
            if rule.get("picks"):
                await prompt_deal_pick(sender, session, rule["picks"][0], lang, bot=bot, db_session=db_session)
            else:
                await finalize_deal(sender, session, lang, bot=bot, db_session=db_session)
            return

        # DL6: explicit Fish & Chips combo — no sub-picks needed
        if item_id == "DL6":
            if "DL6" in session["order"]:
                session["order"]["DL6"]["qty"] += 1
            else:
                session["order"]["DL6"] = {"item": found_item, "qty": 1, "components": ["Fish & Chips", "Soda"]}
            session["last_added"] = "DL6"
            session["stage"] = "qty_control"
            await send_text_message(sender, t(lang, "deal_added"), bot=bot)
            await send_qty_control(sender, "DL6", found_item, session["order"], lang, bot=bot)
            return

        if item_id in BBQ_NEEDS_SIDES:
            if item_id in session["order"]:
                session["order"][item_id]["qty"] += 1
                session["last_added"] = item_id
                session["stage"] = "qty_control"
                await send_qty_control(sender, item_id, found_item, session["order"], lang, bot=bot)
                return
            session["order"][item_id] = {"item": found_item, "qty": 1, "sides": []}
            session["last_added"] = item_id
            session["stage"] = "bbq_sides"
            session["deal_context"] = {"deal_id": "BBQ_SIDES", "target_item_id": item_id, "sides_needed": 2, "sides": []}
            await prompt_bbq_sides(sender, session, lang, bot=bot)
            return

        # Basic item add
        if item_id in session["order"]:
            session["order"][item_id]["qty"] += 1
        else:
            session["order"][item_id] = {"item": found_item, "qty": 1}
        session["last_added"] = item_id

        # DL1_PENDING: burger just chosen, auto-add DL1
        if (is_burger(item_id) and (session.get("deal_context") or {}).get("deal_id") == "DL1_PENDING"):
            dl1_item_lookup = find_item("DL1", MENU)
            dl1_item = dl1_item_lookup[1]
            if dl1_item:
                if "DL1" in session["order"]:
                    session["order"]["DL1"]["qty"] += 1
                else:
                    session["order"]["DL1"] = {"item": dl1_item, "qty": 1}
            session["deal_context"] = None
            session["stage"] = "qty_control"
            await send_text_message(sender, t(lang, "deal_added"), bot=bot)
            await send_qty_control(sender, item_id, found_item, session["order"], lang, bot=bot)
            return

        declined = session.get("upsell_declined_types", [])
        shown = session.get("upsell_shown_for", [])
        upsell_cfg = get_upsell_config(bot)

        # Burger combo upsell
        if (upsell_cfg.get("burger_combo")
                and is_burger(item_id)
                and "burger_combo" not in declined
                and item_id not in shown
                and not has_any_side(session["order"])
                and not has_any_drink(session["order"])
                and "DL1" not in session["order"]):
            burgers_count = sum(1 for k in session["order"] if k.startswith("FF"))
            if burgers_count == 1:
                session["upsell_shown_for"].append(item_id)
                session["_pending_upsell_type"] = "burger_combo"
                session["stage"] = "upsell_combo"
                await send_quick_combo_upsell(sender, lang, bot=bot)
                return

        # Pizza wings upsell
        if (upsell_cfg.get("pizza_wings")
                and is_pizza(item_id)
                and "pizza_wings" not in declined
                and item_id not in shown
                and "SD4" not in session["order"]
                and not has_any_side(session["order"])):
            session["upsell_shown_for"].append(item_id)
            session["_pending_upsell_type"] = "pizza_wings"
            session["stage"] = "upsell_combo"
            await send_quick_upsell(sender, "SD4", "🍗 Add 6 wings with your pizza? Most people do! 😄", lang, "pizza_wings", bot=bot)
            return

        session["stage"] = "qty_control"
        await send_qty_control(sender, item_id, found_item, session["order"], lang, bot=bot)
        return

    # upsell_combo: only SKIP_UPSELL handled here; ADD_COMBO_DL1 handled above
    # Any unrecognised text while in upsell_combo routes to qty_control, not silent drop
    if stage == "upsell_combo":
        session.pop("_pending_upsell_type", None)
        session["stage"] = "qty_control"
        last = session.get("last_added")
        if last and last in session["order"]:
            await send_qty_control(sender, last, session["order"][last]["item"], session["order"], lang, bot=bot)
        else:
            await send_cart_view(sender, session["order"], lang, bot=bot)
        return

    if text == "SKIP_UPSELL":
        ctx_type = session.get("_pending_upsell_type", "generic")
        if ctx_type not in session.get("upsell_declined_types", []):
            session.setdefault("upsell_declined_types", []).append(ctx_type)
        session.pop("_pending_upsell_type", None)
        last = session.get("last_added")
        session["stage"] = "qty_control"
        if last and last in session["order"]:
            await send_qty_control(sender, last, session["order"][last]["item"], session["order"], lang, bot=bot)
        else:
            await send_main_menu(sender, session["order"], lang, bot=bot, db_session=db_session)
        return

    if stage == "deal_build" and text.startswith("DEAL_PICK_"):
        picked_id = text.replace("DEAL_PICK_", "").upper()
        _cat, picked_item = find_item(picked_id, MENU)
        if picked_item:
            session["deal_context"]["picks"].append({"item_id": picked_id, "name": picked_item["name"]})
            needs = session["deal_context"]["needs"]
            if len(session["deal_context"]["picks"]) >= len(needs):
                await finalize_deal(sender, session, lang, bot=bot)
            else:
                await prompt_deal_pick(sender, session, needs[len(session["deal_context"]["picks"])], lang, bot=bot)
        else:
            await send_text_message(sender, "❌ Item not available. Please pick again.", bot=bot)
            needs = session["deal_context"]["needs"]
            await prompt_deal_pick(sender, session, needs[len(session["deal_context"]["picks"])], lang, bot=bot)
        return

    if stage == "bbq_sides":
        if text.startswith("SIDE_"):
            side_key = text.replace("SIDE_", "")
            if side_key in SIDE_CHOICES:
                session["deal_context"].setdefault("sides", []).append(SIDE_CHOICES[side_key])
                if len(session["deal_context"]["sides"]) >= session["deal_context"].get("sides_needed", 2):
                    await finalize_bbq_sides(sender, session, lang, bot=bot)
                else:
                    await prompt_bbq_sides(sender, session, lang, bot=bot)
            else:
                await prompt_bbq_sides(sender, session, lang, bot=bot)
        else:
            # Any non-side text while choosing sides: re-prompt
            await prompt_bbq_sides(sender, session, lang, bot=bot)
        return

    if text == "QTY_PLUS":
        last = session.get("last_added")
        if last and last in session["order"]:
            session["order"][last]["qty"] += 1
            await send_qty_control(sender, last, session["order"][last]["item"], session["order"], lang, bot=bot)
        else:
            await send_cart_view(sender, session["order"], lang, bot=bot)
        return

    if text == "QTY_MINUS":
        last = session.get("last_added")
        if last and last in session["order"]:
            if session["order"][last]["qty"] > 1:
                session["order"][last]["qty"] -= 1
                await send_qty_control(sender, last, session["order"][last]["item"], session["order"], lang, bot=bot)
            else:
                removed_name = session["order"][last]["item"]["name"]
                del session["order"][last]
                session["last_added"] = None
                await send_text_message(sender, f"✅ Removed: {removed_name}", bot=bot)
                session["stage"] = "menu"
                await send_main_menu(sender, session["order"], lang, bot=bot)
        else:
            await send_cart_view(sender, session["order"], lang, bot=bot)
        return

    if text == "CHECKOUT":
        if session["order"]:
            upsell_cfg = get_upsell_config(bot)
            skip_dessert = (
                not upsell_cfg.get("desserts")
                or has_any_dessert(session["order"])
                or "dessert" in session.get("upsell_declined_types", [])
            )
            if skip_dessert:
                session["stage"] = "confirm"
                await send_order_summary(sender, session["order"], lang, bot=bot)
            else:
                session["stage"] = "upsell_check"
                await send_dessert_upsell(sender, session["order"], lang, bot=bot)
        else:
            await send_text_message(sender, t(lang, "cart_empty"), bot=bot)
            await send_main_menu(sender, session["order"], lang, bot=bot)
        return

    if stage == "upsell_check":
        if text == "YES_UPSELL":
            session["stage"] = "items"
            session["current_cat"] = "desserts"
            await send_category_items(sender, "desserts", session["order"], lang, bot=bot)
        else:
            session.setdefault("upsell_declined_types", []).append("dessert")
            session["stage"] = "confirm"
            await send_order_summary(sender, session["order"], lang, bot=bot)
        return

    if text == "VIEW_CART":
        await send_cart_view(sender, session["order"], lang, bot=bot)
        return

    if text == "CONFIRM_ORDER":
        if session.get("name"):
            session["stage"] = "delivery"
            await send_delivery_buttons(sender, session["name"], lang, bot=bot, table_number=session.get("table_number"))
        else:
            session["stage"] = "get_name"
            await send_text_message(sender, t(lang, "name_ask"), bot=bot)
        return

    if text == "CANCEL_ORDER":
        session.update(new_session(sender, bot))
        await send_text_message(sender, t(lang, "cancelled"), bot=bot)
        await send_main_menu(sender, session["order"], lang, bot=bot)
        return

    if text == "DINE_IN":
        session["delivery_type"] = "dine_in"
        table_num = session.get("table_number", "?")
        session["stage"] = "payment"
        await send_text_message(sender, f"🍽️ Perfect! Table {table_num} noted.\n\nNow choose payment method 👇", bot=bot)
        await send_payment_buttons(sender, session.get("name", ""), lang, bot=bot)
        return

    if text in ["DELIVERY", "PICKUP"]:
        total = get_order_total(session["order"])
        if text == "DELIVERY":
            if total < MIN_DELIVERY_ORDER:
                await send_min_order_warning(sender, "delivery", lang, bot=bot)
                return
            session["delivery_type"] = "delivery"
            if session.get("address"):
                session["stage"] = "payment"
                await send_text_message(sender, f"✅ Delivering to: {session['address']}", bot=bot)
                await send_payment_buttons(sender, session.get("name", ""), lang, bot=bot)
            else:
                session["stage"] = "address"
                await send_text_message(sender, t(lang, "address_ask"), bot=bot)
        else:
            if total < MIN_PICKUP_ORDER:
                await send_min_order_warning(sender, "pickup", lang, bot=bot)
                return
            session["delivery_type"] = "pickup"
            session["stage"] = "payment"
            await send_payment_buttons(sender, session.get("name", ""), lang, bot=bot)
        return

    if text in ["CASH", "CARD_STRIPE", "APPLE_PAY"]:
        payment_map = {"CASH": t(lang, "cash"), "CARD_STRIPE": t(lang, "card"), "APPLE_PAY": t(lang, "apple_pay")}
        session["payment"] = payment_map[text]

        if text == "CARD_STRIPE":
            total = get_order_total(session["order"])
            tax_rate = bot.tax_rate if bot else 0.08
            grand_total = total * (1 + tax_rate) + get_delivery_fee(total, session.get("delivery_type"))
            order_id = str(int(time.time()))
            saved_orders[order_id] = {
                "session": session.copy(),
                "sender": sender,
                "timestamp": time.time(),
                "order": session["order"].copy(),
                "customer_name": session.get("name", ""),
                "delivery_type": session.get("delivery_type", ""),
                "address": session.get("address", ""),
            }
            payment_url = await create_stripe_checkout_session(order_id, grand_total)
            if payment_url:
                await send_text_message(sender, f"💳 Pay here:\n{payment_url}", bot=bot)
            else:
                await send_text_message(sender, "❌ Payment link failed. Please try another method.", bot=bot)
            return

        # Cash / Apple Pay — confirm order, then clear session
        order_id = await send_order_confirmed(sender, session, lang, bot=bot)
        session["order_id"] = order_id
        session["just_confirmed"] = True
        session["just_confirmed_at"] = time.time()

        # All post-confirm writes are fire-and-forget — customer doesn't wait
        _owner_id = bot.owner_id if bot else 1
        _order_snapshot = session["order"].copy()
        asyncio.create_task(save_profile_async(sender, session.copy(), owner_id=_owner_id))
        asyncio.create_task(add_to_order_history_async(sender, order_id, _order_snapshot, _owner_id))
        asyncio.create_task(notify_manager(sender, session.copy(), order_id, bot=bot))

        # Now clear for next order
        session["stage"] = "post_order"
        session["post_order_at"] = time.time()
        session["order"] = {}
        session["last_added"] = None
        return

    if stage == "get_name":
        if is_valid_name(text):
            session["name"] = text.strip().title()[:30]
            session["stage"] = "delivery"
            await send_delivery_buttons(sender, session["name"], lang, bot=bot, table_number=session.get("table_number"))
        else:
            await send_text_message(sender, t(lang, "invalid_name"), bot=bot)
        return

    if stage == "address":
        if is_valid_address(text):
            session["address"] = text.strip()
            session["stage"] = "payment"
            await send_text_message(sender, t(lang, "address_saved"), bot=bot)
            await send_payment_buttons(sender, session.get("name", ""), lang, bot=bot)
        else:
            await send_text_message(sender, t(lang, "invalid_address"), bot=bot)
        return

    # Greetings / Fallback
    if text_lower in ["hi", "hello", "hey", "start", "salam", "hola"]:
        if stage == "lang_select":
            await send_language_selection(sender, bot=bot)
        else:
            session["stage"] = "menu"
            await send_text_message(sender, t(lang, "greeting_welcome"), bot=bot)
            await send_main_menu(sender, session["order"], lang, bot=bot)
        return

    if is_menu_request(text_lower):
        session["stage"] = "menu"
        await send_main_menu(sender, session["order"], lang, bot=bot)
        return

    cat_guess = guess_category(text_lower)
    protected = {"get_name", "address", "payment", "delivery", "confirm", "upsell_check", "upsell_combo", "bbq_sides", "deal_build"}
    if cat_guess and stage not in protected:
        session["stage"] = "items"
        session["current_cat"] = cat_guess
        await send_category_items(sender, cat_guess, session["order"], lang, bot=bot)
        return

    if stage != "post_order":
        await send_text_message(sender, t(lang, "sorry_not_understood") + " " + t(lang, "menu_prompt"), bot=bot)
        return

    # AI only in post_order fallback
    session["conversation"].append({"role": "user", "content": text})
    reply = await get_ai_response(sender, text, lang, session)
    session["conversation"].append({"role": "assistant", "content": reply})
    session["conversation"] = session["conversation"][-8:]
    await send_text_message(sender, reply, bot=bot)
