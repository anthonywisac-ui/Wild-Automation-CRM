import os
import json
import logging
import time
import asyncio
from datetime import datetime
from fastapi import APIRouter, Request, Depends, HTTPException, BackgroundTasks
from fastapi.responses import PlainTextResponse, HTMLResponse
from sqlalchemy.orm import Session
from db import get_db, WhatsappBot, WebhookEvent, ChatHistory, Contact, SessionLocal, User, log_bot_event
from ai_utils import get_ai_response
from session import SharedSession
from utils import truncate_title

router = APIRouter(tags=["WhatsApp Webhook"])
logger = logging.getLogger(__name__)

BRIDGE_INTERNAL_SECRET = os.getenv("BRIDGE_INTERNAL_SECRET", "")

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

    session = await SharedSession.get_session()
    async with session.post(url, json=payload, headers=headers) as resp:
        return resp.status == 201

# ========== Multi-Bot Routing Session ==========
_bot_routing: dict = {}   # {sender: {"bot_id": int, "expires": float}}
_ROUTING_TTL = 7200       # 2 hours

def _get_routed_bot(sender: str, bots: list):
    entry = _bot_routing.get(sender)
    if entry and entry["expires"] > time.time():
        return next((b for b in bots if b.id == entry["bot_id"]), None)
    return None

def _set_routed_bot(sender: str, bot_id: int):
    _bot_routing[sender] = {"bot_id": bot_id, "expires": time.time() + _ROUTING_TTL}

