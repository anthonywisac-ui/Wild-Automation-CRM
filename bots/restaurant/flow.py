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
    send_repeat_order_confirm, send_manager_action_list, send_list_message,
    send_manager_report_menu, send_manager_week_menu, send_manager_feature_menu,
    send_reservation_start, send_catalog_message
)
from .ai_utils import get_ai_response
from .stripe_utils import create_stripe_checkout_session
from db import SessionLocal, WhatsappBot, Reservation, Order, ChatHistory

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


# ========== Manager report keywords ==========
_REPORT_PERIOD_MAP = {
    "today":      ("day",          ""),
    "yesterday":  ("day",          "yesterday"),
    "this week":  ("week_current", ""),
    "week":       ("week_current", ""),
    "last 7":     ("week_last7",   ""),
    "7 days":     ("week_last7",   ""),
    "this month": ("month",        ""),
    "month":      ("month",        ""),
    "all time":   ("all",          ""),
    "all":        ("all",          ""),
}
_REPORT_TRIGGER_WORDS = {"report", "sales", "stats", "analytics", "revenue", "summary"}


def _parse_report_period(text_lower: str):
    """Return (period, period_value) by scanning text for period keywords."""
    from datetime import date, timedelta
    for phrase, (period, value) in _REPORT_PERIOD_MAP.items():
        if phrase in text_lower:
            if phrase == "yesterday":
                yesterday = (date.today() - timedelta(days=1)).strftime("%d/%m/%Y")
                return "day", yesterday
            return period, value
    return "week_current", ""  # default: current week


async def _send_manager_report(sender, bot, text_lower: str, db_session):
    """Query DB and send a text sales summary to the manager."""
    from .report_generator import build_text_summary, _get_date_range, _filter_orders
    period, period_value = _parse_report_period(text_lower)

    # Detect delivery-type filter from message
    if "delivery" in text_lower:
        feature = "delivery"
        feature_label = "Delivery Orders"
    elif "car" in text_lower:
        feature = "car"
        feature_label = "Car Delivery"
    elif "dine" in text_lower or "qr" in text_lower:
        feature = "qr"
        feature_label = "Dine-in"
    else:
        feature = "all"
        feature_label = "All Orders"

    start_dt, end_dt, period_label = _get_date_range(period, period_value)

    db_local = db_session or SessionLocal()
    close_db = db_session is None
    try:
        orders = db_local.query(SaleRecord).filter(
            SaleRecord.bot_id == (bot.id if bot else None),
            SaleRecord.created_at >= start_dt,
            SaleRecord.created_at <= end_dt,
        ).all()
        reservations = db_local.query(Reservation).filter(
            Reservation.bot_id == (bot.id if bot else None),
            Reservation.created_at >= start_dt,
            Reservation.created_at <= end_dt,
        ).all() if bot else []
    finally:
        if close_db:
            db_local.close()

    filtered = _filter_orders(orders, start_dt, end_dt, feature)
    text_summary = build_text_summary(filtered, reservations, period_label, feature_label)
    await send_text_message(sender, text_summary, bot=bot)


