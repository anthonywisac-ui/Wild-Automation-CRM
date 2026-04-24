import os
import json
import logging
import time
from datetime import datetime
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session
from db import get_db, WhatsappBot, WebhookEvent, ChatHistory, Contact, SessionLocal, User
from ai_utils import get_ai_response

router = APIRouter(tags=["WhatsApp Webhook"])
logger = logging.getLogger(__name__)

async def trigger_vapi_outbound_call(sender_phone: str, bot: WhatsappBot, db: Session):
    """Triggers a Vapi outbound call to the WhatsApp user"""
    vapi_agent_id = bot.vapi_agent_id
    if not vapi_agent_id:
        return False
    
    # Get owner's API key
    owner = db.query(User).filter(User.id == bot.owner_id).first()
    vapi_key = os.getenv("VAPI_API_KEY") # Default fallback
    
    # Try to find a Vapi agent record for this owner to get the key
    from db import VapiAgent
    agent_record = db.query(VapiAgent).filter(VapiAgent.vapi_agent_id == vapi_agent_id).first()
    if agent_record and agent_record.vapi_api_key:
        vapi_key = agent_record.vapi_api_key

    if not vapi_key:
        return False

    url = "https://api.vapi.ai/call/phone"
    headers = {"Authorization": f"Bearer {vapi_key}", "Content-Type": "application/json"}
    payload = {
        "assistantId": vapi_agent_id,
        "customer": {"number": sender_phone},
        "phoneNumberId": agent_record.phone_number_id if agent_record else None
    }

    import aiohttp
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers) as resp:
            return resp.status == 201

# ========== Simple Rate Limiter (per sender) ==========
_rate_limit: dict = {}  # {sender: [timestamps]}

def _is_rate_limited(sender: str, max_msgs: int = 10, window_secs: int = 10) -> bool:
    """Block senders sending > max_msgs in window_secs seconds."""
    now = time.time()
    times = _rate_limit.get(sender, [])
    times = [t for t in times if now - t < window_secs]
    times.append(now)
    _rate_limit[sender] = times
    return len(times) > max_msgs

# ========== Webhook Verification ==========
@router.get("/webhook")
async def verify_webhook(request: Request):
    params = dict(request.query_params)
    verify_token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    GLOBAL_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "your_verify_token_here")

    if verify_token == GLOBAL_VERIFY_TOKEN:
        return PlainTextResponse(challenge)

    db = next(get_db())
    bot = db.query(WhatsappBot).filter(WhatsappBot.verify_token == verify_token).first()
    db.close()
    if bot:
        return PlainTextResponse(challenge)

    return PlainTextResponse("Forbidden", status_code=403)

