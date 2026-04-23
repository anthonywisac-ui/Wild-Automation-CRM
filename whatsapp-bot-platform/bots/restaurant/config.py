import os

# WhatsApp
VERIFY_TOKEN = os.getenv("WHATSAPP_WEBHOOK_VERIFICATION_TOKEN")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID")

# AI
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Google Sheet (optional)
GOOGLE_SHEET_WEBHOOK = os.getenv("GOOGLE_SHEET_WEBHOOK", "")

# Manager
MANAGER_NUMBER = os.getenv("MANAGER_NUMBER", "")

# Stripe
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")

# Order constants
MIN_DELIVERY_ORDER = 30.00
MIN_PICKUP_ORDER = 10.00
DELIVERY_CHARGE = 4.99
FREE_DELIVERY_THRESHOLD = 50.00
POST_ORDER_WINDOW = 180

# Language names
LANG_NAMES = {
    "en": "English", "ar": "Arabic", "hi": "Hindi",
    "fr": "French", "de": "German", "ru": "Russian",
    "zh": "Chinese", "ml": "Malayalam",
}

# Menu summary (for AI)
MENU_SUMMARY = """
Wild Bites Restaurant Menu (US):
Deals, Burgers, Pizza, BBQ, Fish, Drinks, Sides, Desserts
Delivery: min $30, fee $4.99, free over $50 | Pickup: min $10
Hours: 10am-11pm daily
"""