# ========== Manager flow handler ==========
async def handle_manager_flow(sender, text, is_button=False, bot=None, db_session=None):
    """Handle messages/buttons from the restaurant manager."""
    try:
        text_lower = text.lower().strip()

        # ── Report request ────────────────────────────────────────────────────
        if any(w in text_lower for w in _REPORT_TRIGGER_WORDS):
            await _send_manager_report(sender, bot, text_lower, db_session)
            return

        # ── MGR_{order_id}_{ACTION} buttons from send_manager_action_list ────
        if text.startswith("MGR_"):
            parts = text.split("_", 2)
            if len(parts) < 3:
                await send_text_message(sender, "Unknown action.", bot=bot)
                return
            order_id = parts[1]
            action = parts[2]

            order_data = saved_orders.get(order_id, {})
            customer_number = order_data.get("sender", "")

            if action == "READY":
                if customer_number:
                    await send_text_message(
                        customer_number,
                        f"Your order #{order_id} is ready!\n\nPlease come pick it up — it's hot and waiting for you!",
                        bot=bot
                    )
                await send_text_message(sender, f"Customer notified: order #{order_id} is ready.", bot=bot)

            elif action == "OUTFORDELIVERY":
                if customer_number:
                    await send_text_message(
                        customer_number,
                        f"Your order #{order_id} is on the way!\n\nOur driver is heading to you. Should arrive in 15-30 minutes.",
                        bot=bot
                    )
                await send_text_message(sender, f"Customer notified: order #{order_id} out for delivery.", bot=bot)

            elif action == "CANCELLED":
                if customer_number:
                    await send_text_message(
                        customer_number,
                        f"We're sorry — order #{order_id} has been cancelled.\n\nPlease contact us if you have any questions.",
                        bot=bot
                    )
                await send_text_message(sender, f"Customer notified: order #{order_id} cancelled.", bot=bot)

            else:
                await send_text_message(sender, f"Unknown action '{action}' for order #{order_id}.", bot=bot)
            return

        # ── Free-text fallback ────────────────────────────────────────────────
        await send_text_message(
            sender,
            "*Manager Panel*\n\n"
            "Use the action buttons sent with each order to update customers.\n\n"
            "Order actions:\n"
            "  Ready | Out for Delivery | Cancelled\n\n"
            "Reports (type any of these):\n"
            "  'report today'  |  'sales this week'  |  'stats this month'\n"
            "  'report all time'  |  'delivery report'  |  'report last 7 days'",
            bot=bot
        )
    except Exception as e:
        print(f"MANAGER FLOW ERROR: {e}")
        traceback.print_exc()


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
        elif text == "NEW_RESERVATION":
            session["stage"] = "reservation_name"
            await send_text_message(sender, "🍽️ *Table Reservation*\n\nWhat name should the reservation be under?", bot=bot)
        else:
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

    # Reservation stages
    if stage == "reservation_name":
        name = text.strip()
        if is_valid_name(name):
            session["res_name"] = name.title()[:50]
            session["stage"] = "reservation_party"
            await send_text_message(sender, f"👥 How many people will be dining, {session['res_name']}?", bot=bot)
        else:
            await send_text_message(sender, "Please enter a valid name (letters only).", bot=bot)
        return

    if stage == "reservation_party":
        try:
            party = int(text.strip())
            if 1 <= party <= 20:
                session["res_party"] = party
                session["stage"] = "reservation_date"
                await send_text_message(sender, "📅 What date? (e.g. 25/12/2025)", bot=bot)
            else:
                await send_text_message(sender, "Please enter a number between 1 and 20.", bot=bot)
        except ValueError:
            await send_text_message(sender, "Please enter the number of guests (e.g. 4).", bot=bot)
        return

    if stage == "reservation_date":
        raw = text.strip()
        parsed_date = None
        for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d %b %Y", "%d %B %Y"):
            try:
                parsed_date = time.strftime("%d/%m/%Y", time.strptime(raw, fmt))
                break
            except ValueError:
                continue
        if parsed_date:
            session["res_date"] = parsed_date
            session["stage"] = "reservation_time"
            await send_text_message(sender, "🕐 What time? (e.g. 7:30 PM)", bot=bot)
        else:
            await send_text_message(sender, "Date not recognised. Please use DD/MM/YYYY format (e.g. 25/12/2025).", bot=bot)
        return

    if stage == "reservation_time":
        session["res_time"] = text.strip()[:20]
        # Save to DB
        try:
            db_local = SessionLocal()
            res = Reservation(
                owner_id=bot.owner_id if bot else 1,
                bot_id=bot.id if bot else None,
                customer_phone=sender,
                customer_name=session.get("res_name", ""),
                party_size=session.get("res_party", 2),
                reservation_date=session.get("res_date", ""),
                reservation_time=session.get("res_time", ""),
                status="Pending",
            )
            db_local.add(res)
            db_local.commit()
            db_local.close()
        except Exception as e:
            print(f"Reservation save error: {e}")
        session["stage"] = "menu"
        confirm = (
            f"✅ *Reservation Confirmed!*\n\n"
            f"👤 Name: {session.get('res_name', '')}\n"
            f"👥 Party: {session.get('res_party', '')}\n"
            f"📅 Date: {session.get('res_date', '')}\n"
            f"🕐 Time: {session.get('res_time', '')}\n\n"
            f"We look forward to seeing you! 🍽️"
        )
        await send_text_message(sender, confirm, bot=bot)
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

        # ── Dynamic Deal Logic ──────────────────────────────────────────────
        deal_rules = get_deal_rules(bot)
        rule = deal_rules.get(item_id)
        
        # ── Universal Requirement Engine ─────────────────────────────────────
        deal_rules = get_deal_rules(bot)
        rule = deal_rules.get(item_id)
        
        if rule:
            # 1. Check Pre-conditions (Must-haves)
            requires = rule.get("requires", [])
            if isinstance(requires, str): requires = [requires]
            # Migration support for old 'burger_in_cart' string
            requires = ["burger" if r == "burger_in_cart" else r for r in requires]
            
            for req in requires:
                # Intelligent Match: Check Item ID, Name, and Category
                met = False
                for k, v in session["order"].items():
                    item_name = v["item"].get("name", "").lower()
                    # 1. Direct ID match (e.g. "FF" prefix) or keyword in ID
                    if req.lower() in k.lower(): met = True
                    # 2. Name match (e.g. "Burger" in "Classic Burger")
                    elif req.lower() in item_name: met = True
                    # 3. Category match (Check if this item belongs to a category matching the requirement)
                    else:
                        for cat_key, cat_data in bot_menu.items():
                            if k in cat_data.get("items", {}):
                                if req.lower() in cat_key.lower() or req.lower() in cat_data.get("name", "").lower():
                                    met = True
                    if met: break

                if not met:
                    msg = f"To get this deal, please add {req.title()} to your cart first! 🛒"
                    await send_text_message(sender, msg, bot=bot)
                    session["stage"] = "items"
                    # Try to find which category contains this requirement to help the user
                    help_cat = "fastfood"
                    if "pizza" in req.lower(): help_cat = "pizza"
                    elif "wing" in req.lower() or "side" in req.lower(): help_cat = "sides"
                    elif "drink" in req.lower() or "soda" in req.lower(): help_cat = "drinks"
                    
                    session["current_cat"] = help_cat
                    session["deal_context"] = {"deal_id": f"{item_id}_PENDING"}
                    await send_category_items(sender, help_cat, session["order"], lang, bot=bot, db_session=db_session)
                    return

            # 2. Check Selection Flow (Picks)
            if "picks" in rule:
                session["stage"] = "deal_build"
                session["deal_context"] = {"deal_id": item_id, "deal_item": found_item, "needs": list(rule.get("picks", [])), "picks": []}
                if rule.get("picks"):
                    await prompt_deal_pick(sender, session, rule["picks"][0], lang, bot=bot, db_session=db_session)
                else:
                    await finalize_deal(sender, session, lang, bot=bot, db_session=db_session)
                return

        # Default: Just add the item
        if item_id in session["order"]:
            session["order"][item_id]["qty"] += 1
        else:
            session["order"][item_id] = {"item": found_item, "qty": 1}
        
        session["last_added"] = item_id
        session["stage"] = "qty_control"

        # ── Pending Deal Completion ────────────────────────────────────────
        # If we were waiting for an item to fulfill a deal, re-trigger the deal now
        pending = session.get("deal_context", {}).get("deal_id", "")
        if pending.endswith("_PENDING"):
            orig_deal_id = pending.replace("_PENDING", "")
            # IMPORTANT: Clear context before re-triggering to prevent loops
            session["deal_context"] = {} 
            # CRITICAL: Save session to DB before recursive call so handle_flow sees the new items!
            from .db import save_session_db
            save_session_db(sender, bot.id, session, db_session=db_session)
            
            # Re-run handle_flow for the original deal
            await handle_flow(sender, f"ADD_{orig_deal_id}", is_button=True, bot=bot, db_session=db_session)
            return # Stop here, handle_flow will take over the response

        if item_id.startswith("DL"):
            await send_text_message(sender, t(lang, "deal_added"), bot=bot)
        
        await send_qty_control(sender, item_id, found_item, session["order"], lang, bot=bot)
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

        # Store in saved_orders so manager status updates can reach customer
        saved_orders[str(order_id)] = {
            "session": session.copy(),
            "sender": sender,
            "timestamp": time.time(),
            "order": session["order"].copy(),
            "customer_name": session.get("name", ""),
            "delivery_type": session.get("delivery_type", "pickup"),
            "address": session.get("address", ""),
        }
        customer_order_lookup.setdefault(sender, []).append(str(order_id))

        # All post-confirm writes are fire-and-forget — customer doesn't wait
        _owner_id = bot.owner_id if bot else 1
        _order_snapshot = session["order"].copy()
        asyncio.create_task(save_profile_async(sender, session.copy(), owner_id=_owner_id))
        asyncio.create_task(add_to_order_history_async(sender, order_id, _order_snapshot, _owner_id))
        asyncio.create_task(notify_manager(sender, session.copy(), str(order_id), bot=bot))

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
            _name = (bot.business_name or bot.name) if bot else "Restaurant"
            await send_text_message(sender, f"Welcome to {_name}! 🍽️", bot=bot)
            await send_catalog_message(sender, f"Browse our full catalog from {_name} 👇", bot=bot)
            await send_main_menu(sender, session["order"], lang, bot=bot)
        return

    # ── WhatsApp Catalog: 'ADD_ITEMID:QTY|...' format (from order msg_type) ──
    if "|" in text and all(part.startswith("ADD_") for part in text.split("|")):
        MENU = get_bot_menu(bot.phone_number_id if bot else None, db_session=db_session)
        added_names = []
        for part in text.split("|"):
            try:
                rest = part.replace("ADD_", "")
                if ":" in rest:
                    item_id, qty_str = rest.rsplit(":", 1)
                    qty = int(qty_str)
                else:
                    item_id, qty = rest, 1
                item_id = item_id.upper()
                _cat, found_item = find_item(item_id, MENU)
                if found_item:
                    if item_id in session["order"]:
                        session["order"][item_id]["qty"] += qty
                    else:
                        session["order"][item_id] = {"item": found_item, "qty": qty}
                    added_names.append(f"{found_item['name']} x{qty}")
            except Exception as _e:
                print(f"Catalog order parse error: {_e}")
        if added_names:
            session["stage"] = "confirm"
            items_text = ", ".join(added_names)
            await send_text_message(sender, f"✅ Added to cart: {items_text}", bot=bot)
            await send_cart_view(sender, session["order"], lang, bot=bot)
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


