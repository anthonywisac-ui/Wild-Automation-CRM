import stripe
import time
from bots.restaurant.config import STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET, MANAGER_NUMBER
from db import SessionLocal, Order, WhatsappBot, User
from bots.restaurant.db import save_profile, add_to_order_history
from bots.restaurant.whatsapp_handlers import send_text_message, send_order_confirmed, send_manager_action_list
from utils import get_order_total, get_delivery_fee, get_order_text

stripe.api_key = STRIPE_SECRET_KEY

async def create_stripe_checkout_session(order_id: str, amount: float, success_url=None, cancel_url=None):
    if not success_url:
        success_url = "https://your-domain.railway.app/success"
    if not cancel_url:
        cancel_url = "https://your-domain.railway.app/cancel"
    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": "usd",
                    "product_data": {"name": f"Order {order_id}"},
                    "unit_amount": int(amount * 100),
                },
                "quantity": 1,
            }],
            mode="payment",
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={"order_id": order_id},
            client_reference_id=order_id
        )
        return session.url
    except Exception as e:
        print(f"Stripe error: {e}")
        return None

async def handle_stripe_webhook(payload, sig_header):
    # Webhook handling moved to main.py
    return {"status": "moved"}
