# flow.py - Complete restaurant flow (from your original working bot)
import time
import random
import re
import traceback
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
    send_repeat_order_confirm, send_manager_action_list
)
from .ai_utils import get_ai_response
# MENU will be loaded dynamically from DB now
from .stripe_utils import create_stripe_checkout_session
import json
from db import SessionLocal, WhatsappBot, Reservation

# ========== Constants for Deal Logic ==========
DEAL_RULES = {
    "DL1": {"type": "combo", "burger_needed": True, "price": 4.99},
    "DL2": {"type": "pick", "needs": ["burger"], "name": "Burger Deal"},
    "DL3": {"type": "pick", "needs": ["pizza"], "name": "Pizza Wings Deal"},
    "DL4": {"type": "pick", "needs": ["pizza", "pizza"], "name": "2 Pizza Deal"},
    "DL5": {"type": "pick", "needs": ["2sides"], "name": "Ribs & 2 Sides"},
    "DL6": {"type": "pick", "needs": ["pizza"], "name": "Pizza Soda Deal"}
}
BBQ_NEEDS_SIDES = ["RB1", "RB2", "BK1", "BK2", "BK3"] 
SIDE_CHOICES = ["SIDE_MAC", "SIDE_FRIES", "SIDE_SLAW", "SIDE_SALAD"]

async def notify_manager(sender, session, order_id, bot=None):
    """Notify restaurant manager about a new order"""
    try:
        from .whatsapp_handlers import send_manager_action_list
        from .config import MANAGER_NUMBER
        
        order_text = get_order_text(session["order"])
        total = get_order_total(session["order"])
        tax_rate = bot.tax_rate if bot else 0.08
        tax = total * tax_rate
        delivery_charge = bot.delivery_fee if bot else get_delivery_fee(total, session.get("delivery_type"))
        grand_total = total + tax + delivery_charge
        
        header = f"New Order #{order_id}"
        body = (
            f"👤 Customer: {session.get('name')} ({sender})\n"
            f"📍 Type: {session.get('delivery_type','?').upper()}\n"
            f"🏠 Address: {session.get('address','N/A')}\n"
            f"💰 Total: ${grand_total:.2f}\n\n"
            f"🛒 Items:\n{order_text}"
        )
        await send_manager_action_list(order_id, sender, header, body)
    except Exception as e:
        print(f"Manager Notification Error: {e}")

async def notify_manager_status(order_id, status_text, bot=None):
    """Notify manager about a status issue (e.g. late order)"""
    try:
        from .whatsapp_handlers import send_whatsapp_to_number
        from .config import MANAGER_NUMBER
        msg = f"⚠️ ORDER ALERT #{order_id}\nStatus: {status_text}"
        await send_whatsapp_to_number(MANAGER_NUMBER, msg, bot=bot)
    except Exception as e:
        print(f"Manager Status Notification Error: {e}")
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
                    cat_type = cat.get("type", "normal")
                    
                    dynamic_menu[cat_id] = {
                        "name": cat["name"],
                        "type": cat_type,
                        "display": cat.get("display", "list"),
                        "items": {}
                    }
                    
                    for i, item in enumerate(cat["items"]):
                        # Prefer stable ID if present, fallback to prefix+idx
                        prefix = cat.get("prefix", "ITEM").upper()
                        item_id = item.get("id") or f"{prefix}{i+1}"
                        
                        dynamic_menu[cat_id]["items"][item_id] = {
                            "name": item["name"],
                            "price": item["price"],
                            "desc": item.get("desc", ""),
                            "emoji": item.get("emoji", "🍕"),
                            "addons": item.get("addons", "")
                        }
                db.close()
                return dynamic_menu
        db.close()
    except Exception as e:
        print(f"Error loading dynamic menu: {e}")
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
        "upsell_declined_types": [],
        "upsell_shown_for": [],
        "order_id": None,
        "deal_context": None,
        "post_order_at": 0,
        "just_confirmed": False,
        "just_confirmed_at": 0,
    }

def get_session(sender, bot):
    session = get_session_db(sender, bot.id)
    if not session:
        session = new_session(sender, bot)
        save_session_db(sender, bot.id, session)
    return session

