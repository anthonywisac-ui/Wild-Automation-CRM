from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from typing import List, Optional
from pydantic import BaseModel
import os
import json
import requests
import traceback
import logging
from groq import Groq

from db import (
    get_db, get_user_by_username, decode_token, User,
    Contact, Deal, Call, VapiAgent, WhatsappBot,
    get_contacts, create_contact, get_deals, create_deal,
    get_calls, create_call, get_vapi_agents, create_vapi_agent,
    get_whatsapp_bots, create_whatsapp_bot, WebhookEvent
)
from auth import get_current_user

router = APIRouter(prefix="/api/crm", tags=["CRM"])
logger = logging.getLogger(__name__)

# ── Admin guard ───────────────────────────────────────────────────────────────
def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != "admin":
        raise HTTPException(403, "Admin access required")
    return current_user

# ── Admin: List all users ─────────────────────────────────────────────────────
@router.get("/admin/users")
def admin_list_users(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    from db import WhatsappBot
    users = db.query(User).all()
    return [{
        "id": u.id, "username": u.username, "role": u.role,
        "is_suspended": u.is_suspended,
        "whatsapp_bots": [b.name for b in db.query(WhatsappBot).filter(WhatsappBot.owner_id == u.id).all()]
    } for u in users]

@router.delete("/admin/users/{user_id}")
def admin_delete_user(user_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    if user_id == admin.id:
        raise HTTPException(400, "Cannot delete your own account")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(404, "User not found")
    db.delete(user)
    db.commit()
    return {"status": "deleted"}

@router.post("/admin/suspend-user")
def admin_suspend_user(data: dict, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == data.get("username")).first()
    if not user:
        raise HTTPException(404, "User not found")
    user.is_suspended = data.get("suspended", True)
    db.commit()
    return {"status": "updated", "is_suspended": user.is_suspended}

@router.post("/admin/assign-bot")
def admin_assign_bot(data: dict, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    from db import WhatsappBot
    user = db.query(User).filter(User.username == data.get("username")).first()
    if not user:
        raise HTTPException(404, "User not found")
    bot = db.query(WhatsappBot).filter(WhatsappBot.name == data.get("bot_name")).first()
    if not bot:
        raise HTTPException(404, "Bot not found")
    bot.owner_id = user.id
    db.commit()
    return {"status": "assigned", "bot": bot.name, "user": user.username}

# ── Settings endpoints ─────────────────────────────────────────────────────────
@router.get("/settings/my-config")
def get_my_config(current_user: User = Depends(get_current_user)):
    return {
        "ai_provider": current_user.ai_provider or "groq",
        "groq_api_key": "***" if current_user.groq_api_key else "",
        "gemini_api_key": "***" if current_user.gemini_api_key else "",
        "openai_api_key": "***" if current_user.openai_api_key else "",
    }

@router.post("/settings/save-config")
def save_my_config(data: dict, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    allowed = {"ai_provider", "groq_api_key", "gemini_api_key", "openai_api_key", "default_voice"}
    for k, v in data.items():
        if k in allowed and v and v != "***":
            setattr(current_user, k, v)
    db.commit()
    return {"status": "saved"}

@router.delete("/settings/delete-account")
def delete_my_account(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from db import WhatsappBot, Contact, Deal, Call, VapiAgent
    # Delete all user data
    db.query(WhatsappBot).filter(WhatsappBot.owner_id == current_user.id).delete()
    db.query(Contact).filter(Contact.owner_id == current_user.id).delete()
    db.query(Deal).filter(Deal.owner_id == current_user.id).delete()
    db.query(Call).filter(Call.owner_id == current_user.id).delete()
    db.query(VapiAgent).filter(VapiAgent.owner_id == current_user.id).delete()
    db.delete(current_user)
    db.commit()
    return {"status": "deleted"}


# ========== Pydantic Models for CRM ==========
class ContactCreate(BaseModel):
    first_name: Optional[str] = ""
    last_name: Optional[str] = ""
    company: Optional[str] = ""
    email: Optional[str] = ""
    phone: Optional[str] = ""
    status: Optional[str] = "New"
    source: Optional[str] = "Manual"
    notes: Optional[str] = ""

class ContactUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    company: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    status: Optional[str] = None
    source: Optional[str] = None
    notes: Optional[str] = None

class DealCreate(BaseModel):
    title: str = "New Deal"
    company: Optional[str] = ""
    contact_name: Optional[str] = ""
    value: float = 0.0
    stage: str = "Discovery"
    probability: int = 20
    expected_close: Optional[datetime] = None
    notes: Optional[str] = ""

class DealUpdate(BaseModel):
    title: Optional[str] = None
    company: Optional[str] = None
    contact_name: Optional[str] = None
    value: Optional[float] = None
    stage: Optional[str] = None
    probability: Optional[int] = None
    expected_close: Optional[datetime] = None
    notes: Optional[str] = None

class CallCreate(BaseModel):
    contact_name: str = "Unknown"
    phone: str = ""
    direction: str = "Inbound"
    duration_minutes: float = 0.0
    outcome: str = "Resolved"
    agent: str = ""
    notes: Optional[str] = ""

# ========== Contacts ==========
@router.get("/contacts")
def get_contacts_api(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return get_contacts(db, current_user.id)

@router.post("/contacts")
def create_contact_api(contact: ContactCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return create_contact(db, current_user.id, contact.dict())

@router.put("/contacts/{contact_id}")
def update_contact_api(contact_id: int, contact: ContactUpdate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    db_contact = db.query(Contact).filter(Contact.id == contact_id, Contact.owner_id == current_user.id).first()
    if not db_contact:
        raise HTTPException(404, "Contact not found")
    
    update_data = contact.dict(exclude_unset=True)
    # List of allowed fields to update to prevent mass assignment
    allowed_fields = {"first_name", "last_name", "company", "email", "phone", "status", "source", "notes"}
    
    for key, value in update_data.items():
        if key in allowed_fields:
            setattr(db_contact, key, value)
            
    db.commit()
    db.refresh(db_contact)
    return db_contact

# ========== Deals ==========
@router.get("/deals")
def get_deals_api(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return get_deals(db, current_user.id)

@router.post("/deals")
def create_deal_api(deal: DealCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return create_deal(db, current_user.id, deal.dict())

@router.put("/deals/{deal_id}")
def update_deal_api(deal_id: int, deal: DealUpdate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    db_deal = db.query(Deal).filter(Deal.id == deal_id, Deal.owner_id == current_user.id).first()
    if not db_deal:
        raise HTTPException(404, "Deal not found")
    
    update_data = deal.dict(exclude_unset=True)
    allowed_fields = {"title", "company", "contact_name", "value", "stage", "probability", "expected_close", "notes"}
    
    for key, value in update_data.items():
        if key in allowed_fields:
            setattr(db_deal, key, value)
            
    db.commit()
    db.refresh(db_deal)
    return db_deal

# ========== Calls ==========
@router.get("/calls")
def get_calls_api(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return get_calls(db, current_user.id)

@router.post("/calls")
def create_call_api(call: CallCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return create_call(db, current_user.id, call.dict())

@router.get("/calls/kpis")
def get_kpis_api(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    user_calls = get_calls(db, current_user.id)
    total = len(user_calls)
    resolved = len([c for c in user_calls if c.outcome == "Resolved"])
    missed = len([c for c in user_calls if c.direction == "Missed"])
    fcr = round(resolved / total * 100) if total else 0
    durations = [c.duration_minutes for c in user_calls if c.duration_minutes and c.duration_minutes > 0]
    avg_dur = sum(durations) / len(durations) if durations else 0
    mins = int(avg_dur)
    secs = int((avg_dur - mins) * 60)
    aht = f"{mins}:{secs:02d}"
    return {"total": total, "fcr": fcr, "missed": missed, "aht": aht, "avg_duration": round(avg_dur, 1)}

# ========== Vapi Agents ==========
class VapiAgentCreate(BaseModel):
    name: str
    vapi_api_key: str
    vapi_agent_id: str
    phone_number_id: Optional[str] = None
    first_message: str
    system_prompt: str
    voice: str = "Alloy"
    crm_sync: bool = False
    webhook_url: str

@router.get("/vapi/agents")
def get_vapi_agents_api(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    agents = db.query(VapiAgent).filter(VapiAgent.owner_id == current_user.id).all()
    return [{
        "id": a.id,
        "name": a.name,
        "status": a.status,
        "last_call": a.last_call.isoformat() if a.last_call else None,
        "total_calls": a.total_calls,
        "conversion_rate": a.conversion_rate
        # ✅ FIX #6: Don't return vapi_api_key or vapi_agent_id
    } for a in agents]

@router.post("/vapi/agents")
def create_vapi_agent_api(agent_data: VapiAgentCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    existing = db.query(VapiAgent).filter(VapiAgent.owner_id == current_user.id, VapiAgent.name == agent_data.name).first()
    if existing:
        raise HTTPException(400, "Agent name already exists")
    new_agent = VapiAgent(
        owner_id=current_user.id,
        name=agent_data.name,
        vapi_api_key=agent_data.vapi_api_key,
        vapi_agent_id=agent_data.vapi_agent_id,
        phone_number_id=agent_data.phone_number_id or "",
        first_message=agent_data.first_message,
        system_prompt=agent_data.system_prompt,
        voice=agent_data.voice,
        crm_sync=agent_data.crm_sync,
        webhook_url=agent_data.webhook_url,
        status="Draft",
        total_calls=0,
        conversion_rate=0.0
    )
    db.add(new_agent)
    db.commit()
    db.refresh(new_agent)
    return {"id": new_agent.id, "message": "Agent created"}

@router.delete("/vapi/agents/{agent_id}")
def delete_vapi_agent(agent_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    agent = db.query(VapiAgent).filter(VapiAgent.id == agent_id, VapiAgent.owner_id == current_user.id).first()
    if not agent:
        raise HTTPException(404, "Agent not found")
    db.delete(agent)
    db.commit()
    return {"message": "Agent deleted"}

@router.post("/vapi/agents/{agent_id}/test-call")
def test_vapi_call(agent_id: int, payload: dict, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    agent = db.query(VapiAgent).filter(VapiAgent.id == agent_id, VapiAgent.owner_id == current_user.id).first()
    if not agent:
        raise HTTPException(404, "Agent not found")
    phone = payload.get("phone")
    if not phone:
        raise HTTPException(400, "Phone number required")
    # Simulate call initiation
    return {"message": f"Test call initiated to {phone}"}

@router.get("/vapi/agents/stats")
def get_vapi_stats(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    agents = db.query(VapiAgent).filter(VapiAgent.owner_id == current_user.id).all()
    total_calls = sum(a.total_calls for a in agents)
    hot_leads = 0  # mock
    return {"total_calls": total_calls, "hot_leads": hot_leads}

# ========== WhatsApp Bots ==========
@router.get("/bots")
def get_bots_api(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return get_whatsapp_bots(db, current_user.id)

@router.post("/bots")
def create_bot_api(bot: dict, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return create_whatsapp_bot(db, current_user.id, bot)

class WhatsappBotCreate(BaseModel):
    name: str
    bot_type: str
    business_name: Optional[str] = None
    language: Optional[str] = "en"
    meta_token: Optional[str] = None
    phone_number_id: Optional[str] = None
    waba_id: Optional[str] = None
    verify_token: Optional[str] = None
    manager_number: Optional[str] = None
    ai_provider: Optional[str] = "gemini"
    ai_api_key: Optional[str] = None
    system_prompt: Optional[str] = None
    google_sheet_id: Optional[str] = None
    google_creds_json: Optional[str] = None
    stripe_secret_key: Optional[str] = None
    webhook_url: Optional[str] = None
    vapi_agent_id: Optional[str] = None

@router.post("/bots/whatsapp")
def create_whatsapp_bot_endpoint(
    bot_data: WhatsappBotCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    existing = db.query(WhatsappBot).filter(
        WhatsappBot.owner_id == current_user.id,
        WhatsappBot.name == bot_data.name
    ).first()
    if existing:
        raise HTTPException(400, "Bot name already exists")
    new_bot = WhatsappBot(
        owner_id=current_user.id,
        name=bot_data.name,
        bot_type=bot_data.bot_type,
        business_name=bot_data.business_name or "",
        language=bot_data.language or "en",
        meta_token=bot_data.meta_token or "",
        phone_number_id=bot_data.phone_number_id or "",
        waba_id=bot_data.waba_id or "",
        verify_token=bot_data.verify_token or "",
        manager_number=bot_data.manager_number or "",
        ai_provider=bot_data.ai_provider or "gemini",
        ai_api_key=bot_data.ai_api_key or "",
        system_prompt=bot_data.system_prompt or "",
        google_sheet_id=bot_data.google_sheet_id or "",
        google_creds_json=bot_data.google_creds_json or "",
        stripe_secret_key=bot_data.stripe_secret_key or "",
        webhook_url=bot_data.webhook_url or "",
        vapi_agent_id=bot_data.vapi_agent_id or ""
    )
    db.add(new_bot)
    db.commit()
    db.refresh(new_bot)
    user_bots = current_user.bots
    if bot_data.name not in user_bots:
        user_bots.append(bot_data.name)
        current_user.bots = user_bots
        db.commit()
    return {
        "id": new_bot.id,
        "name": new_bot.name,
        "message": "Bot created successfully",
        "webhook_url": bot_data.webhook_url or f"https://{os.getenv('DOMAIN', 'yourdomain.com')}/webhook"
    }

# ========== Dashboard Stats ==========
@router.get("/stats")
def get_stats_api(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from datetime import date as _date
    from db import ChatHistory, Order
    today_start = datetime.combine(_date.today(), datetime.min.time())

    contacts = get_contacts(db, current_user.id)
    deals = get_deals(db, current_user.id)
    active_deals = [d for d in deals if d.stage != "Lost"]
    pipeline_value = sum(d.value for d in active_deals)
    hot_leads = len([c for c in contacts if c.status == "Hot Lead"])

    # Messages today (WhatsApp conversations)
    messages_today = db.query(ChatHistory).filter(
        ChatHistory.user_id == current_user.id,
        ChatHistory.role == "user",
        ChatHistory.created_at >= today_start
    ).count()

    # Calls today
    calls_today = db.query(Call).filter(
        Call.owner_id == current_user.id,
        Call.call_date >= today_start
    ).count()

    # New leads today
    new_leads = db.query(Contact).filter(
        Contact.owner_id == current_user.id,
        Contact.created_at >= today_start
    ).count()

    # Conversion rate from Vapi agents
    vapi_agents = db.query(VapiAgent).filter(VapiAgent.owner_id == current_user.id).all()
    conversion_rate = round(
        (sum(a.conversion_rate for a in vapi_agents) / len(vapi_agents)) if vapi_agents else 0, 1
    )

    return {
        "contacts": len(contacts),
        "deals": len(active_deals),
        "pipeline_value": pipeline_value,
        "hot_leads": hot_leads,
        "messages_today": messages_today,
        "calls_today": calls_today,
        "new_leads": new_leads,
        "conversion_rate": conversion_rate,
    }

# ========== User Overview ==========
@router.get("/user/overview")
def get_user_overview(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # WhatsApp bots
    whatsapp_bots = db.query(WhatsappBot).filter(WhatsappBot.owner_id == current_user.id).all()
    whatsapp_list = []
    for bot in whatsapp_bots:
        whatsapp_list.append({
            "name": bot.name,
            "status": "live" if bot.webhook_url else "offline",
            "messages_today": 0,
            "total_conversations": 0,
            "last_activity": bot.created_at.isoformat() if bot.created_at else None
        })
    # Vapi agents
    vapi_agents = db.query(VapiAgent).filter(VapiAgent.owner_id == current_user.id).all()
    vapi_list = []
    for agent in vapi_agents:
        vapi_list.append({
            "name": agent.name,
            "status": agent.status,
            "total_calls": agent.total_calls,
            "conversion_rate": agent.conversion_rate,
            "calls_today": 0
        })
    # Recent conversations (mock)
    recent_conversations = [
        {"contact": "Ahmed", "bot_name": "Restaurant Bot", "last_message": "I want to order", "time": datetime.now().isoformat(), "unread": True},
        {"contact": "Sara", "bot_name": "Sales Agent", "last_message": "Call resolved", "time": (datetime.now() - timedelta(hours=1)).isoformat(), "unread": False}
    ]
    # Stats
    stats = {
        "total_messages": 0,
        "total_calls": sum(a.total_calls for a in vapi_agents),
        "total_leads": len([c for c in get_contacts(db, current_user.id) if c.status == "Hot Lead"]),
        "conversion_rate": round((sum(a.conversion_rate for a in vapi_agents) / len(vapi_agents)) if vapi_agents else 0, 1)
    }
    return {
        "whatsapp_bots": whatsapp_list,
        "vapi_agents": vapi_list,
        "recent_conversations": recent_conversations,
        "stats": stats
    }

# ========== Settings Endpoints ==========
class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

@router.post("/settings/change-password")
def change_password(req: ChangePasswordRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from db import verify_password, hash_password
    if not verify_password(req.current_password, current_user.hashed_password):
        raise HTTPException(400, "Current password is incorrect")
    current_user.hashed_password = hash_password(req.new_password)
    db.commit()
    return {"message": "Password updated"}

class UserConfig(BaseModel):
    ai_provider: str = "groq"
    ai_api_key: str = ""
    default_voice: str = "Alloy"
    default_first_message: str = "Hello, how can I help you?"

@router.get("/settings/my-config")
def get_my_config(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return {
        "ai_provider": current_user.ai_provider or "groq",
        "groq_api_key": current_user.groq_api_key or "",
        "gemini_api_key": current_user.gemini_api_key or "",
        "openai_api_key": current_user.openai_api_key or "",
        "default_voice": current_user.default_voice or "Alloy",
        "default_first_message": current_user.default_first_message or "Hello, how can I help you?"
    }

class UserConfigSave(BaseModel):
    ai_provider: str
    groq_api_key: Optional[str] = ""
    gemini_api_key: Optional[str] = ""
    openai_api_key: Optional[str] = ""
    default_voice: Optional[str] = "Alloy"
    default_first_message: Optional[str] = "Hello, how can I help you?"

# ========== WhatsApp Bots (deduplicated) ==========
@router.get("/bots/whatsapp")
def get_my_bots(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    bots = get_whatsapp_bots(db, current_user.id)
    # Serialize to avoid exposing sensitive keys
    return [{
        "id": b.id, "name": b.name, "bot_type": b.bot_type,
        "business_name": b.business_name, "business_niche": b.business_niche,
        "language": b.language, "webhook_url": b.webhook_url,
        "forwarding_url": b.forwarding_url, "phone_number_id": b.phone_number_id,
        "config_json": b.config_json, "created_at": b.created_at.isoformat() if b.created_at else None
    } for b in bots]

@router.put("/bots/whatsapp/{bot_id}")
def update_bot_api(bot_id: int, data: dict, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    bot = db.query(WhatsappBot).filter(WhatsappBot.id == bot_id, WhatsappBot.owner_id == current_user.id).first()
    if not bot:
        raise HTTPException(404, "Bot not found")
    # Allowlist of safe fields to update (no owner_id hijacking)
    allowed = {
        "name", "bot_type", "business_name", "language", "meta_token",
        "phone_number_id", "waba_id", "verify_token", "manager_number",
        "ai_provider", "ai_api_key", "system_prompt", "google_sheet_id",
        "google_creds_json", "stripe_secret_key", "webhook_url", "forwarding_url",
        "config_json", "tax_rate", "delivery_fee", "business_niche"
    }
    for key, val in data.items():
        if key in allowed:
            setattr(bot, key, val)
    db.commit()
    db.refresh(bot)
    return {"status": "updated", "id": bot.id}

@router.delete("/bots/whatsapp/{bot_id}")
def delete_bot_api(bot_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    bot = db.query(WhatsappBot).filter(WhatsappBot.id == bot_id, WhatsappBot.owner_id == current_user.id).first()
    if not bot:
        raise HTTPException(404, "Bot not found")
    db.delete(bot)
    db.commit()
    return {"status": "deleted"}

@router.post("/settings/save-config")
def save_config(config: UserConfigSave, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    current_user.ai_provider = config.ai_provider
    current_user.groq_api_key = config.groq_api_key
    current_user.gemini_api_key = config.gemini_api_key
    current_user.openai_api_key = config.openai_api_key
    current_user.default_voice = config.default_voice
    current_user.default_first_message = config.default_first_message
    db.commit()
    logger.info(f"User {current_user.username} saved AI configuration")
    return {"message": "Configuration saved successfully"}

@router.post("/settings/test-ai")
async def test_ai(payload: dict, current_user: User = Depends(get_current_user)):
    api_key = payload.get("api_key", "").strip()
    provider = payload.get("provider", "groq").lower()
    
    if not api_key or "your" in api_key.lower():
        return {"success": False, "message": "No valid API key provided"}
        
    try:
        if provider == "groq":
            from groq import Groq
            client = Groq(api_key=api_key)
            client.chat.completions.create(
                model="llama-3.1-8b-instant", # Updated model
                messages=[{"role": "user", "content": "test"}],
                max_tokens=5
            )
        elif provider == "gemini":
            import google.generativeai as genai
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel('gemini-1.5-flash-latest') # Updated model
            model.generate_content("test")
        elif provider == "openai":
            from openai import OpenAI
            client = OpenAI(api_key=api_key)
            client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": "test"}],
                max_tokens=5
            )
        else:
            return {"success": False, "message": f"Provider {provider} not supported for testing"}
            
        return {"success": True, "message": "Connection successful! Your API key is working."}
    except Exception as e:
        logger.error(f"AI Test Failed for {provider}: {str(e)}")
        return {"success": False, "message": f"Connection failed: {str(e)}"}

@router.delete("/settings/delete-account")
def delete_account(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    db.query(Contact).filter(Contact.owner_id == current_user.id).delete()
    db.query(Deal).filter(Deal.owner_id == current_user.id).delete()
    db.query(Call).filter(Call.owner_id == current_user.id).delete()
    db.query(VapiAgent).filter(VapiAgent.owner_id == current_user.id).delete()
    db.query(WhatsappBot).filter(WhatsappBot.owner_id == current_user.id).delete()
    db.delete(current_user)
    db.commit()
    logger.warning(f"Account deleted: {current_user.username}")
    return {"message": "Account deleted"}

# ========== Admin Endpoints ==========
def require_admin(current_user: User = Depends(get_current_user)):
    if current_user.role != "admin":
        raise HTTPException(403, "Admin access required")
    return current_user

@router.get("/admin/users")
def list_users(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    users = db.query(User).all()
    result = []
    for u in users:
        whatsapp_bots = db.query(WhatsappBot).filter(WhatsappBot.owner_id == u.id).all()
        vapi_agents = db.query(VapiAgent).filter(VapiAgent.owner_id == u.id).all()
        result.append({
            "id": u.id,
            "username": u.username,
            "role": u.role,
            "registered_at": u.created_at.isoformat(),
            "whatsapp_bots": [b.name for b in whatsapp_bots],
            "vapi_agents": [a.name for a in vapi_agents],
            "is_suspended": u.is_suspended
        })
    return result

@router.get("/admin/stats")
def admin_stats(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    total_users = db.query(User).count()
    active_bots = db.query(WhatsappBot).count()
    active_agents = db.query(VapiAgent).filter(VapiAgent.status == "Live").count()
    return {"total_users": total_users, "active_bots": active_bots, "active_agents": active_agents, "total_revenue": 0}

class AssignBotRequest(BaseModel):
    username: str
    bot_name: str

@router.post("/admin/assign-bot")
def assign_bot(req: AssignBotRequest, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    user = get_user_by_username(db, req.username)
    if not user:
        raise HTTPException(404, "User not found")
    bot = db.query(WhatsappBot).filter(WhatsappBot.name == req.bot_name).first()
    if not bot:
        raise HTTPException(404, "Bot not found")
    if bot.owner_id != user.id:
        bot.owner_id = user.id
        db.commit()
    return {"message": f"Bot {req.bot_name} assigned to {req.username}"}

class AssignVapiRequest(BaseModel):
    username: str
    agent_name: str

@router.post("/admin/assign-vapi")
def assign_vapi(req: AssignVapiRequest, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    user = get_user_by_username(db, req.username)
    if not user:
        raise HTTPException(404, "User not found")
    agent = db.query(VapiAgent).filter(VapiAgent.name == req.agent_name).first()
    if not agent:
        raise HTTPException(404, "Agent not found")
    if agent.owner_id != user.id:
        agent.owner_id = user.id
        db.commit()
    return {"message": f"Vapi agent {req.agent_name} assigned to {req.username}"}

class SuspendUserRequest(BaseModel):
    username: str
    suspended: bool

@router.post("/admin/suspend-user")
def suspend_user(req: SuspendUserRequest, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    user = get_user_by_username(db, req.username)
    if not user:
        raise HTTPException(404, "User not found")
    user.is_suspended = req.suspended
    db.commit()
    return {"message": f"User {req.username} suspension set to {req.suspended}"}

@router.delete("/admin/users/{user_id}")
def delete_user_by_admin(user_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(404, "User not found")
    if user.id == admin.id:
        raise HTTPException(400, "Cannot delete yourself")
    db.delete(user)
    db.commit()
    return {"message": "User deleted"}

@router.post("/crm/events")
def receive_custom_event(event: dict, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from db import WebhookEvent
    new_ev = WebhookEvent(user_id=current_user.id, type="custom")
    new_ev.payload = event
    db.add(new_ev)
    db.commit()
    return {"status": "ok"}

@router.get("/crm/events")
def get_events(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    events = db.query(WebhookEvent).filter(WebhookEvent.user_id == current_user.id).order_by(WebhookEvent.created_at.desc()).limit(50).all()
    return [{"type": e.type, "data": e.payload, "time": e.created_at.isoformat()} for e in events]

# ========== AI Chat Helper (with Groq) ==========

def get_user_config_safe(current_user: User) -> dict:
    """✅ FIX #2: Get config from dedicated User fields"""
    return {
        "ai_provider": current_user.ai_provider or "groq",
        "ai_api_key": current_user.ai_api_key or "",
        "default_voice": current_user.default_voice or "Alloy",
        "default_first_message": current_user.default_first_message or "Hello, how can I help you?"
    }

def get_db_summary(current_user: User, db: Session) -> str:
    if current_user.role == "admin":
        contacts = db.query(Contact).all()
        deals = db.query(Deal).all()
        calls = db.query(Call).all()
        whatsapp_bots = db.query(WhatsappBot).all()
        vapi_agents = db.query(VapiAgent).all()
        users = db.query(User).all()
        summary = f"""
DATABASE SUMMARY (Admin view):
- Total users: {len(users)}
- Total contacts: {len(contacts)} (hot leads: {sum(1 for c in contacts if c.status == 'Hot Lead')})
- Total deals: {len(deals)} (pipeline value: ${sum(d.value for d in deals if d.stage != 'Lost')})
- Total calls: {len(calls)} (resolved: {sum(1 for c in calls if c.outcome == 'Resolved')})
- WhatsApp bots: {len(whatsapp_bots)}
- Vapi agents: {len(vapi_agents)}
"""
    else:
        contacts = db.query(Contact).filter(Contact.owner_id == current_user.id).all()
        deals = db.query(Deal).filter(Deal.owner_id == current_user.id).all()
        calls = db.query(Call).filter(Call.owner_id == current_user.id).all()
        whatsapp_bots = db.query(WhatsappBot).filter(WhatsappBot.owner_id == current_user.id).all()
        vapi_agents = db.query(VapiAgent).filter(VapiAgent.owner_id == current_user.id).all()
        summary = f"""
DATABASE SUMMARY (User: {current_user.username}):
- Your contacts: {len(contacts)} (hot leads: {sum(1 for c in contacts if c.status == 'Hot Lead')})
- Your deals: {len(deals)} (pipeline value: ${sum(d.value for d in deals if d.stage != 'Lost')})
- Your calls: {len(calls)} (resolved: {sum(1 for c in calls if c.outcome == 'Resolved')})
- Your WhatsApp bots: {len(whatsapp_bots)}
- Your Vapi agents: {len(vapi_agents)}
"""
    return summary

class ChatRequest(BaseModel):
    messages: list

# ========== AI Chat Endpoint (FIX #2: Uses dedicated fields) ==========
@router.post("/ai/chat")
async def ai_chat(req: ChatRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        from db import ChatHistory
        user_config = get_user_config_safe(current_user)
        provider = user_config.get("ai_provider", os.getenv("AI_PROVIDER", "groq")).lower()
        
        # Get last 10 messages from history
        history = db.query(ChatHistory).filter(ChatHistory.user_id == current_user.id).order_by(ChatHistory.created_at.desc()).limit(10).all()
        history.reverse()
        
        # Priority: Database (User settings) -> Environment Variable
        provider = user_config.get("ai_provider", os.getenv("AI_PROVIDER", "groq")).lower()
        
        # Get key for the specific provider
        api_key = ""
        if provider == "groq":
            api_key = current_user.groq_api_key or os.getenv("GROQ_API_KEY", "")
        elif provider == "gemini":
            api_key = current_user.gemini_api_key or os.getenv("GEMINI_API_KEY", "")
        elif provider == "openai":
            api_key = current_user.openai_api_key or os.getenv("OPENAI_API_KEY", "")
        
        api_key = api_key.strip()
        if not api_key or "your" in api_key.lower():
            # Fallback to general ai_api_key field if others empty
            api_key = current_user.ai_api_key.strip()

        if not api_key or "your" in api_key.lower():
            logger.error(f"No valid API key found for {provider}")
            return {"reply": f"⚠️ No valid API key found for {provider}. Please set it in Settings."}

        db_summary = get_db_summary(current_user, db)
        system_prompt = f"""You are an AI assistant for a CRM + WhatsApp/Vapi bot platform.
Your role is to help users understand their data and answer questions.

{db_summary}

Rules:
- For admin users: you can answer any question about the system, database, or suggest improvements.
- For normal users: only answer questions related to their own leads, contacts, deals, calls, bots.
- Be concise, helpful, and use markdown for lists/tables when useful.
- Never reveal other users' data.

Current user: {current_user.username} (role: {current_user.role})
"""
        last_user_msg = req.messages[-1]["content"]
        
        # Save user message to history
        db.add(ChatHistory(user_id=current_user.id, role="user", content=last_user_msg))
        db.commit()

        # Build messages for LLM
        llm_messages = [{"role": "system", "content": system_prompt}]
        for h in history:
            llm_messages.append({"role": h.role, "content": h.content})
        llm_messages.append({"role": "user", "content": last_user_msg})

        reply = ""
        try:
            if provider == "groq":
                from groq import Groq
                client = Groq(api_key=api_key)
                response = client.chat.completions.create(
                    model="llama-3.3-70b-versatile", # Updated model
                    messages=llm_messages,
                    temperature=0.7,
                    max_tokens=800
                )
                reply = response.choices[0].message.content

            elif provider == "gemini":
                import google.generativeai as genai
                genai.configure(api_key=api_key)
                # Try multiple common model name formats
                model_name = 'gemini-1.5-flash-latest' # Updated model
                try:
                    model = genai.GenerativeModel(model_name)
                    chat = model.start_chat(history=[
                        {"role": h.role if h.role == "user" else "model", "parts": [h.content]}
                        for h in history
                    ])
                    response = chat.send_message(f"{system_prompt}\n\nUser: {last_user_msg}")
                    reply = response.text
                except Exception:
                    try:
                        # Try with models/ prefix
                        model = genai.GenerativeModel(f'models/{model_name}')
                        chat = model.start_chat(history=[
                            {"role": h.role if h.role == "user" else "model", "parts": [h.content]}
                            for h in history
                        ])
                        response = chat.send_message(f"{system_prompt}\n\nUser: {last_user_msg}")
                        reply = response.text
                    except Exception:
                        # Final fallback to gemini-pro
                        model = genai.GenerativeModel('gemini-pro')
                        chat = model.start_chat(history=[
                            {"role": h.role if h.role == "user" else "model", "parts": [h.content]}
                            for h in history
                        ])
                        response = chat.send_message(f"{system_prompt}\n\nUser: {last_user_msg}")
                        reply = response.text

            elif provider == "openai":
                from openai import OpenAI
                client = OpenAI(api_key=api_key)
                response = client.chat.completions.create(
                    model="gpt-4-turbo",
                    messages=llm_messages,
                    temperature=0.7,
                    max_tokens=800
                )
                reply = response.choices[0].message.content
            else:
                reply = f"Provider '{provider}' not supported."
        except Exception as primary_error:
            logger.warning(f"Primary AI provider ({provider}) failed: {str(primary_error)}. Attempting fallback...")
            
            # Fallback to Groq if Gemini/OpenAI fails
            if provider != "groq":
                # Priority for fallback key: User settings (DB) -> Environment (.env)
                fallback_key = (current_user.groq_api_key or os.getenv("GROQ_API_KEY", "")).strip()
                
                if fallback_key and "your" not in fallback_key.lower():
                    try:
                        from groq import Groq
                        client = Groq(api_key=fallback_key)
                        response = client.chat.completions.create(
                            model="llama-3.1-8b-instant",
                            messages=llm_messages,
                            temperature=0.7,
                            max_tokens=800
                        )
                        reply = f" (Fallback) {response.choices[0].message.content}"
                        logger.info("Fallback to Groq successful")
                    except Exception as fallback_error:
                        reply = f"⚠️ Primary error ({provider}): {str(primary_error)}. Fallback error (Groq): {str(fallback_error)}"
                else:
                    reply = f"⚠️ AI Error ({provider}): {str(primary_error)}. No Groq fallback key configured in Settings or .env."
            else:
                reply = f"⚠️ AI Error (Groq): {str(primary_error)}"

        # Save assistant reply to history
        if reply:
            db.add(ChatHistory(user_id=current_user.id, role="assistant", content=reply))
            db.commit()

        return {"reply": reply}

    except Exception as e:
        logger.error(f"AI Chat Error: {str(e)}", exc_info=True)
        return {"reply": f"⚠️ Error: {str(e)}. Check your API key and provider settings."}
# ========== Webhook Info ==========
@router.get("/webhook/port")
def get_webhook_port():
    return 8000

@router.get("/webhook/events")
def get_webhook_events(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from db import WebhookEvent
    events = db.query(WebhookEvent).filter(
        WebhookEvent.user_id == current_user.id
    ).order_by(WebhookEvent.created_at.desc()).limit(20).all()
    return [{"type": e.type, "data": e.payload, "time": e.created_at.isoformat()} for e in events]

# ========== Reservations ==========
class ReservationCreate(BaseModel):
    customer_name: str = ""
    customer_phone: str = ""
    party_size: int = 2
    reservation_date: str = ""
    reservation_time: str = ""
    notes: Optional[str] = ""

@router.get("/reservations")
def get_reservations(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from db import Reservation
    rows = db.query(Reservation).filter(Reservation.owner_id == current_user.id).order_by(Reservation.created_at.desc()).all()
    return [{
        "id": r.id, "customer_name": r.customer_name, "customer_phone": r.customer_phone,
        "party_size": r.party_size, "date": r.reservation_date, "time": r.reservation_time,
        "status": r.status, "notes": r.notes, "created_at": r.created_at.isoformat()
    } for r in rows]

@router.post("/reservations")
def create_reservation(data: ReservationCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from db import Reservation
    r = Reservation(owner_id=current_user.id, **data.dict())
    db.add(r)
    db.commit()
    db.refresh(r)
    return {"id": r.id, "message": "Reservation created"}

@router.put("/reservations/{res_id}")
def update_reservation(res_id: int, data: dict, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from db import Reservation
    r = db.query(Reservation).filter(Reservation.id == res_id, Reservation.owner_id == current_user.id).first()
    if not r:
        raise HTTPException(404, "Reservation not found")
    allowed = {"customer_name", "customer_phone", "party_size", "reservation_date", "reservation_time", "status", "notes"}
    for k, v in data.items():
        if k in allowed:
            setattr(r, k, v)
    db.commit()
    return {"status": "updated"}

@router.delete("/reservations/{res_id}")
def delete_reservation(res_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from db import Reservation
    r = db.query(Reservation).filter(Reservation.id == res_id, Reservation.owner_id == current_user.id).first()
    if not r:
        raise HTTPException(404, "Reservation not found")
    db.delete(r)
    db.commit()
    return {"status": "deleted"}

# ========== Orders ==========
@router.get("/orders")
def get_orders(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from db import Order
    rows = db.query(Order).filter(Order.owner_id == current_user.id).order_by(Order.created_at.desc()).limit(100).all()
    return [{
        "id": r.id, "customer_number": r.customer_number, "items": r.items,
        "total_amount": r.total_amount, "grand_total": r.grand_total,
        "status": r.status, "delivery_type": r.delivery_type,
        "created_at": r.created_at.isoformat()
    } for r in rows]

@router.put("/orders/{order_id}/status")
def update_order_status(order_id: int, data: dict, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from db import Order
    order = db.query(Order).filter(Order.id == order_id, Order.owner_id == current_user.id).first()
    if not order:
        raise HTTPException(404, "Order not found")
    status = data.get("status", "")
    if status in ["Pending", "Confirmed", "Ready", "Delivered", "Cancelled"]:
        order.status = status
        db.commit()
    return {"status": "updated"}

# ========== Conversations (Chat History per contact) ==========
@router.get("/conversations")
def get_conversations(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Returns unique contacts with their last message for conversation list."""
    from db import ChatHistory
    from sqlalchemy import func
    subq = db.query(
        ChatHistory.customer_phone,
        func.max(ChatHistory.created_at).label("last_time")
    ).filter(
        ChatHistory.user_id == current_user.id,
        ChatHistory.customer_phone != ""
    ).group_by(ChatHistory.customer_phone).subquery()

    results = db.query(ChatHistory, subq.c.last_time).join(
        subq, (ChatHistory.customer_phone == subq.c.customer_phone) &
              (ChatHistory.created_at == subq.c.last_time)
    ).order_by(subq.c.last_time.desc()).limit(50).all()

    return [{
        "phone": row.ChatHistory.customer_phone,
        "last_message": row.ChatHistory.content[:100],
        "last_role": row.ChatHistory.role,
        "time": row.last_time.isoformat()
    } for row in results]

@router.get("/conversations/{phone}")
def get_conversation_history(phone: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from db import ChatHistory
    msgs = db.query(ChatHistory).filter(
        ChatHistory.user_id == current_user.id,
        ChatHistory.customer_phone == phone
    ).order_by(ChatHistory.created_at.asc()).limit(100).all()
    return [{
        "role": m.role, "content": m.content,
        "time": m.created_at.isoformat()
    } for m in msgs]

# ========== Admin Global Stats ==========
@router.get("/admin/platform-stats")
def admin_platform_stats(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    from db import ChatHistory, Order, Reservation
    from datetime import date as _date
    today_start = datetime.combine(_date.today(), datetime.min.time())
    return {
        "total_users": db.query(User).count(),
        "total_bots": db.query(WhatsappBot).count(),
        "total_agents": db.query(VapiAgent).count(),
        "total_contacts": db.query(Contact).count(),
        "total_orders": db.query(Order).count(),
        "total_reservations": db.query(Reservation).count(),
        "messages_today": db.query(ChatHistory).filter(ChatHistory.created_at >= today_start, ChatHistory.role == "user").count(),
        "calls_today": db.query(Call).filter(Call.call_date >= today_start).count(),
    }

# ========== QR Code Generation for Table Orders ==========
@router.get("/bots/whatsapp/{bot_id}/qr-codes")
def get_qr_codes(bot_id: int, tables: int = 10, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Generate QR code URLs for each table of a restaurant bot."""
    bot = db.query(WhatsappBot).filter(WhatsappBot.id == bot_id, WhatsappBot.owner_id == current_user.id).first()
    if not bot:
        raise HTTPException(404, "Bot not found")
    domain = os.getenv("DOMAIN", "localhost:8000")
    qr_codes = []
    for i in range(1, tables + 1):
        qr_url = f"https://{domain}/qr/{bot_id}/{i}"
        wa_link = f"https://wa.me/{bot.phone_number_id}?text=TABLE_{i}"
        qr_codes.append({
            "table": i,
            "qr_url": qr_url,
            "whatsapp_link": wa_link
        })
    return {"bot_name": bot.name, "qr_codes": qr_codes}