# ========== Manager state storage (in-memory per bot) ==========
# {sender: {"stage": ..., "period": ..., "period_value": ..., "feature": ...}}
_manager_sessions: dict = {}


def _get_mgr_session(sender: str) -> dict:
    if sender not in _manager_sessions:
        _manager_sessions[sender] = {"stage": "idle"}
    return _manager_sessions[sender]


# ========== Manager flow entry point ==========
async def handle_manager_flow(sender, text, is_button=False, bot=None, db_session=None):
    """Handle all messages from the manager's WhatsApp number."""
    try:
        await _handle_manager_inner(sender, text, is_button, bot, db_session)
    except Exception as e:
        print(f"MANAGER FLOW ERROR: {e}")
        traceback.print_exc()
        await send_text_message(sender, "⚠️ Error processing your request. Please try again.", bot=bot)


async def _handle_manager_inner(sender, text, is_button, bot, db_session):
    mgr = _get_mgr_session(sender)
    text_upper = text.strip().upper()
    text_lower = text.strip().lower()

    # ── Handle MGR_<order_id>_<ACTION> status updates ──────────────────────────
    if text.startswith("MGR_"):
        parts = text.split("_")
        # format: MGR_{order_id}_{ACTION}
        if len(parts) >= 3:
            order_id = parts[1]
            action = parts[2]
            await _process_manager_status(sender, order_id, action, bot=bot)
            return

    # ── Idle: check for report trigger ──────────────────────────────────────
    trigger_words = {"report", "رپورٹ", "sales", "stats", "statistics"}
    if mgr["stage"] == "idle" or text_lower in trigger_words:
        if text_lower in trigger_words or text_lower.startswith("report"):
            mgr["stage"] = "report_period"
            await send_manager_report_menu(sender, bot=bot)
            return
        # Unknown message from manager — brief acknowledgement
        await send_text_message(
            sender,
            "👋 Manager Panel\n\n"
            "Send *report* for sales report.\n"
            "Order status updates arrive automatically.",
            bot=bot
        )
        return

    # ── Report period selection ──────────────────────────────────────────────
    if mgr["stage"] == "report_period":
        if text_upper == "RPT_DAY":
            mgr["stage"] = "report_day_input"
            mgr["period"] = "day"
            await send_text_message(sender, "📅 Enter date (DD/MM/YYYY):", bot=bot)
        elif text_upper == "RPT_WEEK":
            mgr["stage"] = "report_week_type"
            mgr["period"] = "week"
            await send_manager_week_menu(sender, bot=bot)
        elif text_upper == "RPT_MONTH":
            mgr["period"] = "month"
            mgr["period_value"] = ""
            mgr["stage"] = "report_feature"
            await send_manager_feature_menu(sender, bot=bot)
        elif text_upper == "RPT_ALL":
            mgr["period"] = "all"
            mgr["period_value"] = ""
            mgr["stage"] = "report_feature"
            await send_manager_feature_menu(sender, bot=bot)
        else:
            await send_manager_report_menu(sender, bot=bot)
        return

    # ── Day input ────────────────────────────────────────────────────────────
    if mgr["stage"] == "report_day_input":
        mgr["period_value"] = text.strip()
        mgr["stage"] = "report_feature"
        await send_manager_feature_menu(sender, bot=bot)
        return

    # ── Week type ────────────────────────────────────────────────────────────
    if mgr["stage"] == "report_week_type":
        if text_upper == "RPT_WEEK_CURRENT":
            mgr["period"] = "week_current"
        else:
            mgr["period"] = "week_last7"
        mgr["period_value"] = ""
        mgr["stage"] = "report_feature"
        await send_manager_feature_menu(sender, bot=bot)
        return

    # ── Feature selection → generate report ─────────────────────────────────
    if mgr["stage"] == "report_feature":
        feature_map = {
            "RPT_FEAT_ALL": "all",
            "RPT_FEAT_DELIVERY": "delivery",
            "RPT_FEAT_CAR": "car",
            "RPT_FEAT_RESERVATION": "reservation",
            "RPT_FEAT_QR": "qr",
        }
        feature = feature_map.get(text_upper, "all")
        feature_labels = {
            "all": "All Orders",
            "delivery": "Home Deliveries",
            "car": "Car Deliveries",
            "reservation": "Reservations",
            "qr": "Restaurant QR Orders",
        }

        mgr["stage"] = "idle"
        await send_text_message(sender, "⏳ Generating your report...", bot=bot)
        await _generate_and_send_report(
            sender, bot, db_session,
            period=mgr.get("period", "all"),
            period_value=mgr.get("period_value", ""),
            feature=feature,
            feature_label=feature_labels.get(feature, "All"),
        )
        return

    # Fallback
    mgr["stage"] = "idle"
    await send_text_message(sender, "Send *report* to generate a sales report.", bot=bot)