async def _send_bot_selector(sender: str, bots: list, cred_bot):
    from bots.restaurant.whatsapp_handlers import send_list_message
    rows = [{"id": f"SELECT_BOT_{b.id}",
             "title": truncate_title(b.business_name or b.name, 24),
             "description": (b.bot_type or "restaurant").title()} for b in bots[:10]]
    await send_list_message(
        sender, "🍽️ Welcome!",
        "We have multiple restaurants. Choose one to start ordering:",
        "Our Restaurants", "Browse Menus",
        [{"title": "Available Restaurants", "rows": rows}],
        bot=cred_bot
    )

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

    GLOBAL_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "token123")

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
async def whatsapp_webhook(request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
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

        # ── 1. Identify the Bot (supports multi-bot on same number) ─────────
        bots = db.query(WhatsappBot).filter(
            WhatsappBot.phone_number_id == phone_number_id,
            WhatsappBot.status != "deleted"
        ).all()
        if not bots:
            logger.warning(f"No bot found for phone_number_id: {phone_number_id}")
            return {"status": "ok"}
        bot = bots[0]  # will be overridden below if multi-bot

        # ── 2. Background Logging ─────────────────────────────────────────────
        def log_webhook_async(owner_id, payload):
            db_local = SessionLocal()
            try:
                new_event = WebhookEvent(user_id=owner_id, type="whatsapp")
                new_event.payload_json = json.dumps(payload)
                db_local.add(new_event)
                db_local.commit()
            finally:
                db_local.close()

        background_tasks.add_task(log_webhook_async, bot.owner_id, data)

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

        elif msg_type == "order":
            # WhatsApp Catalog se user ne items select kiye
            is_button = True
            order_data = message.get("order", {})
            order_items = order_data.get("product_items", [])
            if order_items:
                order_parts = []
                for item in order_items:
                    pid = item.get("product_retailer_id", "").upper()
                    qty = item.get("quantity", 1)
                    order_parts.append(f"ADD_{pid}:{qty}")
                user_msg = "|".join(order_parts)

        if not user_msg:
            db.commit()
            return {"status": "ok"}

        # ── QR Table+Bot pre-fill: "TABLE_3_BOT_5" ──────────────────────────
        import re as _re
        _qr_match = _re.match(r"^TABLE_(\w+)_BOT_(\d+)$", user_msg)
        if _qr_match:
            table_num, target_bot_id = _qr_match.group(1), int(_qr_match.group(2))
            target = next((b for b in bots if b.id == target_bot_id), None)
            if target:
                _set_routed_bot(sender, target_bot_id)
                bot = target
                # Store table number in session so flow picks it up
                from bots.restaurant.db import get_session_db, save_session_db
                _sess = get_session_db(sender, bot.id, db_session=db) or {}
                _sess["table_number"] = table_num
                save_session_db(sender, bot.id, _sess, db_session=db)
                from bots.restaurant.flow import handle_flow
                await handle_flow(sender, "hi", is_button=False, bot=bot, db_session=db)
                db.commit()
                return {"status": "ok"}
            # Fallback: plain table message
            user_msg = f"TABLE_{table_num}"

        # ── Multi-Bot Routing ────────────────────────────────────────────────
        if len(bots) > 1:
            if user_msg.startswith("SELECT_BOT_"):
                try:
                    sel_id = int(user_msg.replace("SELECT_BOT_", ""))
                    sel_bot = next((b for b in bots if b.id == sel_id), None)
                    if sel_bot:
                        _set_routed_bot(sender, sel_id)
                        bot = sel_bot
                        from whatsapp_handlers import send_text_message_v2
                        await send_text_message_v2(
                            sender,
                            f"✅ Welcome to *{bot.business_name or bot.name}*! 🍽️\n\nType *menu* to browse or say hi to get started!",
                            bot
                        )
                        db.commit()
                        return {"status": "ok"}
                except (ValueError, AttributeError):
                    pass
            elif user_msg.lower() in ["change", "restaurants", "switch", "back to menu"]:
                _bot_routing.pop(sender, None)
                await _send_bot_selector(sender, bots, bots[0])
                db.commit()
                return {"status": "ok"}
            else:
                routed = _get_routed_bot(sender, bots)
                if routed:
                    bot = routed
                else:
                    await _send_bot_selector(sender, bots, bots[0])
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

        # ── 3. Background Chat Logging ─────────────────────────────────────────────
        def log_chat_async(owner_id, phone, msg, bot_id):
            db_local = SessionLocal()
            try:
                db_local.add(ChatHistory(
                    user_id=owner_id, customer_phone=phone,
                    role="user", content=msg
                ))
                db_local.commit()
                log_bot_event(bot_id, "MSG_IN", msg[:200], customer_phone=phone)
            finally:
                db_local.close()

        background_tasks.add_task(log_chat_async, bot.owner_id, sender, user_msg, bot.id)

        # ── 4. Plugin pre-message hooks (run before bot flow) ───────────────
        try:
            from plugins import run_pre_message_hooks
            plugin_reply = await run_pre_message_hooks(sender, user_msg, bot, db)
            if plugin_reply:
                from whatsapp_handlers import send_text_message_v2
                await send_text_message_v2(sender, plugin_reply, bot)
                db.add(ChatHistory(user_id=bot.owner_id, customer_phone=sender, role="assistant", content=plugin_reply))
                db.commit()
                return {"status": "ok"}
        except Exception as _pe:
            logger.warning(f"Plugin hook error: {_pe}")

        # ── 5. Route to correct handler ──────────────────────────────────────
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
                from bots.restaurant.flow import handle_flow, handle_manager_flow

                # Route manager messages separately
                manager_num = (bot.manager_number or "").strip().lstrip("+")
                sender_bare = sender.strip().lstrip("+")
                is_manager = manager_num and (sender_bare == manager_num or sender == bot.manager_number)

                if is_manager:
                    await handle_manager_flow(sender, user_msg, is_button=is_button, bot=bot, db_session=db)
                else:
                    await handle_flow(sender, user_msg, is_button=is_button, bot=bot, db_session=db)

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

# ========== wwebjs Incoming Webhook ==========
@router.post("/wwebjs/webhook")
async def wwebjs_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Receives incoming messages forwarded by the wa-bridge Node.js service.
    Payload: { session, from, body, type, timestamp }

    Security: verifies X-Bridge-Secret header so only the bridge can post here.
    Then routes to the same bot-flow logic used by the Meta webhook.
    """
    # ── 1. Verify bridge secret ───────────────────────────────────────────────
    if BRIDGE_INTERNAL_SECRET:
        provided = request.headers.get("X-Bridge-Secret", "")
        if provided != BRIDGE_INTERNAL_SECRET:
            logger.warning("[wwebjs] Rejected webhook — wrong X-Bridge-Secret")
            return {"status": "ok"}   # always return 200 to bridge

    data = await request.json()

    session_name = data.get("session", "")
    sender       = data.get("from", "").strip()
    user_msg     = data.get("body", "").strip()

    if not session_name or not sender or not user_msg:
        return {"status": "ok"}

    # ── 2. Find bot by wwebjs_session name ────────────────────────────────────
    bot = db.query(WhatsappBot).filter(
        WhatsappBot.wwebjs_session == session_name
    ).first()

    if not bot:
        logger.warning(f"[wwebjs] No bot found for session: {session_name}")
        return {"status": "ok"}

    # ── 3. Rate limit ─────────────────────────────────────────────────────────
    if _is_rate_limited(sender):
        logger.warning(f"[wwebjs] Rate limit hit for sender: {sender}")
        return {"status": "ok"}

    # ── 4. Translate numbered reply → button ID using stored menu map ─────────
    from providers.wwebjs import get_menu_map
    menu_map = get_menu_map(session_name, sender)
    if menu_map and user_msg.isdigit():
        translated = menu_map.get(user_msg)
        if translated:
            logger.debug(f"[wwebjs] Translated '{user_msg}' → '{translated}'")
            user_msg = translated

    is_button = False   # wwebjs has no native buttons; all input is text

    # ── 5. Auto-create contact ────────────────────────────────────────────────
    contact = db.query(Contact).filter(
        Contact.phone == sender,
        Contact.owner_id == bot.owner_id
    ).first()
    if not contact:
        contact = Contact(
            owner_id=bot.owner_id, phone=sender,
            first_name="WhatsApp User", source="WhatsApp-wwebjs"
        )
        db.add(contact)
        db.commit()

    # ── 6. Background logging ─────────────────────────────────────────────────
    def _log_chat(owner_id, phone, msg, bot_id):
        db_l = SessionLocal()
        try:
            db_l.add(ChatHistory(
                user_id=owner_id, customer_phone=phone, role="user", content=msg
            ))
            db_l.commit()
            log_bot_event(bot_id, "MSG_IN", msg[:200], customer_phone=phone)
        finally:
            db_l.close()

    background_tasks.add_task(_log_chat, bot.owner_id, sender, user_msg, bot.id)

    # ── 7. Vapi handoff check ─────────────────────────────────────────────────
    handoff_keywords = ["call me", "talk to human", "speak with someone", "voice call"]
    if any(k in user_msg.lower() for k in handoff_keywords):
        if bot.vapi_agent_id:
            success = await trigger_vapi_outbound_call(sender, bot, db)
            if success:
                from whatsapp_handlers import send_text_message_v2
                await send_text_message_v2(
                    sender,
                    "📞 I'm initiating a voice call to you right now! Please pick up.",
                    bot,
                )
                db.add(ChatHistory(
                    user_id=bot.owner_id, customer_phone=sender,
                    role="assistant", content="[vapi_handoff_triggered]"
                ))
                db.commit()
                return {"status": "ok"}

    # ── 8. Route to bot flow (same as Meta webhook) ───────────────────────────
    try:
        if bot.bot_type == "restaurant":
            from bots.restaurant.flow import handle_flow, handle_manager_flow
            manager_num  = (bot.manager_number or "").strip().lstrip("+")
            sender_bare  = sender.strip().lstrip("+")
            is_manager   = manager_num and (
                sender_bare == manager_num or sender == bot.manager_number
            )
            if is_manager:
                await handle_manager_flow(sender, user_msg, is_button=is_button, bot=bot, db_session=db)
            else:
                await handle_flow(sender, user_msg, is_button=is_button, bot=bot, db_session=db)

        elif bot.bot_type == "real_estate":
            from bots.real_estate.flow import handle_flow as re_flow
            await re_flow(sender, user_msg, bot, db)

        elif bot.bot_type == "appointment":
            from bots.appointment.flow import handle_flow as appt_flow
            await appt_flow(sender, user_msg, bot, db)

        else:
            # AI fallback
            reply = await get_ai_response(sender, user_msg, bot, db)
            from whatsapp_handlers import send_text_message_v2
            await send_text_message_v2(sender, reply, bot)
            db.add(ChatHistory(
                user_id=bot.owner_id, customer_phone=sender,
                role="assistant", content=reply
            ))

        db.commit()

    except Exception as exc:
        import traceback
        logger.error(f"[wwebjs] Flow error: {exc}\n{traceback.format_exc()}")

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

    # wa.me needs the actual phone number (digits only, no +)
    # waba_id field stores the WhatsApp display number for some setups;
    # fall back to phone_number_id if not set
    wa_number = (bot.waba_id or bot.phone_number_id or "").replace("+", "").replace(" ", "")
    prefill = f"TABLE_{table_number}_BOT_{bot_id}"
    wa_link = f"https://wa.me/{wa_number}?text={prefill}"
    bot_name = bot.business_name or bot.name

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Table {table_number} — {bot_name}</title>
  <meta http-equiv="refresh" content="1;url={wa_link}">
  <style>
    body{{font-family:sans-serif;display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:100vh;margin:0;background:#111;color:#fff;text-align:center;padding:1rem}}
    .logo{{font-size:3rem;margin-bottom:1rem}}
    h1{{font-size:1.5rem;margin:0 0 .5rem}}
    p{{color:#aaa;font-size:.9rem}}
    a{{display:inline-block;margin-top:1.5rem;background:#25d366;color:#fff;text-decoration:none;padding:.8rem 2rem;border-radius:999px;font-weight:bold;font-size:1rem}}
  </style>
</head>
<body>
  <div class="logo">🍽️</div>
  <h1>{bot_name}</h1>
  <p>Table {table_number}</p>
  <p>Opening WhatsApp to start your order…</p>
  <a href="{wa_link}">Open WhatsApp</a>
</body>
</html>"""
    return HTMLResponse(content=html)