# ========== Main Webhook Handler ==========
@router.post("/webhook")
async def whatsapp_webhook(request: Request, db: Session = Depends(get_db)):
    data = await request.json()

    try:
        if not data.get("entry"):
            return {"status": "ok"}

        entry_data = data["entry"][0]
        if not entry_data.get("changes"):
            return {"status": "ok"}

        value = entry_data["changes"][0]["value"]
        metadata = value.get("metadata", {})
        phone_number_id = metadata.get("phone_number_id", "")

        # ── 1. Identify the Bot ──────────────────────────────────────────────
        bot = db.query(WhatsappBot).filter(WhatsappBot.phone_number_id == phone_number_id).first()
        if not bot:
            logger.warning(f"No bot found for phone_number_id: {phone_number_id}")
            return {"status": "ok"}

        # ── 2. Log webhook event ─────────────────────────────────────────────
        new_event = WebhookEvent(user_id=bot.owner_id, type="whatsapp")
        new_event.payload = data
        db.add(new_event)

        # ── 3. Process Messages ──────────────────────────────────────────────
        if "messages" not in value:
            db.commit()
            return {"status": "ok"}

        message = value["messages"][0]
        sender = message.get("from", "")
        msg_type = message.get("type", "")

        if not sender:
            db.commit()
            return {"status": "ok"}

        # Rate limiting
        if _is_rate_limited(sender):
            logger.warning(f"Rate limit hit for sender: {sender}")
            db.commit()
            return {"status": "ok"}

        # ── Extract message content (text OR interactive button/list reply) ──
        user_msg = None
        is_button = False

        if msg_type == "text":
            user_msg = message["text"]["body"].strip()

        elif msg_type == "interactive":
            is_button = True
            interactive = message.get("interactive", {})
            itype = interactive.get("type", "")
            if itype == "button_reply":
                user_msg = interactive["button_reply"]["id"]
            elif itype == "list_reply":
                user_msg = interactive["list_reply"]["id"]

        elif msg_type == "button":
            # Template button reply
            is_button = True
            user_msg = message.get("button", {}).get("payload", "")

        if not user_msg:
            db.commit()
            return {"status": "ok"}

        # ── VAPI HANDOFF CHECK ──────────────────────────────────────────────
        handoff_keywords = ["call me", "talk to human", "speak with someone", "voice call"]
        if any(k in user_msg.lower() for k in handoff_keywords) or user_msg == "TALK_TO_HUMAN":
            if bot.vapi_agent_id:
                success = await trigger_vapi_outbound_call(sender, bot, db)
                if success:
                    from whatsapp_handlers import send_text_message_v2
                    await send_text_message_v2(sender, "📞 I'm initiating a voice call to you right now! Please pick up.", bot)
                    db.add(ChatHistory(user_id=bot.owner_id, customer_phone=sender, role="assistant", content="[vapi_handoff_triggered]"))
                    db.commit()
                    return {"status": "ok"}
                else:
                    logger.warning(f"Vapi handoff failed for bot {bot.name}")

        # ── Auto-create contact record ───────────────────────────────────────
        contact = db.query(Contact).filter(
            Contact.phone == sender,
            Contact.owner_id == bot.owner_id
        ).first()
        if not contact:
            contact = Contact(
                owner_id=bot.owner_id, phone=sender,
                first_name="WhatsApp User", source="WhatsApp"
            )
            db.add(contact)
            db.commit()

        # ── Log incoming message ─────────────────────────────────────────────
        db.add(ChatHistory(
            user_id=bot.owner_id, customer_phone=sender,
            role="user", content=user_msg
        ))
        db.commit()

        # ── 4. Route to correct handler ──────────────────────────────────────
        if bot.forwarding_url:
            # ── FORWARDING MODE: send raw payload to external engine (e.g. Railway) ──
            import aiohttp
            async with aiohttp.ClientSession() as http_session:
                try:
                    async with http_session.post(
                        bot.forwarding_url, json=data, timeout=aiohttp.ClientTimeout(total=10)
                    ) as resp:
                        logger.info(f"Forwarded to {bot.forwarding_url}, status: {resp.status}")
                except Exception as fe:
                    logger.error(f"Forwarding failed for bot {bot.name}: {fe}")

        elif bot.bot_type == "real_estate":
            # ── REAL ESTATE ENGINE ──────────────────────────────────────────
            try:
                from bots.real_estate.flow import handle_flow as re_flow
                await re_flow(sender, user_msg, bot, db)
                db.add(ChatHistory(user_id=bot.owner_id, customer_phone=sender, role="assistant", content="[real_estate_flow]"))
                db.commit()
            except Exception as e:
                logger.error(f"Real Estate flow error: {e}")

        elif bot.bot_type == "appointment":
            # ── APPOINTMENT ENGINE ──────────────────────────────────────────
            try:
                from bots.appointment.flow import handle_flow as appt_flow
                await appt_flow(sender, user_msg, bot, db)
                db.add(ChatHistory(user_id=bot.owner_id, customer_phone=sender, role="assistant", content="[appointment_flow]"))
                db.commit()
            except Exception as e:
                logger.error(f"Appointment flow error: {e}")

        elif bot.bot_type == "restaurant":
            # ── RESTAURANT FLOW ENGINE ──────────────────────────────────────
            try:
                from bots.restaurant.flow import handle_flow
                await handle_flow(sender, user_msg, is_button=is_button, bot=bot)

                db.add(ChatHistory(
                    user_id=bot.owner_id, customer_phone=sender,
                    role="assistant", content="[restaurant_flow]"
                ))
                db.commit()

            except Exception as e:
                import traceback
                logger.error(f"Restaurant flow error: {e}\n{traceback.format_exc()}")

        else:
            # ── LOCAL AI MODE: simple AI reply for non-restaurant bots ──────
            reply = await get_ai_response(sender, user_msg, bot, db)

            from whatsapp_handlers import send_text_message_v2
            await send_text_message_v2(sender, reply, bot)

            db.add(ChatHistory(
                user_id=bot.owner_id, customer_phone=sender,
                role="assistant", content=reply
            ))
            db.commit()

    except Exception as e:
        import traceback
        logger.error(f"WhatsApp Webhook Error: {e}\n{traceback.format_exc()}")

    return {"status": "ok"}

# ========== QR Table Entry Endpoint ==========
@router.get("/qr/{bot_id}/{table_number}")
async def qr_table_entry(bot_id: int, table_number: str, db: Session = Depends(get_db)):
    """
    QR code landing — customer scans table QR, gets WhatsApp deep-link.
    Stores table context so next message from that customer sets table_number.
    """
    bot = db.query(WhatsappBot).filter(WhatsappBot.id == bot_id).first()
    if not bot:
        raise HTTPException(404, "Bot not found")

    # Store table number pending for this bot (picked up on first message)
    # The restaurant flow reads table_number from the URL and sets it in session
    wa_link = f"https://wa.me/{bot.phone_number_id}?text=TABLE_{table_number}"
    return {
        "message": f"Scan to order from Table {table_number}",
        "whatsapp_link": wa_link,
        "bot": bot.name,
        "table": table_number
    }
