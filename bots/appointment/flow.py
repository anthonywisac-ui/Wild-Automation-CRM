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

    if stage == "greeting":
        import json
        config = {}
        try: config = json.loads(bot.config_json) if bot.config_json else {}
        except: pass
        
        services = config.get("services", ["Consultation", "Maintenance", "General Inquiry"])
        options = "\n".join([f"- {s}" for s in services])
        
        msg = f"📅 Hello! Welcome to {bot.name} Booking Assistant.\n\nWhat service would you like to book?\n{options}"
        await send_text_message_v2(sender, msg, bot)
        session["stage"] = "select_service"

    elif stage == "select_service":
        session["service"] = text
        msg = "Great! Please enter your preferred *Date* (e.g., Tomorrow, Monday, or Oct 25th)."
        await send_text_message_v2(sender, msg, bot)
        session["stage"] = "select_date"

    elif stage == "select_date":
        session["date"] = text
        msg = "What *Time* works best for you? (e.g., 10 AM, 2:30 PM)"
        await send_text_message_v2(sender, msg, bot)
        session["stage"] = "select_time"

    elif stage == "select_time":
        session["time"] = text
        msg = f"📝 *Confirm Booking:*\n🔹 Service: {session['service']}\n📅 Date: {session['date']}\n⏰ Time: {session['time']}\n\nType *Confirm* to book!"
        await send_text_message_v2(sender, msg, bot)
        session["stage"] = "confirm"

    elif stage == "confirm":
        if "confirm" in text_lower:
            msg = "✅ Appointment booked! You will receive a confirmation shortly. Thank you!"
            await send_text_message_v2(sender, msg, bot)
            session["stage"] = "completed"
        else:
            msg = "Booking cancelled. Type anything to start again."
            await send_text_message_v2(sender, msg, bot)
            session = {"stage": "greeting"}
            
    save_session(sender, bot.id, session, db)
