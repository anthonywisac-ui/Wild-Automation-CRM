import json
import time
from datetime import datetime
from db import SessionLocal, SessionState, Contact, WhatsappBot

# Persistence wrappers for the restaurant engine
def get_session_db(sender, bot_id):
    db = SessionLocal()
    try:
        session_record = db.query(SessionState).filter(
            SessionState.sender_number == sender,
            SessionState.bot_id == bot_id
        ).first()
        if session_record:
            return json.loads(session_record.state_json)
        return None
    finally:
        db.close()

def save_session_db(sender, bot_id, state_dict):
    db = SessionLocal()
    try:
        session_record = db.query(SessionState).filter(
            SessionState.sender_number == sender,
            SessionState.bot_id == bot_id
        ).first()
        if not session_record:
            session_record = SessionState(sender_number=sender, bot_id=bot_id)
            db.add(session_record)
        
        session_record.state_json = json.dumps(state_dict)
        session_record.updated_at = datetime.utcnow()
        db.commit()
    finally:
        db.close()

def get_profile_db(sender, owner_id):
    db = SessionLocal()
    try:
        contact = db.query(Contact).filter(Contact.phone == sender, Contact.owner_id == owner_id).first()
        if contact and contact.metadata_json:
            return json.loads(contact.metadata_json)
        return {"name": contact.first_name if contact else "", "address": "", "lang": "en", "order_history": []}
    finally:
        db.close()

def save_profile(sender, session, owner_id=None):
    if not owner_id: return
    db = SessionLocal()
    try:
        contact = db.query(Contact).filter(Contact.phone == sender, Contact.owner_id == owner_id).first()
        if not contact:
            contact = Contact(phone=sender, owner_id=owner_id, source="WhatsApp")
            db.add(contact)
        
        profile = json.loads(contact.metadata_json) if contact.metadata_json else {"order_history": []}
        profile.update({
            "name": session.get("name", contact.first_name),
            "address": session.get("address", ""),
            "lang": session.get("lang", "en"),
            "delivery_type": session.get("delivery_type", ""),
            "payment": session.get("payment", ""),
        })
        contact.first_name = profile["name"]
        contact.metadata_json = json.dumps(profile)
        db.commit()
    finally:
        db.close()

def add_to_order_history(sender, order_id, order_items, owner_id):
    db = SessionLocal()
    try:
        contact = db.query(Contact).filter(Contact.phone == sender, Contact.owner_id == owner_id).first()
        if not contact: return
        
        profile = json.loads(contact.metadata_json) if contact.metadata_json else {"order_history": []}
        if "order_history" not in profile: profile["order_history"] = []
        
        profile["order_history"].append({
            "order_id": order_id,
            "items": [{"item_id": k, "name": v["item"]["name"], "qty": v["qty"]} for k, v in order_items.items()],
            "timestamp": time.time()
        })
        profile["order_history"] = profile["order_history"][-5:]
        contact.metadata_json = json.dumps(profile)
        db.commit()
    finally:
        db.close()

def get_favorite_items(sender, owner_id):
    profile = get_profile_db(sender, owner_id)
    history = profile.get("order_history", [])
    if not history: return []
    item_counts = {}
    for order in history:
        for item in order.get("items", []):
            name = item.get("name") if isinstance(item, dict) else item
            if name: item_counts[name] = item_counts.get(name, 0) + 1
    return [i for i, c in sorted(item_counts.items(), key=lambda x: x[1], reverse=True)[:3]]

# ========== Dynamic Menu Loader ==========
def get_bot_menu(phone_number_id=None):
    """Fetch menu from DB config_json"""
    from .menu_data import MENU as DEFAULT_MENU
    db = SessionLocal()
    try:
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
    finally:
        db.close()

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

def get_session(sender, bot=None):
    session = get_session_db(sender, bot.id if bot else None)
    if not session:
        session = new_session(sender, bot)
    return session

# Legacy dicts kept as empty to prevent import errors during transition
customer_sessions = {}
customer_profiles = {}
customer_order_lookup = {}
saved_orders = {}
manager_pending = {}