# ========== Helper functions (deal, side, etc.) ==========
async def prompt_deal_pick(sender, session, kind, lang="en"):
    import aiohttp
    from ..config import WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID
    from ..session import SharedSession
    ctx = session["deal_context"]
    deal_id = ctx["deal_id"]
    if kind == "burger":
        cat_key = "fastfood"
        prompt_key = "choose_burger_deal"
    elif kind == "pizza":
        cat_key = "pizza"
        prompt_key = "choose_pizza_deal"
    elif kind == "2sides":
        session["stage"] = "bbq_sides"
        ctx["sides_needed"] = 2
        ctx.setdefault("sides", [])
        await prompt_bbq_sides(sender, session, lang)
        return
    else:
        return
    
    MENU = session.get("menu", {})
    cat = MENU.get(cat_key, {"items": {}})
    rows = []
    for item_id, item in cat.get("items", {}).items():
        title = truncate_title(f"{item['emoji']} {item['name']}", 24)
        desc = f"${item['price']:.2f} - {item['desc']}"
        if len(desc) > 72:
            desc = desc[:71] + "…"
        rows.append({
            "id": f"DEAL_PICK_{item_id}",
            "title": title,
            "description": desc,
        })
    url = f"https://graph.facebook.com/v18.0/{WHATSAPP_PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp", "to": sender, "type": "interactive",
        "interactive": {
            "type": "list",
            "header": {"type": "text", "text": truncate_title(ctx["deal_item"]["name"], 60)},
            "body": {"text": t(lang, prompt_key)},
            "footer": {"text": "Deal Builder"},
            "action": {"button": "Select", "sections": [{"title": truncate_title(cat["name"], 24), "rows": rows}]}
        }
    }
    shared_session = await SharedSession.get_session()
    async with shared_session.post(url, json=payload, headers=headers) as r:
        _ = await r.text()

async def finalize_deal(sender, session, lang="en"):
    ctx = session["deal_context"]
    deal_id = ctx["deal_id"]
    deal_item = ctx["deal_item"]
    components = [p["name"] for p in ctx.get("picks", [])]
    if deal_id == "DL2":
        components = components + ["Fries", "Soda"]
    elif deal_id == "DL3":
        components = components + ["6 Wings"]
    elif deal_id == "DL4":
        components = components + ["2 Sodas"]
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
    await send_text_message(sender, t(lang, "deal_added"))
    await send_qty_control(sender, key, deal_item, session["order"], lang)

async def prompt_bbq_sides(sender, session, lang="en"):
    import aiohttp
    from ..config import WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID
    from ..session import SharedSession
    ctx = session["deal_context"]
    picked_so_far = ctx.get("sides", [])
    needed = ctx.get("sides_needed", 2)
    remaining = needed - len(picked_so_far)
    prompt_key = "pick_ribs_sides" if ctx.get("deal_id") == "DL5" else "pick_bbq_sides"
    progress = f" ({len(picked_so_far)}/{needed} picked)" if picked_so_far else ""
    url = f"https://graph.facebook.com/v18.0/{WHATSAPP_PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    rows = [
        {"id": "SIDE_MAC", "title": truncate_title(t(lang, "side_mac"), 24), "description": "Creamy and cheesy"},
        {"id": "SIDE_FRIES", "title": truncate_title(t(lang, "side_fries"), 24), "description": "Crispy golden"},
        {"id": "SIDE_SLAW", "title": truncate_title(t(lang, "side_slaw"), 24), "description": "Fresh crunch"},
        {"id": "SIDE_SALAD", "title": truncate_title(t(lang, "side_salad"), 24), "description": "Classic greens"},
    ]
    payload = {
        "messaging_product": "whatsapp", "to": sender, "type": "interactive",
        "interactive": {
            "type": "list",
            "header": {"type": "text", "text": "🍖 Choose Your Sides"},
            "body": {"text": f"{t(lang, prompt_key)}{progress}"},
            "footer": {"text": f"Pick {remaining} more"},
            "action": {"button": "Pick Side", "sections": [{"title": "Sides", "rows": rows}]}
        }
    }
    shared_session = await SharedSession.get_session()
    async with shared_session.post(url, json=payload, headers=headers) as r:
        _ = await r.text()

