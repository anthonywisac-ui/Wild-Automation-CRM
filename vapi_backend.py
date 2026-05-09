import os
import json
import hmac
import hashlib
import re
from datetime import datetime
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Request, Header
from sqlalchemy.orm import Session
from pydantic import BaseModel
from db import get_db, User, VapiAgent, Contact, Call
from auth import get_current_user

router = APIRouter(prefix="/api/vapi", tags=["Vapi"])

# ========== Pydantic Models ==========
class VapiAgentCreate(BaseModel):
    name: str
    vapi_api_key: str
    vapi_agent_id: str
    phone_number: str
    system_prompt: str
    voice: str = "Alloy"
    first_message: str
    webhook_url: str
    crm_sync_enabled: bool = False

class VapiAgentUpdate(BaseModel):
    name: Optional[str] = None
    vapi_api_key: Optional[str] = None
    vapi_agent_id: Optional[str] = None
    phone_number: Optional[str] = None
    system_prompt: Optional[str] = None
    voice: Optional[str] = None
    first_message: Optional[str] = None
    webhook_url: Optional[str] = None
    crm_sync_enabled: Optional[bool] = None

# ========== Helper: Extract name from transcript ==========
def extract_name_from_transcript(transcript: str) -> str:
    """Simple regex to find name like 'my name is X' or 'I am X'"""
    patterns = [
        r"my name is ([A-Z][a-z]+)",
        r"i am ([A-Z][a-z]+)",
        r"this is ([A-Z][a-z]+)",
        r"i'm ([A-Z][a-z]+)"
    ]
    for pattern in patterns:
        match = re.search(pattern, transcript, re.IGNORECASE)
        if match:
            return match.group(1).capitalize()
    return "Unknown"

def detect_sentiment(transcript: str) -> str:
    """Simple keyword-based sentiment for outcome"""
    positive = ["interested", "yes", "sure", "absolutely", "definitely", "love", "great"]
    negative = ["no", "not interested", "busy", "later", "maybe", "cancel"]
    transcript_lower = transcript.lower()
    for word in positive:
        if word in transcript_lower:
            return "Resolved"
    for word in negative:
        if word in transcript_lower:
            return "Follow-up"
    return "Resolved"  # default

