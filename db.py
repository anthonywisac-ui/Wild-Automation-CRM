# database.py - FIXED VERSION (FIX #2, #15)
import os
import json
from datetime import datetime, timedelta
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Text, ForeignKey, Boolean, inspect, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from passlib.context import CryptContext
from jose import JWTError, jwt
from typing import Optional, List
import secrets
from utils import get_order_total, get_delivery_fee

# ========== Database Setup ==========
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    DATABASE_URL = "sqlite:///./platform.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# ========== Password & JWT ==========
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)
SECRET_KEY = os.getenv("JWT_SECRET_KEY", secrets.token_urlsafe(32))
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)

def create_access_token(data: dict) -> str:
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode = data.copy()
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None

# ========== Define all models ==========
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    role = Column(String, default="user")
    bots_json = Column(Text, default="[]")
    is_suspended = Column(Boolean, default=False)
    assigned_bots = Column(Text, default="[]")
    assigned_vapi_agents = Column(Text, default="[]")
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # ✅ FIX #2: Add dedicated AI config fields
    ai_provider = Column(String, default="groq")
    ai_api_key = Column(String, default="") # legacy/selected
    groq_api_key = Column(String, default="")
    gemini_api_key = Column(String, default="")
    openai_api_key = Column(String, default="")
    default_voice = Column(String, default="Alloy")
    default_first_message = Column(String, default="Hello, how can I help you?")

    contacts = relationship("Contact", back_populates="owner", cascade="all, delete-orphan")
    deals = relationship("Deal", back_populates="owner", cascade="all, delete-orphan")
    calls = relationship("Call", back_populates="owner", cascade="all, delete-orphan")
    vapi_agents = relationship("VapiAgent", back_populates="owner", cascade="all, delete-orphan")
    whatsapp_bots = relationship("WhatsappBot", back_populates="owner", cascade="all, delete-orphan")

    @property
    def bots(self) -> List[str]:
        return json.loads(self.bots_json or "[]")

    @bots.setter
    def bots(self, value: List[str]):
        self.bots_json = json.dumps(value)

    @property
    def assigned_bots_list(self) -> List[str]:
        return json.loads(self.assigned_bots or "[]")

    @assigned_bots_list.setter
    def assigned_bots_list(self, value: List[str]):
        self.assigned_bots = json.dumps(value)

    @property
    def assigned_vapi_list(self) -> List[str]:
        return json.loads(self.assigned_vapi_agents or "[]")

    @assigned_vapi_list.setter
    def assigned_vapi_list(self, value: List[str]):
        self.assigned_vapi_agents = json.dumps(value)