async def finalize_bbq_sides(sender, session, lang="en"):
    ctx = session["deal_context"]
    sides = ctx.get("sides", [])
    MENU = session.get("menu", {})
    
    if ctx.get("deal_id") == "DL5":
        # Handle DL5 Ribs deal
        deals_cat = MENU.get("deals", {"items": {}})
        deal_item = deals_cat.get("items", {}).get("DL5")
        if not deal_item:
            deal_item = {"name": "Ribs & 2 Sides Deal", "price": 24.99}
            
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
        await send_text_message(sender, t(lang, "deal_added"))
        await send_qty_control(sender, key, deal_item, session["order"], lang)
        return
    target_id = ctx.get("target_item_id")
    if target_id and target_id in session["order"]:
        session["order"][target_id]["sides"] = sides
        session["last_added"] = target_id
        session["stage"] = "qty_control"
        session["deal_context"] = None
        item = session["order"][target_id]["item"]
        await send_text_message(sender, f"✅ Sides locked in: {', '.join(sides)}")
        await send_qty_control(sender, target_id, item, session["order"], lang)

# ========== Order status ==========
async def handle_order_status(sender, session, lang, text, bot=None):
    order_id = extract_order_number(text)
    if not order_id:
        order_id = session.get("order_id")
    if not order_id:
        orders_list = customer_order_lookup.get(sender, [])
        if orders_list:
            order_id = orders_list[-1]
    if not order_id:
        await send_text_message(sender, "I don't see an active order. Type *menu* to place a new order!")
        return
    order_data = saved_orders.get(order_id)
    if not order_data:
        await send_text_message(sender, f"Checking order #{order_id}... I'll get back to you shortly.")
        return
    elapsed_min = (time.time() - order_data["timestamp"]) / 60
    delivery_type = order_data.get("delivery_type", "pickup")
    expected_max = 45 if delivery_type == "delivery" else 20
    if elapsed_min < expected_max:
        remaining = int(expected_max - elapsed_min)
        msg = f"Your order #{order_id} is being prepared. It should be ready in about {remaining} minutes."
    else:
        # Escalation logic
        msg = f"I apologize for the delay on order #{order_id}. I've just escalated this to the manager to check on it immediately."
        await notify_manager_status(order_id, f"Customer checking on LATE order ({int(elapsed_min)}m elapsed)")
    await send_text_message(sender, msg, bot=bot)

# ========== Main flow entry point (called from router) ==========
async def handle_flow(sender, text, is_button=False, bot=None):
    """Wrapper to handle errors and logging"""
    try:
        await _handle_flow_inner(sender, text, is_button, bot)
    except Exception as e:
        print(f"FLOW ERROR: {e}")
        traceback.print_exc()
        await send_text_message(sender, "Sorry, I encountered an error. Please type *menu* to restart.", bot=bot)

# ========== Reservation Flow Helpers ==========
async def start_reservation(sender, session, lang, bot=None):
    session["stage"] = "reserve_date"
    await send_text_message(sender, t(lang, "reserve_prompt_date") or "📅 Please enter the date for your reservation (e.g., Tomorrow, or 25th Oct):", bot=bot)

