import os
from dotenv import load_dotenv

load_dotenv()

# WhatsApp
VERIFY_TOKEN = os.getenv("WHATSAPP_WEBHOOK_VERIFICATION_TOKEN")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
WHATSAPP_API_VERSION = os.getenv("WHATSAPP_API_VERSION", "v18.0")

# AI
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Stripe
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")

# Manager
MANAGER_NUMBER = os.getenv("MANAGER_NUMBER", "")

# Restaurant / Order constants
MIN_DELIVERY_ORDER = 30.00
MIN_PICKUP_ORDER = 10.00
DELIVERY_CHARGE = 4.99
FREE_DELIVERY_THRESHOLD = 50.00
POST_ORDER_WINDOW = 180

# Language names (optional)
LANG_NAMES = {
    "en": "English", "ar": "Arabic", "hi": "Hindi",
    "fr": "French", "de": "German", "ru": "Russian",
    "zh": "Chinese", "ml": "Malayalam",
}

# Menu summary (optional, for AI)
MENU_SUMMARY = """
Wild Bites Restaurant Menu (US):
Deals, Burgers, Pizza, BBQ, Fish, Drinks, Sides, Desserts
Delivery: min $30, fee $4.99, free over $50 | Pickup: min $10
Hours: 10am-11pm daily
"""

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
MINIMAX_API_KEY = os.getenv("MINIMAX_API_KEY")

if not GROQ_API_KEY:
    print("❌ GROQ_API_KEY missing")

if not GEMINI_API_KEY:
    print("❌ GEMINI_API_KEY missing")