class Contact(Base):
    __tablename__ = "contacts"
    id = Column(Integer, primary_key=True, index=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    first_name = Column(String, default="")
    last_name = Column(String, default="")
    company = Column(String, default="")
    email = Column(String, default="")
    phone = Column(String, default="")
    status = Column(String, default="New")
    source = Column(String, default="Manual")
    notes = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    owner = relationship("User", back_populates="contacts")

class Deal(Base):
    __tablename__ = "deals"
    id = Column(Integer, primary_key=True, index=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String, default="New Deal")
    company = Column(String, default="")
    contact_name = Column(String, default="")
    value = Column(Float, default=0.0)
    stage = Column(String, default="Discovery")
    probability = Column(Integer, default=20)
    expected_close = Column(DateTime, nullable=True)
    notes = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    owner = relationship("User", back_populates="deals")

class Call(Base):
    __tablename__ = "calls"
    id = Column(Integer, primary_key=True, index=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    contact_name = Column(String, default="Unknown")
    phone = Column(String, default="")
    direction = Column(String, default="Inbound")
    duration_minutes = Column(Float, default=0.0)
    outcome = Column(String, default="Resolved")
    agent = Column(String, default="")
    notes = Column(Text, default="")
    call_date = Column(DateTime, default=datetime.utcnow)
    owner = relationship("User", back_populates="calls")

class VapiAgent(Base):
    __tablename__ = "vapi_agents"
    id = Column(Integer, primary_key=True, index=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String, nullable=False)
    vapi_api_key = Column(String, default="")
    vapi_agent_id = Column(String, default="")
    phone_number_id = Column(String, default="")
    first_message = Column(Text, default="")
    system_prompt = Column(Text, default="")
    voice = Column(String, default="Alloy")
    crm_sync = Column(Boolean, default=False)
    webhook_url = Column(String, default="")
    status = Column(String, default="Draft")
    total_calls = Column(Integer, default=0)
    conversion_rate = Column(Float, default=0.0)
    last_call = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    owner = relationship("User", back_populates="vapi_agents")

class WhatsappBot(Base):
    __tablename__ = "whatsapp_bots"
    id = Column(Integer, primary_key=True, index=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String, unique=True, index=True, nullable=False)
    bot_type = Column(String, default="restaurant") # restaurant, salon, agency, hvac, lawyer, etc.
    business_niche = Column(String, default="general")
    meta_token = Column(String, default="")
    phone_number_id = Column(String, default="")
    waba_id = Column(String, default="")
    verify_token = Column(String, default="")
    ai_provider = Column(String, default="groq")
    ai_api_key = Column(String, default="")
    manager_number = Column(String, default="")
    google_sheet_id = Column(String, default="")
    google_creds_json = Column(Text, default="")
    language = Column(String, default="en")
    business_name = Column(String, default="")
    system_prompt = Column(Text, default="")
    stripe_secret_key = Column(String, default="")
    webhook_url = Column(String, default="")
    forwarding_url = Column(String, default="") # Outgoing to external engine
    
    # ✅ Configurable business logic (Phase 3)
    tax_rate = Column(Float, default=0.08)  # Default 8%
    delivery_fee = Column(Float, default=0.0)
    config_json = Column(Text, default="{}") # Stores Menu, Rules, Deals
    
    created_at = Column(DateTime, default=datetime.utcnow)
    owner = relationship("User", back_populates="whatsapp_bots")

class WebhookEvent(Base):
    __tablename__ = "webhook_events"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    type = Column(String) # whatsapp, vapi, custom
    payload_json = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    @property
    def payload(self) -> dict:
        return json.loads(self.payload_json or "{}")

    @payload.setter
    def payload(self, value: dict):
        self.payload_json = json.dumps(value)

class SessionState(Base):
    __tablename__ = "session_states"
    id = Column(Integer, primary_key=True, index=True)
    bot_id = Column(Integer, ForeignKey("whatsapp_bots.id"), nullable=True)
    sender_number = Column(String, index=True, nullable=False)
    state_json = Column(Text, default="{}")
    last_activity = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @property
    def data(self) -> dict:
        return json.loads(self.state_json or "{}")

    @data.setter
    def data(self, value: dict):
        self.state_json = json.dumps(value)

class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True, index=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    customer_number = Column(String, index=True)
    items_json = Column(Text, default="[]")
    total_amount = Column(Float, default=0.0)
    tax_amount = Column(Float, default=0.0)
    delivery_amount = Column(Float, default=0.0)
    grand_total = Column(Float, default=0.0)
    status = Column(String, default="Pending")
    delivery_type = Column(String, default="delivery") # delivery, pickup
    created_at = Column(DateTime, default=datetime.utcnow)

    @property
    def items(self) -> list:
        return json.loads(self.items_json or "[]")

    @items.setter
    def items(self, value: list):
        self.items_json = json.dumps(value)

class ChatHistory(Base):
    __tablename__ = "chat_history"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    customer_phone = Column(String, default="", index=True)  # WhatsApp sender number
    role = Column(String)  # user, assistant
    content = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

class Reservation(Base):
    __tablename__ = "reservations"
    id = Column(Integer, primary_key=True, index=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    bot_id = Column(Integer, ForeignKey("whatsapp_bots.id"), nullable=True)
    customer_phone = Column(String, index=True, default="")
    customer_name = Column(String, default="")
    party_size = Column(Integer, default=2)
    reservation_date = Column(String, default="")
    reservation_time = Column(String, default="")
    status = Column(String, default="Pending")  # Pending, Confirmed, Cancelled
    notes = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

class CustomerProfile(Base):
    __tablename__ = "customer_profiles"
    id = Column(Integer, primary_key=True, index=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    bot_id = Column(Integer, ForeignKey("whatsapp_bots.id"), nullable=True)
    phone = Column(String, index=True, nullable=False)
    name = Column(String, default="")
    lang = Column(String, default="en")
    delivery_type = Column(String, default="")
    address = Column(Text, default="")
    payment = Column(String, default="")
    order_history_json = Column(Text, default="[]")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @property
    def order_history(self) -> list:
        return json.loads(self.order_history_json or "[]")

    @order_history.setter
    def order_history(self, value: list):
        self.order_history_json = json.dumps(value)

class BotConfig(Base):
    __tablename__ = "bot_configs"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True, nullable=False)
    bot_type = Column(String, nullable=False)
    config_json = Column(Text, default="{}")
    vapi_agent_id = Column(String, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

# ========== FIX #15: Database Migration Helper ==========
def migrate_user_table(session):
    """Add new columns if they don't exist"""
    engine = session.get_bind()
    inspector = inspect(engine)
    
    if not inspector.has_table('users'):
        Base.metadata.create_all(bind=engine)
        return
    
    columns = [c['name'] for c in inspector.get_columns('users')]
    new_columns = {
        'ai_provider': "TEXT DEFAULT 'groq'",
        'ai_api_key': "TEXT DEFAULT ''",
        'groq_api_key': "TEXT DEFAULT ''",
        'gemini_api_key': "TEXT DEFAULT ''",
        'openai_api_key': "TEXT DEFAULT ''",
        'default_voice': "TEXT DEFAULT 'Alloy'",
        'default_first_message': "TEXT DEFAULT 'Hello, how can I help you?'"
    }
    
    with engine.connect() as conn:
        # Migration for Users table
        for col_name, col_def in new_columns.items():
            if col_name not in columns:
                if 'sqlite' in DATABASE_URL:
                    conn.execute(text(f"ALTER TABLE users ADD COLUMN {col_name} {col_def}"))
                else:
                    conn.execute(text(f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {col_name} {col_def}"))
                print(f"Added column to users: {col_name}")
        
        # Migration for WhatsappBot table
        bot_columns = [c['name'] for c in inspector.get_columns('whatsapp_bots')]
        new_bot_cols = {
            'tax_rate': "FLOAT DEFAULT 0.08",
            'delivery_fee': "FLOAT DEFAULT 0.0",
            'business_niche': "TEXT DEFAULT 'general'",
            'bot_type': "TEXT DEFAULT 'restaurant'",
            'forwarding_url': "TEXT DEFAULT ''",
            'config_json': "TEXT DEFAULT '{}'",
            'vapi_agent_id': "TEXT DEFAULT ''"
        }
        for col_name, col_def in new_bot_cols.items():
            if col_name not in bot_columns:
                if 'sqlite' in DATABASE_URL:
                    conn.execute(text(f"ALTER TABLE whatsapp_bots ADD COLUMN {col_name} {col_def}"))
                else:
                    conn.execute(text(f"ALTER TABLE whatsapp_bots ADD COLUMN IF NOT EXISTS {col_name} {col_def}"))
                print(f"Added column to whatsapp_bots: {col_name}")

        # Migration for SessionState table
        session_columns = [c['name'] for c in inspector.get_columns('session_states')]
        if 'bot_id' not in session_columns:
            if 'sqlite' in DATABASE_URL:
                conn.execute(text("ALTER TABLE session_states ADD COLUMN bot_id INTEGER"))
            else:
                conn.execute(text("ALTER TABLE session_states ADD COLUMN IF NOT EXISTS bot_id INTEGER"))
            print("Added column to session_states: bot_id")

        # Migration for ChatHistory table — add customer_phone
        if inspector.has_table('chat_history'):
            chat_cols = [c['name'] for c in inspector.get_columns('chat_history')]
            if 'customer_phone' not in chat_cols:
                if 'sqlite' in DATABASE_URL:
                    conn.execute(text("ALTER TABLE chat_history ADD COLUMN customer_phone TEXT DEFAULT ''"))
                else:
                    conn.execute(text("ALTER TABLE chat_history ADD COLUMN IF NOT EXISTS customer_phone TEXT DEFAULT ''"))
                print("Added column to chat_history: customer_phone")

        conn.commit()

    # Ensure all tables are created (includes new: reservations, customer_profiles)
    Base.metadata.create_all(bind=engine)


# ========== Create all tables (after all models defined) ==========
Base.metadata.create_all(bind=engine)

# Run migrations on database load
db_session = SessionLocal()
migrate_user_table(db_session)
db_session.close()

# ========== Database Session ==========
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ========== Helper Functions ==========
def create_user(db, username: str, password: str, role: str = "user") -> Optional[User]:
    if db.query(User).filter(User.username == username).first():
        return None
    hashed = hash_password(password)
    user = User(username=username, hashed_password=hashed, role=role)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user

def get_user_by_username(db, username: str) -> Optional[User]:
    return db.query(User).filter(User.username == username).first()

def authenticate_user(db, username: str, password: str) -> Optional[User]:
    user = get_user_by_username(db, username)
    if not user or not verify_password(password, user.hashed_password):
        return None
    if user.is_suspended:
        return None
    return user

def get_contacts(db, owner_id: int):
    return db.query(Contact).filter(Contact.owner_id == owner_id).all()

def create_contact(db, owner_id: int, data: dict):
    contact = Contact(owner_id=owner_id, **data)
    db.add(contact)
    db.commit()
    db.refresh(contact)
    return contact

def get_deals(db, owner_id: int):
    return db.query(Deal).filter(Deal.owner_id == owner_id).all()

def create_deal(db, owner_id: int, data: dict):
    deal = Deal(owner_id=owner_id, **data)
    db.add(deal)
    db.commit()
    db.refresh(deal)
    return deal

def get_calls(db, owner_id: int):
    return db.query(Call).filter(Call.owner_id == owner_id).all()

def create_call(db, owner_id: int, data: dict):
    call = Call(owner_id=owner_id, **data)
    db.add(call)
    db.commit()
    db.refresh(call)
    return call

def get_vapi_agents(db, owner_id: int):
    return db.query(VapiAgent).filter(VapiAgent.owner_id == owner_id).all()

def create_vapi_agent(db, owner_id: int, data: dict):
    agent = VapiAgent(owner_id=owner_id, **data)
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return agent

def get_whatsapp_bots(db, owner_id: int):
    return db.query(WhatsappBot).filter(WhatsappBot.owner_id == owner_id).all()

def create_whatsapp_bot(db, owner_id: int, data: dict):
    bot = WhatsappBot(owner_id=owner_id, **data)
    db.add(bot)
    db.commit()
    db.refresh(bot)
    return bot

def get_bot_config(db, name: str):
    return db.query(BotConfig).filter(BotConfig.name == name).first()

def create_bot_config(db, name: str, bot_type: str, config_json: str):
    bot = BotConfig(name=name, bot_type=bot_type, config_json=config_json)
    db.add(bot)
    db.commit()
    db.refresh(bot)
    return bot

def delete_bot_config(db, name: str):
    bot = db.query(BotConfig).filter(BotConfig.name == name).first()
    if bot:
        db.delete(bot)
        db.commit()
    return bot

# ========== Session & Order Helpers ==========
def get_session_data(db, sender_number: str) -> dict:
    session = db.query(SessionState).filter(SessionState.sender_number == sender_number).first()
    return session.data if session else {}

def save_session_data(db, sender_number: str, data: dict):
    session = db.query(SessionState).filter(SessionState.sender_number == sender_number).first()
    if not session:
        session = SessionState(sender_number=sender_number)
        db.add(session)
    session.data = data
    db.commit()

def save_new_order(db, owner_id: int, customer_number: str, order_data: dict, bot: WhatsappBot):
    # Calculate totals using bot-specific config
    total_amount = get_order_total(order_data.get("order", {}))
    tax_rate = bot.tax_rate if bot else 0.08
    tax_amount = total_amount * tax_rate
    delivery_fee = get_delivery_fee(total_amount, order_data.get("delivery_type")) # Simplified
    grand_total = total_amount + tax_amount + delivery_fee

    order = Order(
        owner_id=owner_id,
        customer_number=customer_number,
        items_json=json.dumps(order_data.get("order", {})),
        total_amount=total_amount,
        tax_amount=tax_amount,
        delivery_amount=delivery_fee,
        grand_total=grand_total,
        delivery_type=order_data.get("delivery_type", "delivery")
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order

# ========== Helpers (no circular imports) ==========

# ========== Populate Dummy Data ==========
def populate_dummy_data(db):
    """Populates sample data for the first admin user found"""
    admin = db.query(User).filter(User.role == "admin").first()
    if not admin:
        return
    # Contacts
    if db.query(Contact).filter(Contact.owner_id == admin.id).count() == 0:
        sample_contacts = [
            {"first_name": "Ahmed", "last_name": "Ali", "company": "TechCorp", "phone": "+92300123456", "status": "Hot Lead", "source": "Web"},
            {"first_name": "Sara", "last_name": "Khan", "company": "DesignStudio", "phone": "+92300123457", "status": "Warm", "source": "Referral"},
            {"first_name": "John", "last_name": "Smith", "company": "Smith Ltd", "phone": "+92300123458", "status": "New", "source": "Chat"},
            {"first_name": "Maria", "last_name": "Garcia", "company": "Garcia Corp", "phone": "+92300123459", "status": "Hot Lead", "source": "Call"},
            {"first_name": "Usman", "last_name": "Tariq", "company": "Tariq Sons", "phone": "+92300123460", "status": "Cold", "source": "Email"}
        ]
        for c in sample_contacts:
            contact = Contact(owner_id=admin.id, **c)
            db.add(contact)
    # Deals
    if db.query(Deal).filter(Deal.owner_id == admin.id).count() == 0:
        sample_deals = [
            {"title": "Ahmed Corp deal", "company": "Ahmed Corp", "contact_name": "Ahmed Ali", "value": 5000, "stage": "Proposal", "probability": 60},
            {"title": "Khan Enterprises", "company": "Khan Enterprises", "contact_name": "Sara Khan", "value": 12000, "stage": "Negotiation", "probability": 80},
            {"title": "Smith Co", "company": "Smith Co", "contact_name": "John Smith", "value": 3500, "stage": "Discovery", "probability": 20}
        ]
        for d in sample_deals:
            deal = Deal(owner_id=admin.id, **d)
            db.add(deal)
    # Calls
    if db.query(Call).filter(Call.owner_id == admin.id).count() == 0:
        sample_calls = [
            {"contact_name": "Ahmed Ali", "phone": "+92300123456", "direction": "Inbound", "duration_minutes": 4.5, "outcome": "Resolved", "agent": "SalesBot"},
            {"contact_name": "Sara Khan", "phone": "+92300123457", "direction": "Outbound", "duration_minutes": 2.0, "outcome": "Follow-up", "agent": "SupportBot"},
            {"contact_name": "Unknown", "phone": "+92300123458", "direction": "Missed", "duration_minutes": 0.0, "outcome": "Follow-up", "agent": "System"}
        ]
        for c in sample_calls:
            call = Call(owner_id=admin.id, **c)
            db.add(call)
    # Vapi Agents
    if db.query(VapiAgent).filter(VapiAgent.owner_id == admin.id).count() == 0:
        sample_agents = [
            {"name": "Sales Agent", "status": "Live", "total_calls": 24, "conversion_rate": 35.0, "first_message": "Hello!", "system_prompt": "You are a sales agent."},
            {"name": "Support Agent", "status": "Draft", "total_calls": 8, "conversion_rate": 20.0, "first_message": "Hi there!", "system_prompt": "You are a support agent."}
        ]
        for a in sample_agents:
            agent = VapiAgent(owner_id=admin.id, **a)
            db.add(agent)
    # WhatsApp Bot
    if db.query(WhatsappBot).filter(WhatsappBot.owner_id == admin.id).count() == 0:
        wbot = WhatsappBot(owner_id=admin.id, name="Restaurant Bot", bot_type="order", business_name="Wild Restaurant", language="en", webhook_url=os.getenv("WEBHOOK_URL", ""))
        db.add(wbot)
    db.commit()

# ========== Legacy in-memory vars (for restaurant bot) ==========
saved_orders = {}
customer_sessions = {}
last_message_time = {}
customer_order_lookup = {}
manager_pending = {}
customer_profiles = {}  # phone -> {name, lang, address, order_history, ...}

def save_profile(sender, session, owner_id=1):
    """Persist customer profile to DB and update in-memory cache."""
    if not session.get("name"):
        return
    # Update in-memory cache immediately
    profile = customer_profiles.get(sender, {"order_history": []})
    profile.update({
        "name": session.get("name", ""),
        "address": session.get("address", ""),
        "lang": session.get("lang", "en"),
        "delivery_type": session.get("delivery_type", ""),
        "payment": session.get("payment", ""),
    })
    if "order_history" not in profile:
        profile["order_history"] = []
    customer_profiles[sender] = profile
    # Persist to DB
    try:
        db = SessionLocal()
        row = db.query(CustomerProfile).filter(CustomerProfile.phone == sender).first()
        if not row:
            row = CustomerProfile(phone=sender, owner_id=owner_id)
            db.add(row)
        row.name = profile["name"]
        row.lang = profile["lang"]
        row.delivery_type = profile["delivery_type"]
        row.address = profile["address"]
        row.payment = profile["payment"]
        db.commit()
        db.close()
    except Exception as e:
        print(f"save_profile DB error: {e}")

def add_to_order_history(sender, order_id, order_items, owner_id=1):
    """Append order to customer history in memory and DB."""
    import time as _time
    profile = customer_profiles.get(sender, {"order_history": []})
    if "order_history" not in profile:
        profile["order_history"] = []
    items_list = [
        {"item_id": k, "name": v["item"]["name"], "qty": v["qty"]}
        for k, v in order_items.items()
    ]
    profile["order_history"].append({
        "order_id": order_id,
        "items": items_list,
        "timestamp": _time.time()
    })
    profile["order_history"] = profile["order_history"][-10:]
    customer_profiles[sender] = profile
    # Persist to DB
    try:
        db = SessionLocal()
        row = db.query(CustomerProfile).filter(CustomerProfile.phone == sender).first()
        if not row:
            row = CustomerProfile(phone=sender, owner_id=owner_id)
            db.add(row)
        row.order_history = profile["order_history"]
        db.commit()
        db.close()
    except Exception as e:
        print(f"add_to_order_history DB error: {e}")

def get_favorite_items(sender):
    """Return top 3 most ordered items for returning customer."""
    profile = customer_profiles.get(sender, {})
    history = profile.get("order_history", [])
    if not history:
        # Try loading from DB
        try:
            db = SessionLocal()
            row = db.query(CustomerProfile).filter(CustomerProfile.phone == sender).first()
            db.close()
            if row:
                customer_profiles[sender] = {
                    "name": row.name, "lang": row.lang,
                    "address": row.address, "delivery_type": row.delivery_type,
                    "payment": row.payment, "order_history": row.order_history
                }
                history = row.order_history
        except:
            return []
    item_counts = {}
    for order in history:
        for item in order.get("items", []):
            name = item.get("name") if isinstance(item, dict) else item
            if name:
                item_counts[name] = item_counts.get(name, 0) + 1
    return [name for name, _ in sorted(item_counts.items(), key=lambda x: x[1], reverse=True)[:3]]

async def save_to_sheet(customer_number, session, order_id):
    """Save order to Google Sheets (optional). Configure GOOGLE_SHEET_WEBHOOK in .env."""
    import os, aiohttp
    webhook = os.getenv("GOOGLE_SHEET_WEBHOOK", "")
    if not webhook:
        return
    try:
        payload = {
            "order_id": order_id,
            "phone": customer_number,
            "name": session.get("name", ""),
            "items": [{"name": v["item"]["name"], "qty": v["qty"]} for v in session.get("order", {}).values()],
            "delivery_type": session.get("delivery_type", ""),
            "address": session.get("address", ""),
            "payment": session.get("payment", "")
        }
        async with aiohttp.ClientSession() as s:
            await s.post(webhook, json=payload)
    except Exception as e:
        print(f"save_to_sheet error: {e}")

def load_customer_profiles_from_db():
    """Pre-load all customer profiles into memory cache on startup."""
    try:
        db = SessionLocal()
        profiles = db.query(CustomerProfile).all()
        for p in profiles:
            customer_profiles[p.phone] = {
                "name": p.name, "lang": p.lang,
                "delivery_type": p.delivery_type, "address": p.address,
                "payment": p.payment, "order_history": p.order_history
            }
        db.close()
        print(f"Loaded {len(profiles)} customer profiles from DB")
    except Exception as e:
        print(f"load_customer_profiles_from_db error: {e}")
