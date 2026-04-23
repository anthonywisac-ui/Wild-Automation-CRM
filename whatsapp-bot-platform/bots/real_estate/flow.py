import time
import logging
from whatsapp_handlers import send_text_message_v2

# In-memory session for simplicity (in production, use DB SessionState like restaurant)
sessions = {}

async def handle_flow(sender, text, bot, db):
    if sender not in sessions:
        sessions[sender] = {"stage": "greeting"}
    
    session = sessions[sender]
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
        msg = "What type of property are you looking for?\n\n1. House 🏡\n2. Apartment 🏢\n3. Land 🌳\n4. Commercial 🏭"
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
        # Log lead to CRM (Contact already exists via router)
        session["stage"] = "completed"