async def _process_manager_status(sender, order_id, action, bot=None):
    """Update order status and notify customer."""
    order_data = saved_orders.get(order_id, {})
    customer_number = order_data.get("sender") or order_data.get("customer_number", "")
    customer_name = order_data.get("customer_name", "Customer")

    status_messages = {
        "READY": f"✅ Great news, {customer_name}! Your order #{order_id} is *ready* for pickup! 🍽️\n\nCome collect when you're ready! 😊",
        "OUTFORDELIVERY": f"🚚 Your order #{order_id} is *out for delivery*, {customer_name}!\n\nExpect it in 20-30 minutes. Get ready! 😊",
        "CANCELLED": f"❌ We're sorry, {customer_name}. Your order #{order_id} has been *cancelled*.\n\nPlease contact us for a refund or to re-order. 🙏",
    }
    msg = status_messages.get(action, f"Order #{order_id} status updated.")

    if customer_number:
        await send_text_message(customer_number, msg, bot=bot)
        await send_text_message(sender, f"✅ Status sent to +{customer_number}", bot=bot)
    else:
        await send_text_message(sender, f"⚠️ Order #{order_id} customer number not found in memory.", bot=bot)

    # Update DB order status
    try:
        db_local = SessionLocal()
        db_order = db_local.query(Order).filter(Order.id == int(order_id)).first()
        if db_order:
            db_order.status = action.title()
            db_local.commit()
        db_local.close()
    except Exception as e:
        print(f"Order status DB update error: {e}")