async def handle_reservation_flow(sender, session, text, lang, bot=None):
    stage = session["stage"]
    
    if stage == "reserve_date":
        session["reserve_date"] = text
        session["stage"] = "reserve_time"
        await send_text_message(sender, t(lang, "reserve_prompt_time") or "⏰ What time would you like to arrive?", bot=bot)
        
    elif stage == "reserve_time":
        session["reserve_time"] = text
        session["stage"] = "reserve_size"
        await send_text_message(sender, t(lang, "reserve_prompt_size") or "👥 How many people will be in your party?", bot=bot)
        
    elif stage == "reserve_size":
        try:
            size = int(re.search(r'\d+', text).group())
            session["reserve_size"] = size
            session["stage"] = "reserve_confirm"
            msg = f"📝 *Confirm Reservation:*\n📅 Date: {session['reserve_date']}\n⏰ Time: {session['reserve_time']}\n👥 Party Size: {size}\n\nType *Confirm* to book or *Cancel* to start over."
            await send_text_message(sender, msg, bot=bot)
        except:
            await send_text_message(sender, "Please enter a number for the party size.", bot=bot)

    elif stage == "reserve_confirm":
        if "confirm" in text.lower():
            # Save to DB
            try:
                db = SessionLocal()
                # Find bot for this session (using a default or session-stored ID)
                bot = db.query(WhatsappBot).filter(WhatsappBot.bot_type == "restaurant").first()
                new_res = Reservation(
                    owner_id=bot.owner_id if bot else 1,
                    bot_id=bot.id if bot else None,
                    customer_name=session.get("name", "WhatsApp User"),
                    customer_phone=sender,
                    party_size=session.get("reserve_size", 2),
                    reservation_date=session.get("reserve_date"),
                    reservation_time=session.get("reserve_time"),
                    status="Pending"
                )
                db.add(new_res)
                db.commit()
                db.close()
                await send_text_message(sender, "✅ Your reservation has been received! We will notify you once it is confirmed. 🥂", bot=bot)
                session["stage"] = "post_order"
                session["post_order_at"] = time.time()
            except Exception as e:
                print(f"Reservation Save Error: {e}")
                await send_text_message(sender, "Sorry, I couldn't save your reservation. Please try again later.", bot=bot)
        else:
            session["stage"] = "menu"
            await send_main_menu(sender, session.get("name", ""), lang, bot=bot)
