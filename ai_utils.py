import aiohttp
import os
import json
import logging
from datetime import datetime
from sqlalchemy.orm import Session
from db import WhatsappBot, ChatHistory
from session import SharedSession

logger = logging.getLogger(__name__)

# ==============================
# NICHE PROMPTS
# ==============================
NICHE_PROMPTS = {
    "restaurant": "You are a professional food ordering assistant for {business_name}. Your goal is to help customers browse the menu and take their orders. Be friendly and efficient.",
    "salon": "You are an appointment booking assistant for {business_name}. Help customers book services like haircuts, massages, or spa treatments. Check their preferred time and date.",
    "gym": "You are a fitness consultant and booking agent for {business_name}. Help customers with membership info and booking personal training sessions.",
    "lawyer": "You are a legal intake assistant for {business_name}. Gather basic information about the client's case and schedule a consultation. Do not give legal advice.",
    "real_estate": "You are a real estate lead assistant for {business_name}. Help users find properties, schedule viewings, and gather their budget and location preferences.",
    "hvac": "You are a service coordinator for {business_name}. Help customers book repair or maintenance services for their home systems.",
    "agency": "You are a consultant for {business_name}. Help users with travel bookings or insurance quotes by gathering their requirements.",
    "support": "You are a helpful customer support agent for {business_name}. Answer questions about services, hours, and location."
}

def get_niche_prompt(bot: WhatsappBot):
    base = NICHE_PROMPTS.get(bot.bot_type, NICHE_PROMPTS["support"])
    custom = bot.system_prompt or ""
    return f"{base.format(business_name=bot.business_name)}\n\n{custom}"

# ==============================
# MAIN AI LOGIC
# ==============================
async def get_ai_response(sender: str, user_message: str, bot: WhatsappBot, db: Session):
    # 1. Fetch Chat History (last 10 messages)
    history = db.query(ChatHistory).filter(ChatHistory.customer_phone == sender, ChatHistory.user_id == bot.owner_id).order_by(ChatHistory.created_at.desc()).limit(10).all()
    
    messages = [{"role": "system", "content": get_niche_prompt(bot)}]
    
    # Add history in chronological order
    for msg in reversed(history):
        messages.append({"role": msg.role, "content": msg.content})
        
    # Add current message
    messages.append({"role": "user", "content": user_message})
    
    # 2. Determine Provider & Key
    # Priority: Bot-specific key -> User-level key -> Global Env key
    provider = bot.ai_provider or "groq"
    api_key = bot.ai_api_key or ""
    
    # If bot doesn't have a key, try the owner's key
    if not api_key:
        from db import User
        owner = db.query(User).filter(User.id == bot.owner_id).first()
        if owner:
            if provider == "groq": api_key = owner.groq_api_key
            elif provider == "gemini": api_key = owner.gemini_api_key
            elif provider == "openai": api_key = owner.openai_api_key
            
    # Fallback to Global Env keys if still empty
    if not api_key:
        if provider == "groq": api_key = os.getenv("GROQ_API_KEY")
        elif provider == "gemini": api_key = os.getenv("GEMINI_API_KEY")
        elif provider == "openai": api_key = os.getenv("OPENAI_API_KEY")

    if not api_key:
        return "System configuration error: Missing API Key."

    # 3. Call AI
    try:
        if provider == "groq":
            return await call_groq_api(messages, api_key)
        elif provider == "gemini":
            return await call_gemini_api(user_message, messages, api_key)
        elif provider == "openai":
            return await call_openai_api(messages, api_key)
    except Exception as e:
        logger.error(f"AI Call failed ({provider}): {str(e)}")
        return "I'm having trouble processing that right now. Please try again in a moment."

async def call_groq_api(messages, api_key):
    url = "https://api.groq.com/openai/v1/chat/completions"
    session = await SharedSession.get_session()
    async with session.post(
        url,
        headers={"Authorization": f"Bearer {api_key}"},
        json={"model": "llama-3.1-8b-instant", "messages": messages}
    ) as res:
        data = await res.json()
        return data["choices"][0]["message"]["content"]

async def call_gemini_api(user_message, messages, api_key):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
    # Convert messages to Gemini format
    contents = []
    for m in messages:
        role = "user" if m["role"] == "user" else "model"
        contents.append({"role": role, "parts": [{"text": m["content"]}]})
    
    payload = {"contents": contents}
    session = await SharedSession.get_session()
    async with session.post(url, json=payload) as res:
        data = await res.json()
        if "candidates" in data:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        return "I'm sorry, I couldn't generate a response."

async def call_openai_api(messages, api_key):
    url = "https://api.openai.com/v1/chat/completions"
    session = await SharedSession.get_session()
    async with session.post(
        url,
        headers={"Authorization": f"Bearer {api_key}"},
        json={"model": "gpt-3.5-turbo", "messages": messages}
    ) as res:
        data = await res.json()
        return data["choices"][0]["message"]["content"]