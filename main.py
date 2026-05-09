import os
import importlib
import logging
import json
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session
from db import (
    get_db, get_user_by_username, authenticate_user,
    create_access_token, create_user, User
)
import uvicorn
from db import populate_dummy_data, SessionLocal, migrate_db
from setup_bot import setup_platform

# ========== Configure Logging ==========
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Ensure tables are created (Bug Fix for Railway logs)
migrate_db()

db = SessionLocal()
try:
    logger.info("Running startup admin check via populate_dummy_data()...")
    populate_dummy_data(db)
    logger.info("Startup admin check complete.")
except Exception as _e:
    logger.error(f"populate_dummy_data() failed on startup: {_e}")
finally:
    db.close()

app = FastAPI(title="WhatsApp Bot Platform", version="2.0.0")
static_dir = os.path.join(os.path.dirname(__file__), "cms", "static")

# ========== Security Middleware ==========
@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response

# ========== CORS Configuration ==========
allowed_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:8000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from vapi_backend import router as vapi_router
from crm_backend import router as crm_router
from cms.routes import router as cms_router
from whatsapp_router import router as whatsapp_router
from auth import get_current_user

app.include_router(vapi_router)
app.include_router(crm_router)
app.include_router(cms_router)
app.include_router(whatsapp_router)

@app.get("/")
async def root():
    """Serve the CRM dashboard as the landing page."""
    index_path = os.path.join(static_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "Welcome to Wild Automations Bot Platform. CRM not found."}

@app.get("/crm")
async def crm_redirect():
    return RedirectResponse(url="/")

security = HTTPBearer()

class LoginRequest(BaseModel):
    username: str
    password: str

class RegisterRequest(BaseModel):
    username: str
    password: str

@app.post("/auth/login")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    user = authenticate_user(db, req.username, req.password)
    if not user:
        logger.warning(f"Failed login attempt for username: {req.username}")
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_access_token({"sub": user.username})
    logger.info(f"User {req.username} logged in successfully")
    return {"access_token": token, "token_type": "bearer"}

@app.post("/auth/register")
def register(req: RegisterRequest, db: Session = Depends(get_db)):
    user = create_user(db, req.username, req.password, role="user")
    if not user:
        logger.warning(f"Registration failed - username already exists: {req.username}")
        raise HTTPException(status_code=400, detail="Username already exists")
    logger.info(f"New user registered: {req.username}")
    return {"message": "User created successfully", "username": user.username}

# ========== FIX #1: Add /cms/register endpoint ==========
@app.post("/cms/register")
def cms_register(req: RegisterRequest, db: Session = Depends(get_db)):
    """Wrapper for registration to match frontend expectations"""
    user = create_user(db, req.username, req.password, role="user")
    if not user:
        raise HTTPException(status_code=400, detail="Username already exists")
    logger.info(f"New user registered via CMS: {req.username}")
    return {"message": "Registration successful", "username": user.username}

@app.get("/auth/me")
def auth_me(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
):
    from db import decode_token
    payload = decode_token(credentials.credentials)
    if not payload:
        raise HTTPException(401, "Invalid token")
    user = get_user_by_username(db, payload.get("sub"))
    if not user:
        raise HTTPException(404, "User not found")
    return {"username": user.username, "role": user.role,
            "user_id": user.id, "bots": user.bots}

# ========== Stripe Webhook ==========
@app.post("/api/stripe/webhook")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    endpoint_secret = os.getenv("STRIPE_WEBHOOK_SECRET")

    import stripe
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, endpoint_secret) if endpoint_secret else None
        if not event:
            event = json.loads(payload)
    except Exception as e:
        logger.error(f"Stripe Webhook Error: {e}")
        raise HTTPException(status_code=400, detail="Invalid payload")

    etype = event.get("type") if isinstance(event, dict) else event.type
    if etype == "checkout.session.completed":
        session_obj = event.get("data", {}).get("object") if isinstance(event, dict) else event.data.object
        order_id = session_obj.get("metadata", {}).get("order_id")
        
        if order_id:
            from db import Order
            order = db.query(Order).filter(Order.id == int(order_id)).first()
            if order:
                order.status = "Confirmed"
                db.commit()
                logger.info(f"✅ Order #{order_id} auto-confirmed via Stripe")

    return {"status": "success"}

ALLOW_DEBUG = os.getenv("ALLOW_DEBUG", "false").lower() == "true"
if ALLOW_DEBUG:
    @app.post("/fix-admin")
    def fix_admin(req: Request, db: Session = Depends(get_db)):
        """Securely reset admin password if debug is enabled and secret matches"""
        admin_secret = os.getenv("ADMIN_SECRET")
        provided_secret = req.headers.get("X-Admin-Secret")
        
        if not admin_secret or provided_secret != admin_secret:
            logger.warning("Unauthorized attempt to use /fix-admin")
            raise HTTPException(status_code=403, detail="Forbidden")

        from db import hash_password
        admin_password = os.getenv("ADMIN_PASSWORD", "admin123")
        u = get_user_by_username(db, "admin")
        if u:
            u.hashed_password = hash_password(admin_password)
            db.commit()
            logger.info("Admin password reset via /fix-admin")
            return {"msg": "Admin password reset successfully"}
        
        create_user(db, "admin", admin_password, role="admin")
        logger.info("Admin user created via /fix-admin")
        return {"msg": "Admin created"}

def get_allowed_bot_types():
    bots_dir = "bots"
    if not os.path.exists(bots_dir):
        return []
    return [d for d in os.listdir(bots_dir)
            if os.path.isdir(os.path.join(bots_dir, d))]

BOT_TYPE = os.getenv("BOT_TYPE", "restaurant")
allowed = get_allowed_bot_types()
if BOT_TYPE in allowed:
    try:
        bot_module = importlib.import_module(f"bots.{BOT_TYPE}.main")
        # Mount at /bot/<type> to avoid shadowing platform routes like /webhook
        app.mount(f"/bot/{BOT_TYPE}", bot_module.app)
        logger.info(f"✅ Bot '{BOT_TYPE}' loaded at /bot/{BOT_TYPE}")
    except Exception as e:
        logger.warning(f"⚠️ Bot sub-app not mounted (using integrated flow instead): {e}")
else:
    logger.warning(f"⚠️ BOT_TYPE '{BOT_TYPE}' not found in bots/")

if os.path.exists(static_dir):
    app.mount("/cms/static", StaticFiles(directory=static_dir, html=True),
              name="cms_static")

# ========== Startup Event ==========
@app.on_event("startup")
def create_initial_admin():
    # Validate required environment variables
    required_env_vars = ["JWT_SECRET_KEY"]
    if os.getenv("ENVIRONMENT") == "production":
        required_env_vars.extend(["VAPI_WEBHOOK_SECRET"])

    for var in required_env_vars:
        if not os.getenv(var) or os.getenv(var) == "your-secret-key-change-me":
            logger.warning(f"⚠️ {var} not properly configured in .env")

    try:
        setup_platform()
        from db import load_customer_profiles_from_db
        load_customer_profiles_from_db()
    except Exception as e:
        logger.warning(f"Auto-setup failed: {e}")

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