async def _handle_flow_inner(sender, text, is_button=False, bot=None):
    session = get_session(sender, bot)
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
            session = new_session(sender, bot)
            save_session_db(sender, bot.id, session)
            stage = session["stage"]
        else:
            if is_order_status_query(text_lower):
                await handle_order_status(sender, session, lang, text, bot=bot)
                save_session_db(sender, bot.id, session)
                return
            if is_thanks(text_lower) or is_bye(text_lower):
                await send_text_message(sender, t(lang, "thanks_reply") if is_thanks(text_lower) else t(lang, "bye_reply"), bot=bot)
                save_session_db(sender, bot.id, session)
                return
            if is_menu_request(text_lower) or text_lower in ["hi", "hello", "hey", "start"]:
                session = new_session(sender, bot)
                save_session_db(sender, bot.id, session)
                stage = session["stage"]
            else:
                reply = await get_ai_response(sender, text, lang, session)
                await send_text_message(sender, reply, bot=bot)
                save_session_db(sender, bot.id, session)
                return

    if text_lower in ["restart", "reset", "start over"]:
        session = new_session(sender, bot)
        session["stage"] = "lang_select"
        save_session_db(sender, bot.id, session)
        await send_language_selection(sender, bot=bot)
        return

    # Order status query from outside ordering stages
    ordering_stages = {"items", "qty_control", "upsell_check", "upsell_combo", "confirm", "get_name", "address", "delivery", "payment", "deal_build", "bbq_sides", "repeat_confirm"}
    if is_order_status_query(text_lower) and stage not in ordering_stages:
        await handle_order_status(sender, session, lang, text, bot=bot)
        return

    # Reservation Flow Trigger
    if stage.startswith("reserve_") or text == "NEW_RESERVATION" or "book a table" in text_lower:
        if text == "NEW_RESERVATION" or "book a table" in text_lower:
            await start_reservation(sender, session, lang, bot=bot)
            return
        await handle_reservation_flow(sender, session, text, lang, bot=bot)
        return

    # Returning customer
    if stage == "returning":
        name = session.get("name", "")
        favorites = get_favorite_items(sender, bot.owner_id)
        fav_text = f"\n\nYou usually order: {', '.join(favorites)}" if favorites else ""
        session["stage"] = "returning_choice"
        # Check if they want to book or order
        await send_returning_customer_menu(sender, name, fav_text, lang, bot=bot)
        return

    # Returning customer choice
    if stage == "returning_choice":
        if text == "REPEAT_ORDER":
            session["stage"] = "repeat_confirm"
            await send_repeat_order_confirm(sender, lang)
        elif text == "NEW_RESERVATION":
            await start_reservation(sender, session, lang)
        elif text in ["NEW_ORDER", "REPEAT_ADD_MORE"]:
            session["stage"] = "menu"
            await send_main_menu(sender, session["order"], lang)
        elif text == "CHANGE_ADDRESS":
            session["stage"] = "address_update"
            await send_text_message(sender, "Sure! What's your new delivery address?", bot=bot)
        elif text == "REPEAT_CONFIRM":
            profile = get_profile_db(sender, bot.owner_id)
            history = profile.get("order_history", [])
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
                            for item_id, item in cat_data["items"].items():
                                if item["name"] == it:
                                    session["order"][item_id] = {"item": item, "qty": 1}
                if session["order"]:
                    session["stage"] = "confirm"
                    await send_order_summary(sender, session["order"], lang, bot=bot)
                else:
                    session["stage"] = "menu"
                    await send_main_menu(sender, session["order"], lang, bot=bot)
            else:
                session["stage"] = "menu"
                await send_main_menu(sender, session["order"], lang, bot=bot)
            return
        return

    # Reservation states
    if stage.startswith("reserve_"):
        await handle_reservation_flow(sender, session, text, lang)
        return

    # Menu loading - Dynamic & Multi-tenant
    MENU = get_bot_menu(bot.phone_number_id if bot else None)
    session["menu"] = MENU # Store in session for helpers

    if stage == "address_update":
        if not is_valid_address(text):
            await send_text_message(sender, t(lang, "invalid_address"), bot=bot)
            return
        session["address"] = text.strip()
        save_profile(sender, session, owner_id=bot.owner_id if bot else 1)
        await send_text_message(sender, f"Address updated! {text}", bot=bot)
        session["stage"] = "menu"
        await send_main_menu(sender, session["order"], lang)
        return

    # Language selection
    if stage == "lang_select":
        lang_map = {
            "LANG_EN": "en", "LANG_AR": "ar", "LANG_HI": "hi",
            "LANG_FR": "fr", "LANG_DE": "de", "LANG_RU": "ru",
            "LANG_ZH": "zh", "LANG_ML": "ml"
        }
        if text in lang_map:
            session["lang"] = lang_map[text]
            lang = lang_map[text]
            session["stage"] = "menu"
            await send_text_message(sender, t(lang, "greeting_welcome"), bot=bot)
            await send_main_menu(sender, session["order"], lang)
        else:
            await send_language_selection(sender, bot=bot)
        return

    # Global back to menu
    if text in ["SHOW_MENU", "BACK_MENU", "ADD_MORE"]:
        session["stage"] = "menu"
        await send_main_menu(sender, session["order"], lang)
        return

    if text == "BACK_TO_DELIVERY":
        session["stage"] = "delivery"
        session["delivery_type"] = ""
        await send_delivery_buttons(sender, session.get("name", ""), lang, bot=bot)
        return

    # Remove item
    m_remove = re.match(r"^(remove|delete)\s+([a-z0-9]+)$", text_lower)
    if m_remove:
        item_id = m_remove.group(2).upper()
        if item_id in session["order"]:
            del session["order"][item_id]
        await send_cart_view(sender, session["order"], lang)
        return

    # Category selection
    cat_map = {
        "CAT_DEALS": "deals", "CAT_FASTFOOD": "fastfood", "CAT_PIZZA": "pizza",
        "CAT_BBQ": "bbq", "CAT_FISH": "fish", "CAT_SIDES": "sides",
        "CAT_DRINKS": "drinks", "CAT_DESSERTS": "desserts",
    }
    if text in cat_map:
        session["stage"] = "items"
        session["current_cat"] = cat_map[text]
        await send_category_items(sender, cat_map[text], session["order"], lang, bot=bot)
        return

    # Deal building logic (simplified, but enough)
    if stage == "deal_build" and session.get("deal_context"):
        ctx = session["deal_context"]
        if text.startswith("DEAL_PICK_"):
            picked_id = text.replace("DEAL_PICK_", "").upper()
            _cat, picked_item = find_item(picked_id, MENU)
            if picked_item:
                ctx["picks"].append({"item_id": picked_id, "name": picked_item["name"]})
                needs = ctx["needs"]
                if len(ctx["picks"]) >= len(needs):
                    await finalize_deal(sender, session, lang)
                else:
                    next_kind = needs[len(ctx["picks"])]
                    await prompt_deal_pick(sender, session, next_kind, lang)
            return
        needs = ctx["needs"]
        if len(ctx["picks"]) < len(needs):
            await prompt_deal_pick(sender, session, needs[len(ctx["picks"])], lang)
        return

    if stage == "bbq_sides" and session.get("deal_context"):
        ctx = session["deal_context"]
        if text.startswith("SIDE_"):
            side_key = text.replace("SIDE_", "")
            side_names = {"MAC": "Mac & Cheese", "FRIES": "Fries", "SLAW": "Coleslaw", "SALAD": "Caesar Salad"}
            if side_key in side_names:
                ctx.setdefault("sides", []).append(side_names[side_key])
                if len(ctx["sides"]) >= ctx.get("sides_needed", 2):
                    await finalize_bbq_sides(sender, session, lang)
                else:
                    await prompt_bbq_sides(sender, session, lang)
            return
        await prompt_bbq_sides(sender, session, lang)
        return

    # Add item to cart
    if text.startswith("ADD_"):
        item_id = text.replace("ADD_", "").upper()
        MENU = session.get("menu", {})
        cat, found_item = find_item(item_id, MENU)
        if not found_item:
            return

        if stage in {"upsell_combo", "upsell_check"}:
            session.pop("_pending_upsell_type", None)
            session["stage"] = "items"
            stage = "items"

        # 1. Check for Deal Building Triggers (DL2-DL6)
        if item_id in DEAL_RULES and DEAL_RULES[item_id]["type"] == "pick":
            session["deal_context"] = {
                "deal_id": item_id,
                "deal_item": found_item,
                "needs": DEAL_RULES[item_id]["needs"],
                "picks": []
            }
            session["stage"] = "deal_build"
            await prompt_deal_pick(sender, session, session["deal_context"]["needs"][0], lang)
            return

        # 2. Check for BBQ Sides Trigger
        if item_id in BBQ_NEEDS_SIDES:
            session["deal_context"] = {
                "target_item_id": item_id,
                "sides_needed": 2,
                "sides": []
            }
            # Add base item first
            session["order"][item_id] = {"item": found_item, "qty": 1}
            session["stage"] = "bbq_sides"
            await prompt_bbq_sides(sender, session, lang)
            return

        # 3. Handle DL1 (Combo) logic
        if item_id == "DL1":
            # Check if burger already exists
            has_burger = any(k.startswith("FF") for k in session["order"])
            if not has_burger:
                session["pending_combo"] = True
                await send_text_message(sender, "Great choice! First, please *pick a burger* to include in your combo.", bot=bot)
                session["stage"] = "items"
                session["current_cat"] = "fastfood"
                await send_category_items(sender, "fastfood", session["order"], lang, bot=bot)
                return

        # Simple add logic
        if item_id in session["order"]:
            session["order"][item_id]["qty"] += 1
        else:
            session["order"][item_id] = {"item": found_item, "qty": 1}
        
        session["last_added"] = item_id
        session["stage"] = "qty_control"

        # 4. Upsell Triggers
        if item_id.startswith("FF"): # Burger added
            # Check if combo or side/drink already exists
            has_combo = "DL1" in session["order"]
            has_side = any(k.startswith("SD") or k.startswith("DRK") for k in session["order"])
            if not has_combo and not has_side and "combo" not in session.get("upsell_shown_for", set()):
                session.setdefault("upsell_shown_for", set()).add("combo")
                session["_pending_upsell_type"] = "combo"
                await send_quick_combo_upsell(sender, lang)
                return

        if item_id.startswith("PIZ"): # Pizza added
            has_wings = "SD2" in session["order"] # Assuming SD2 is wings
            if not has_wings and "wings" not in session.get("upsell_shown_for", set()):
                session.setdefault("upsell_shown_for", set()).add("wings")
                session["_pending_upsell_type"] = "wings"
                await send_quick_upsell(sender, "SD2", "Want to add some crispy Buffalo Wings for only $6.99? 🍗", lang)
                return

        # Auto-add DL1 if pending
        if session.get("pending_combo") and item_id.startswith("FF"):
            session.pop("pending_combo", None)
            deals_cat = MENU.get("deals", {"items": {}})
            dl1_item = deals_cat.get("items", {}).get("DL1")
            if dl1_item:
                session["order"]["DL1"] = {"item": dl1_item, "qty": 1}
                await send_text_message(sender, "✅ Combo deal activated!")

        await send_qty_control(sender, item_id, found_item, session["order"], lang, bot=bot)
        return

    # Quantity controls
    if text in ["QTY_PLUS", "QTY_MINUS"]:
        item_id = session.get("last_added")
        if item_id and item_id in session["order"]:
            if text == "QTY_PLUS":
                session["order"][item_id]["qty"] += 1
            else:
                if session["order"][item_id]["qty"] > 1:
                    session["order"][item_id]["qty"] -= 1
                else:
                    del session["order"][item_id]
                    await send_text_message(sender, f"Removed {item_id}")
                    session["stage"] = "menu"
                    await send_main_menu(sender, session["order"], lang)
                    return
            if item_id in session["order"]:
                await send_qty_control(sender, item_id, session["order"][item_id]["item"], session["order"], lang)
        else:
            session["stage"] = "menu"
            await send_main_menu(sender, session["order"], lang)
        return

    # Upsell skip
    if text == "SKIP_UPSELL":
        ctx_type = session.get("_pending_upsell_type", "generic")
        session["upsell_declined_types"].add(ctx_type)
        session.pop("_pending_upsell_type", None)
        last = session.get("last_added")
        session["stage"] = "qty_control"
        if last and last in session["order"]:
            await send_qty_control(sender, last, session["order"][last]["item"], session["order"], lang)
        else:
            await send_main_menu(sender, session["order"], lang)
        return

    if text == "ADD_COMBO_DL1":
        try:
            MENU = session.get("menu", {})
            deals_cat = MENU.get("deals", {"items": {}})
            deal_item = deals_cat.get("items", {}).get("DL1")
            if not deal_item:
                deal_item = {"name": "Burger Combo", "price": 4.99}
                
            if "DL1" in session["order"]:
                session["order"]["DL1"]["qty"] += 1
            else:
                session["order"]["DL1"] = {"item": deal_item, "qty": 1}
            session.pop("_pending_upsell_type", None)
            last = session.get("last_added")
            session["stage"] = "qty_control"
            if last and last in session["order"]:
                await send_qty_control(sender, last, session["order"][last]["item"], session["order"], lang)
            else:
                await send_cart_view(sender, session["order"], lang)
            return
        except:
            await send_text_message(sender, "Could not add combo.", bot=bot)
            return

    # Checkout
    if text == "CHECKOUT":
        if session["order"]:
            # Dessert upsell check
            MENU = session.get("menu", {})
            has_dessert = any(k.startswith("DS") for k in session["order"])
            if has_dessert or "dessert" in session.get("upsell_declined_types", set()):
                session["stage"] = "confirm"
                await send_order_summary(sender, session["order"], lang, bot=bot)
            else:
                session["stage"] = "upsell_check"
                # Check if dessert category exists
                dessert_cat = MENU.get("desserts")
                if dessert_cat:
                    await send_dessert_upsell(sender, session["order"], lang, bot=bot)
                else:
                    session["stage"] = "confirm"
                    await send_order_summary(sender, session["order"], lang, bot=bot)
        else:
            await send_text_message(sender, t(lang, "cart_empty"), bot=bot)
            await send_main_menu(sender, session["order"], lang, bot=bot)
        return

    if text == "VIEW_CART":
        await send_cart_view(sender, session["order"], lang, bot=bot)
        save_session_db(sender, bot.id, session)
        return

    if text in ["YES_UPSELL", "NO_UPSELL"]:
        if text == "YES_UPSELL":
            session["stage"] = "items"
            session["current_cat"] = "desserts"
            await send_category_items(sender, "desserts", session["order"], lang, bot=bot)
        else:
            if "dessert" not in session["upsell_declined_types"]:
                session["upsell_declined_types"].append("dessert")
            session["stage"] = "confirm"
            await send_order_summary(sender, session["order"], lang, bot=bot)
        save_session_db(sender, bot.id, session)
        return

    if text == "CONFIRM_ORDER":
        if session.get("name"):
            session["stage"] = "delivery"
            await send_delivery_buttons(sender, session["name"], lang, bot=bot)
        else:
            session["stage"] = "get_name"
            await send_text_message(sender, t(lang, "name_ask"), bot=bot)
        return

    if text == "CANCEL_ORDER":
        session = new_session(sender, bot)
        await send_text_message(sender, t(lang, "cancelled"), bot=bot)
        save_session_db(sender, bot.id, session)
        return

    if text == "DINE_IN":
        session["delivery_type"] = "dine_in"
        session["stage"] = "payment"
        await send_text_message(sender, f"🍽️ Table {session.get('table_number','?')} noted. Choose payment:", bot=bot)
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

    # Payment
    if text in ["CASH", "CARD_STRIPE", "APPLE_PAY"]:
        payment_map = {"CASH": t(lang, "cash"), "CARD_STRIPE": t(lang, "card"), "APPLE_PAY": t(lang, "apple_pay")}
        session["payment"] = payment_map[text]

        if text == "CARD_STRIPE":
            total = get_order_total(session["order"])
            tax = total * 0.08
            delivery_charge = get_delivery_fee(total, session.get("delivery_type"))
            grand_total = total + tax + delivery_charge
            order_id = str(int(time.time()))
            saved_orders[order_id] = {"session": session.copy(), "sender": sender, "timestamp": time.time()}
            saved_orders[order_id]["order"] = session["order"]
            saved_orders[order_id]["customer_name"] = session.get("name", "")
            payment_url = await create_stripe_checkout_session(order_id, grand_total)
            if payment_url:
                await send_text_message(sender, f"💳 Pay here:\n{payment_url}", bot=bot)
            else:
                await send_text_message(sender, "❌ Payment link failed. Try another method.")
            return

        # Cash / Apple Pay
        order_id = await send_order_confirmed(sender, session, lang, bot=bot)
        session["order_id"] = order_id
        session["just_confirmed"] = True
        session["just_confirmed_at"] = time.time()
        
        # Notify Manager
        await notify_manager(sender, session, order_id, bot=bot)
        
        save_profile(sender, session, owner_id=bot.owner_id if bot else 1)
        add_to_order_history(sender, order_id, session["order"], owner_id=bot.owner_id if bot else 1)
        session["stage"] = "post_order"
        session["post_order_at"] = time.time()
        session["order"] = {}
        session["last_added"] = None
        return

    # Get name
    if stage == "get_name":
        if not is_valid_name(text):
            await send_text_message(sender, t(lang, "invalid_name"), bot=bot)
            return
        session["name"] = text.strip().title()[:30]
        session["stage"] = "delivery"
        await send_delivery_buttons(sender, session["name"], lang, bot=bot)
        return

    # Address
    if stage == "address":
        if not is_valid_address(text):
            await send_text_message(sender, t(lang, "invalid_address"), bot=bot)
            return
        session["address"] = text.strip()
        session["stage"] = "payment"
        await send_text_message(sender, t(lang, "address_saved"), bot=bot)
        await send_payment_buttons(sender, session.get("name", ""), lang, bot=bot)
        return

    # Greetings
    if text_lower in ["hi", "hello", "hey", "start", "salam", "hola"]:
        if stage == "lang_select":
            await send_language_selection(sender, bot=bot)
        else:
            session["stage"] = "menu"
            await send_text_message(sender, t(lang, "greeting_welcome"), bot=bot)
            await send_main_menu(sender, session["order"], lang)
        return

    if is_menu_request(text_lower):
        session["stage"] = "menu"
        await send_main_menu(sender, session["order"], lang)
        return

    # AI fallback
    session["conversation"].append({"role": "user", "content": text})
    reply = await get_ai_response(sender, text, lang, session)
    session["conversation"].append({"role": "assistant", "content": reply})
    session["conversation"] = session["conversation"][-8:]
    await send_text_message(sender, reply)
