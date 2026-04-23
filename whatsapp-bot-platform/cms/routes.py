from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from db import get_db, User, get_user_by_username, create_user, BotConfig, get_bot_config, create_bot_config, delete_bot_config
from auth import verify_password, get_password_hash, create_access_token, decode_token
from pydantic import BaseModel
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

@router.post("/bots", dependencies=[Depends(get_current_admin)])
def create_bot(bot: BotConfigCreate, db: Session = Depends(get_db)):
    # Check if bot already exists in filesystem
    bot_path = f"bots/{bot.name}"
    if os.path.exists(bot_path):
        raise HTTPException(status_code=400, detail="Bot folder already exists")
    # Save config to database
    existing = get_bot_config(db, bot.name)
    if existing:
        raise HTTPException(status_code=400, detail="Bot config already exists")
    db_bot = create_bot_config(db, bot.name, bot.bot_type, bot.config_json)
    # Call generator script
    config_file = f"/tmp/{bot.name}_config.json"
    with open(config_file, "w") as f:
        json.dump(bot.config_json, f, indent=2)
    result = subprocess.run(["python", "generate_bot.py", config_file], capture_output=True, text=True)
    os.remove(config_file)
    if result.returncode != 0:
        # Rollback
        delete_bot_config(db, bot.name)
        db.commit()
        raise HTTPException(status_code=500, detail=f"Generator failed: {result.stderr}")
    return {"message": f"Bot {bot.name} created", "output": result.stdout}

@router.get("/bots", dependencies=[Depends(get_current_admin)])
def list_bots(db: Session = Depends(get_db)):
    bots = db.query(BotConfig).all()
    return [{"id": b.id, "name": b.name, "type": b.bot_type, "created": b.created_at} for b in bots]

@router.delete("/bots/{bot_name}", dependencies=[Depends(get_current_admin)])
def delete_bot(bot_name: str, db: Session = Depends(get_db)):
    import shutil
    bot_path = f"bots/{bot_name}"
    if not os.path.exists(bot_path):
        raise HTTPException(status_code=404, detail="Bot folder not found")
    shutil.rmtree(bot_path)
    delete_bot_config(db, bot_name)
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