# ========== HMAC Signature Verification ==========
def verify_vapi_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify Vapi webhook signature using HMAC-SHA256"""
    if not signature or not secret:
        return False
    computed = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed, signature)

# ========== Endpoints ==========
@router.post("/agents")
def create_agent(
    agent_data: VapiAgentCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # Check duplicate name
    existing = db.query(VapiAgent).filter(
        VapiAgent.owner_id == current_user.id,
        VapiAgent.name == agent_data.name
    ).first()
    if existing:
        raise HTTPException(400, "Agent name already exists")
    
    new_agent = VapiAgent(
        owner_id=current_user.id,
        name=agent_data.name,
        vapi_api_key=agent_data.vapi_api_key,
        vapi_agent_id=agent_data.vapi_agent_id,
        phone_number_id=agent_data.phone_number,  # using same field
        first_message=agent_data.first_message,
        system_prompt=agent_data.system_prompt,
        voice=agent_data.voice,
        crm_sync=agent_data.crm_sync_enabled,
        webhook_url=agent_data.webhook_url,
        status="Draft",
        total_calls=0,
        conversion_rate=0.0
    )
    db.add(new_agent)
    db.commit()
    db.refresh(new_agent)
    return {"id": new_agent.id, "message": "Agent created successfully"}

@router.get("/agents")
def list_agents(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    agents = db.query(VapiAgent).filter(VapiAgent.owner_id == current_user.id).all()
    result = []
    from sqlalchemy import func
    
    for agent in agents:
        # ✅ FIX #3: Use SQL aggregation for efficiency (Phase 3)
        total_calls = db.query(func.count(Call.id)).filter(Call.agent == agent.name).scalar() or 0
        resolved_calls = db.query(func.count(Call.id)).filter(Call.agent == agent.name, Call.outcome == "Resolved").scalar() or 0
        conv_rate = round((resolved_calls / total_calls * 100) if total_calls else 0, 1)
        
        result.append({
            "id": agent.id,
            "name": agent.name,
            "status": agent.status,
            "total_calls": total_calls,
            "conversion_rate": conv_rate,
            "last_call": agent.last_call.isoformat() if agent.last_call else None
        })
    return result

@router.put("/agents/{agent_id}")
def update_agent(
    agent_id: int,
    agent_data: VapiAgentUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    agent = db.query(VapiAgent).filter(VapiAgent.id == agent_id, VapiAgent.owner_id == current_user.id).first()
    if not agent:
        raise HTTPException(404, "Agent not found")
    update_data = agent_data.dict(exclude_unset=True)
    for key, value in update_data.items():
        if key == "crm_sync_enabled":
            setattr(agent, "crm_sync", value)
        elif key == "phone_number":
            setattr(agent, "phone_number_id", value)
        else:
            setattr(agent, key, value)
    db.commit()
    db.refresh(agent)
    return {"message": "Agent updated"}

@router.delete("/agents/{agent_id}")
def delete_agent(
    agent_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    agent = db.query(VapiAgent).filter(VapiAgent.id == agent_id, VapiAgent.owner_id == current_user.id).first()
    if not agent:
        raise HTTPException(404, "Agent not found")
    db.delete(agent)
    db.commit()
    return {"message": "Agent deleted"}

@router.post("/agents/{agent_id}/test-call")
def test_call(
    agent_id: int,
    payload: dict,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    agent = db.query(VapiAgent).filter(VapiAgent.id == agent_id, VapiAgent.owner_id == current_user.id).first()
    if not agent:
        raise HTTPException(404, "Agent not found")
    phone = payload.get("phone")
    if not phone:
        raise HTTPException(400, "Phone number required")
    # Here you would call Vapi API to initiate call using agent.vapi_api_key and agent.vapi_agent_id
    # For now, we simulate success
    return {"message": f"Test call initiated to {phone} (simulated)"}

# ========== Webhook Endpoint (no auth, uses HMAC) ==========
@router.post("/webhook")
async def vapi_webhook(
    request: Request,
    x_vapi_signature: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    # Read raw body once; parse manually to avoid double-consuming the stream
    body = await request.body()
    payload = json.loads(body)
    
    # Get Vapi secret from env (you should set VAPI_WEBHOOK_SECRET)
    vapi_secret = os.getenv("VAPI_WEBHOOK_SECRET", "")
    if vapi_secret and x_vapi_signature:
        if not verify_vapi_signature(body, x_vapi_signature, vapi_secret):
            raise HTTPException(401, "Invalid signature")
    
    event_type = payload.get("type")
    if not event_type:
        return {"status": "ignored"}
    
    # Extract call data
    call_data = payload.get("call", {})
    caller_number = call_data.get("customer", {}).get("number")
    duration = call_data.get("duration", 0)  # in seconds
    transcript = payload.get("transcript", "")
    agent_id = call_data.get("assistantId")  # Vapi agent ID (not our internal ID)
    
    # Find our agent by vapi_agent_id
    agent = db.query(VapiAgent).filter(VapiAgent.vapi_agent_id == agent_id).first() if agent_id else None
    
    # Process call.ended event
    if event_type == "call.ended" and caller_number:
        # Update agent stats
        if agent:
            agent.total_calls = (agent.total_calls or 0) + 1
            agent.last_call = datetime.utcnow()
            db.commit()
        
        # Auto-create/update contact if CRM sync enabled
        if agent and agent.crm_sync:
            # Find or create contact
            contact = db.query(Contact).filter(Contact.phone == caller_number).first()
            if not contact:
                # Extract name from transcript
                name = extract_name_from_transcript(transcript)
                contact = Contact(
                    owner_id=agent.owner_id,
                    first_name=name,
                    last_name="",
                    phone=caller_number,
                    status="Hot Lead" if duration > 120 else "Warm",
                    source="Vapi_Call",
                    notes=transcript[:500]
                )
                db.add(contact)
            else:
                # Update contact
                if duration > 120 and contact.status != "Hot Lead":
                    contact.status = "Hot Lead"
                contact.notes = (contact.notes or "") + f"\n[{datetime.utcnow()}] Call duration {duration}s\n{transcript[:200]}"
            db.commit()
        
        # Create Call record only when we know the owner
        if agent:
            outcome = detect_sentiment(transcript)
            call_record = Call(
                owner_id=agent.owner_id,
                contact_name=extract_name_from_transcript(transcript) if transcript else "Unknown",
                phone=caller_number,
                direction="Inbound",
                duration_minutes=round(duration / 60, 1),
                outcome=outcome,
                agent=agent.name,
                notes=transcript[:500]
            )
            db.add(call_record)
            db.commit()
    
    # For other events (call.started, transcript.ready), just log
    return {"status": "ok", "event": event_type}