async def _generate_and_send_report(sender, bot, db_session, period, period_value, feature, feature_label):
    """Fetch orders/reservations, generate PDF, send via WhatsApp."""
    from .report_generator import (
        _get_date_range, _filter_orders, generate_report_pdf,
        build_text_summary
    )
    import os

    try:
        start_dt, end_dt, period_label = _get_date_range(period, period_value)

        # Fetch from DB
        db_local = db_session or SessionLocal()
        owner_id = bot.owner_id if bot else None
        q = db_local.query(Order)
        if owner_id:
            q = q.filter(Order.owner_id == owner_id)
        all_orders = q.all()

        reservations = []
        if feature in ("reservation", "all"):
            rq = db_local.query(Reservation)
            if owner_id:
                rq = rq.filter(Reservation.owner_id == owner_id)
            all_res = rq.all()
            reservations = [r for r in all_res if start_dt <= r.created_at <= end_dt]

        if db_session is None:
            db_local.close()

        # Filter orders
        if feature == "reservation":
            orders = []
        else:
            orders = _filter_orders(all_orders, start_dt, end_dt, feature)

        owner_name = bot.business_name if bot else ""

        # Send text summary first
        summary = build_text_summary(orders, reservations, period_label, feature_label)
        await send_text_message(sender, summary, bot=bot)

        # Generate PDF
        pdf_path = generate_report_pdf(orders, reservations, period_label, feature_label, owner_name)

        # Try to send PDF via WhatsApp document upload
        # WhatsApp requires a publicly accessible URL. We'll host it via a temp endpoint or skip.
        # For now: inform manager where the file is and send text summary.
        # Full PDF sending requires a media upload endpoint.
        await send_text_message(
            sender,
            f"📄 PDF report generated.\n"
            f"File: {os.path.basename(pdf_path)}\n\n"
            f"_To receive PDFs directly, set up a media hosting URL in your server config._",
            bot=bot
        )

    except Exception as e:
        print(f"Report generation error: {e}")
        traceback.print_exc()
        await send_text_message(sender, f"❌ Report generation failed: {str(e)[:100]}", bot=bot)
