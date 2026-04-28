from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from db import get_db, User, get_user_by_username, create_user, WhatsappBot
from auth import verify_password, get_password_hash, create_access_token, decode_token
from pydantic import BaseModel
from typing import Optional
import subprocess
import json
import os

router = APIRouter(prefix="/cms", tags=["CMS"])
security = HTTPBearer()

# ---------- Admin dependency using db.py User ----------
def get_current_admin(credentials: HTTPAuthorizationCredentials = Depends(security), db: Session = Depends(get_db)):
    token = credentials.credentials
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")
    username = payload.get("sub")
    user = get_user_by_username(db, username)
    if not user or user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user

# ---------- Auth Models ----------
class LoginRequest(BaseModel):
    username: str
    password: str

@router.post("/login")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    from db import authenticate_user
    user = authenticate_user(db, req.username, req.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Not an admin user")
    token = create_access_token({"sub": user.username})
    return {"access_token": token, "token_type": "bearer"}

@router.get("/setup")
@router.post("/setup")
def setup_admin(db: Session = Depends(get_db)):
    """First-time setup – create admin user if none exists"""
    admin = db.query(User).filter(User.role == "admin").first()
    if not admin:
        default_password = os.getenv("ADMIN_PASSWORD", "admin123")
        user = create_user(db, "admin", default_password, role="admin")
        if user:
            return {"message": f"Admin created. Username: admin, Password: {default_password}"}
    return {"message": "Admin already exists"}

# ---------- Bot CRUD ----------
class BotConfigCreate(BaseModel):
    name: str
    bot_type: str
    config_json: dict

@router.post("/bots")
def create_bot(bot: BotConfigCreate, db: Session = Depends(get_db), current_admin: User = Depends(get_current_admin)):
    # Check if bot already exists in filesystem
    bot_path = f"bots/{bot.name}"
    if os.path.exists(bot_path):
        raise HTTPException(status_code=400, detail="Bot folder already exists")
    # Save config to database
    existing = db.query(WhatsappBot).filter(WhatsappBot.name == bot.name).first()
    if existing:
        raise HTTPException(status_code=400, detail="Bot config already exists")
    
    # Create new WhatsappBot record
    db_bot = WhatsappBot(
        owner_id=current_admin.id,
        name=bot.name,
        bot_type=bot.bot_type,
        config_json=json.dumps(bot.config_json)
    )
    db.add(db_bot)
    db.commit()
    db.refresh(db_bot)
    # Call generator script
    config_file = f"/tmp/{bot.name}_config.json"
    with open(config_file, "w") as f:
        json.dump(bot.config_json, f, indent=2)
    result = subprocess.run(["python", "generate_bot.py", config_file], capture_output=True, text=True)
    os.remove(config_file)
    if result.returncode != 0:
        # Rollback
        db.delete(db_bot)
        db.commit()
        raise HTTPException(status_code=500, detail=f"Generator failed: {result.stderr}")
    return {"message": f"Bot {bot.name} created", "output": result.stdout}

@router.get("/bots", dependencies=[Depends(get_current_admin)])
def list_bots(db: Session = Depends(get_db)):
    bots = db.query(WhatsappBot).all()
    return [{"id": b.id, "name": b.name, "type": b.bot_type, "created": b.created_at} for b in bots]

@router.delete("/bots/{bot_name}", dependencies=[Depends(get_current_admin)])
def delete_bot(bot_name: str, db: Session = Depends(get_db)):
    import shutil
    bot_path = f"bots/{bot_name}"
    if not os.path.exists(bot_path):
        raise HTTPException(status_code=404, detail="Bot folder not found")
    shutil.rmtree(bot_path)
    db_bot = db.query(WhatsappBot).filter(WhatsappBot.name == bot_name).first()
    if db_bot:
        db.delete(db_bot)
    db.commit()
    return {"message": f"Bot {bot_name} deleted"}

# ---------- Assign bot to user ----------
@router.post("/assign-bot")
def assign_bot(bot_name: str, username: str, current_user: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    user = get_user_by_username(db, username)
    if not user:
        raise HTTPException(404, "User not found")
    current_bots = user.bots
    if bot_name not in current_bots:
        current_bots.append(bot_name)
        user.bots = current_bots
        db.commit()
    return {"msg": f"Bot {bot_name} assigned to {username}"}


# ============================================================
# wwebjs Session Management (Own Number — QR Scan)
# ============================================================

class WwebjsBotCreate(BaseModel):
    name:              str
    bot_type:          str   = "restaurant"
    business_name:     str   = ""
    manager_number:    str   = ""
    language:          str   = "en"
    ai_provider:       str   = "groq"
    ai_api_key:        str   = ""
    wwebjs_bridge_url: Optional[str] = None   # if blank → uses server default


@router.post("/bots/wwebjs/create",
             summary="Create a wwebjs bot (own number — QR scan)")
async def create_wwebjs_bot(
    data: WwebjsBotCreate,
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    """
    Creates a new bot record configured for the wwebjs provider.
    After creation, call /cms/bots/wwebjs/{bot_id}/start-session to get the QR.
    """
    existing = db.query(WhatsappBot).filter(WhatsappBot.name == data.name).first()
    if existing:
        raise HTTPException(400, f"Bot name '{data.name}' already exists")

    bridge_url = (
        data.wwebjs_bridge_url
        or os.getenv("WWEBJS_BRIDGE_URL", "http://localhost:3000")
    )

    bot = WhatsappBot(
        owner_id          = current_admin.id,
        name              = data.name,
        bot_type          = data.bot_type,
        business_name     = data.business_name,
        manager_number    = data.manager_number,
        language          = data.language,
        ai_provider       = data.ai_provider,
        ai_api_key        = data.ai_api_key,
        provider          = "wwebjs",
        wwebjs_bridge_url = bridge_url,
        # session name set after save (uses bot.id)
    )
    db.add(bot)
    db.commit()
    db.refresh(bot)

    # Set stable session name = bot_{id}
    bot.wwebjs_session = f"bot_{bot.id}"
    db.commit()

    return {
        "message":  f"wwebjs bot '{data.name}' created",
        "bot_id":   bot.id,
        "session":  bot.wwebjs_session,
        "next_step": f"POST /cms/bots/wwebjs/{bot.id}/start-session",
    }


@router.post("/bots/wwebjs/{bot_id}/start-session",
             summary="Start wwebjs session — triggers QR generation")
async def start_wwebjs_session(
    bot_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_admin),
):
    """
    Tells the wa-bridge to start the session for this bot.
    Poll GET /cms/bots/wwebjs/{bot_id}/qr until you get a QR string,
    then render it in the browser with qrcode.js.
    """
    bot = db.query(WhatsappBot).filter(WhatsappBot.id == bot_id).first()
    if not bot:
        raise HTTPException(404, "Bot not found")
    if bot.provider != "wwebjs":
        raise HTTPException(400, "This bot is not configured as a wwebjs bot")

    from providers.wwebjs import bridge_start_session
    bridge_url = bot.wwebjs_bridge_url or os.getenv("WWEBJS_BRIDGE_URL", "http://localhost:3000")

    try:
        result = await bridge_start_session(bot.wwebjs_session, bridge_url)
        return {"session": bot.wwebjs_session, "bridge_response": result}
    except Exception as exc:
        raise HTTPException(502, f"Bridge unreachable: {exc}")


@router.get("/bots/wwebjs/{bot_id}/qr",
            summary="Get current QR code string for browser rendering")
async def get_wwebjs_qr(
    bot_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_admin),
):
    """
    Returns the raw QR string from the bridge.
    Frontend renders it with qrcode.js — QR never passes through an
    external image service.

    Poll this endpoint every 2 seconds after start-session.
    Status transitions: STARTING → SCAN_QR_CODE (QR available) → CONNECTED
    """
    bot = db.query(WhatsappBot).filter(WhatsappBot.id == bot_id).first()
    if not bot:
        raise HTTPException(404, "Bot not found")
    if bot.provider != "wwebjs":
        raise HTTPException(400, "Not a wwebjs bot")

    from providers.wwebjs import bridge_get_qr, bridge_get_status
    bridge_url = bot.wwebjs_bridge_url or os.getenv("WWEBJS_BRIDGE_URL", "http://localhost:3000")

    try:
        status = await bridge_get_status(bot.wwebjs_session, bridge_url)

        if status == "CONNECTED":
            # Update bot status in DB
            bot.status = "active"
            db.commit()
            return {"status": "CONNECTED", "qr": None,
                    "message": "Already connected — no QR needed"}

        qr_data = await bridge_get_qr(bot.wwebjs_session, bridge_url)
        qr      = qr_data.get("qr")

        return {
            "status":  status,
            "qr":      qr,        # raw QR string — render with qrcode.js
            "session": bot.wwebjs_session,
        }

    except Exception as exc:
        raise HTTPException(502, f"Bridge error: {exc}")


@router.get("/bots/wwebjs/{bot_id}/status",
            summary="Get connection status of a wwebjs bot")
async def get_wwebjs_status(
    bot_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_admin),
):
    bot = db.query(WhatsappBot).filter(WhatsappBot.id == bot_id).first()
    if not bot:
        raise HTTPException(404, "Bot not found")
    if bot.provider != "wwebjs":
        raise HTTPException(400, "Not a wwebjs bot")

    from providers.wwebjs import bridge_get_status
    bridge_url = bot.wwebjs_bridge_url or os.getenv("WWEBJS_BRIDGE_URL", "http://localhost:3000")

    try:
        status = await bridge_get_status(bot.wwebjs_session, bridge_url)

        # Sync status back to DB
        if status == "CONNECTED" and bot.status != "active":
            bot.status = "active"
            db.commit()
        elif status in ("DISCONNECTED", "AUTH_FAILURE", "ERROR") and bot.status == "active":
            bot.status = "disconnected"
            db.commit()

        return {
            "bot_id":  bot_id,
            "name":    bot.name,
            "session": bot.wwebjs_session,
            "status":  status,
        }
    except Exception as exc:
        raise HTTPException(502, f"Bridge error: {exc}")


@router.delete("/bots/wwebjs/{bot_id}/disconnect",
               summary="Logout and delete wwebjs session (keeps bot config)")
async def disconnect_wwebjs_session(
    bot_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_admin),
):
    """
    Logs out the WhatsApp session from wa-bridge and deletes local session files.
    Bot config in the database is kept — call start-session to reconnect.
    """
    bot = db.query(WhatsappBot).filter(WhatsappBot.id == bot_id).first()
    if not bot:
        raise HTTPException(404, "Bot not found")
    if bot.provider != "wwebjs":
        raise HTTPException(400, "Not a wwebjs bot")

    from providers.wwebjs import bridge_delete_session
    bridge_url = bot.wwebjs_bridge_url or os.getenv("WWEBJS_BRIDGE_URL", "http://localhost:3000")

    try:
        ok = await bridge_delete_session(bot.wwebjs_session, bridge_url)
        bot.status = "disconnected"
        db.commit()
        return {"success": ok, "message": "Session disconnected. Scan QR to reconnect."}
    except Exception as exc:
        raise HTTPException(502, f"Bridge error: {exc}")