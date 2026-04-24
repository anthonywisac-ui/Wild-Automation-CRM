import json
from db import SessionState
from whatsapp_handlers import send_text_message_v2

def get_session(sender, bot_id, db):
    session_record = db.query(SessionState).filter(SessionState.customer_phone == sender, SessionState.bot_id == bot_id).first()
    if session_record: return json.loads(session_record.state_json)
    return {"stage": "greeting"}

def save_session(sender, bot_id, state, db):
    session_record = db.query(SessionState).filter(SessionState.customer_phone == sender, SessionState.bot_id == bot_id).first()
    if not session_record:
        session_record = SessionState(customer_phone=sender, bot_id=bot_id)
        db.add(session_record)
    session_record.state_json = json.dumps(state)
    db.commit()

async def handle_flow(sender, text, bot, db):
    session = get_session(sender, bot.id, db)
    stage = session["stage"]
    text_lower = text.lower().strip()

    if "restart" in text_lower or "menu" in text_lower:
        stage = "greeting"

    if stage == "greeting":
        msg = f"🏠 Welcome to {bot.name}! I can help you find your dream home.\n\nAre you looking to *Buy* or *Rent*?"
        await send_text_message_v2(sender, msg, bot)
        session["stage"] = "search_type"

    elif stage == "search_type":
        session["search_type"] = text
        # Load property types from config (Fixing Hardcoded logic)
        import json
        config = {}
        try: config = json.loads(bot.config_json) if bot.config_json else {}
        except: pass
        
        props = config.get("property_types", ["House 🏡", "Apartment 🏢", "Land 🌳", "Commercial 🏭"])
        options = "\n".join([f"{i+1}. {p}" for i, p in enumerate(props)])
        
        msg = f"What type of property are you looking for?\n\n{options}"
        await send_text_message_v2(sender, msg, bot)
        session["stage"] = "property_type"

    elif stage == "property_type":
        session["property_type"] = text
        msg = "What is your approximate budget range? (e.g., $200k - $500k)"
        await send_text_message_v2(sender, msg, bot)
        session["stage"] = "budget"

    elif stage == "budget":
        session["budget"] = text
        msg = "Excellent! Please provide your *Email* so one of our agents can send you a curated list of matching properties."
        await send_text_message_v2(sender, msg, bot)
        session["stage"] = "collect_lead"

    elif stage == "collect_lead":
        session["email"] = text
        msg = "Thank you! 🚀 We have received your request. An agent will contact you shortly with the best options."
        await send_text_message_v2(sender, msg, bot)
        
        # PERSIST LEAD (Fixing Ghost Lead flaw)
        from db import Contact
        contact = db.query(Contact).filter(Contact.phone == sender, Contact.owner_id == bot.owner_id).first()
        if contact:
            contact.email = text
            contact.notes = f"Real Estate Inquiry: {session.get('property_type')} for {session.get('search_type')} with budget {session.get('budget')}"
            db.commit()
            
        session["stage"] = "completed"
    
    save_session(sender, bot.id, session, db)
