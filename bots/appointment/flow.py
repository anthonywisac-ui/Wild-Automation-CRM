import time
from whatsapp_handlers import send_text_message_v2

sessions = {}

async def handle_flow(sender, text, bot, db):
    if sender not in sessions:
        sessions[sender] = {"stage": "greeting"}
    
    session = sessions[sender]
    stage = session["stage"]
    text_lower = text.lower().strip()

    if stage == "greeting":
        msg = f"📅 Hello! Welcome to {bot.name} Booking Assistant.\n\nWhat service would you like to book?\n- Consultation\n- Maintenance\n- General Inquiry"
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
            sessions.pop(sender)
