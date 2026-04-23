import stripe
import time
from config import STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET, MANAGER_NUMBER
from db import saved_orders, save_profile, add_to_order_history, save_to_sheet
from whatsapp_handlers import send_text_message, send_order_confirmed, send_manager_action_list
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
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except Exception as e:
        print(f"Webhook error: {e}")
        return {"status": "error"}
    if event["type"] == "checkout.session.completed":
        checkout_session = event["data"]["object"]
        order_id = checkout_session.get("metadata", {}).get("order_id")
        if not order_id or order_id not in saved_orders:
            return {"status": "ignored"}
        order_data = saved_orders[order_id]
        session_data = order_data.get("session")
        sender = order_data["sender"]
        lang = session_data.get("lang", "en")
        await send_order_confirmed(sender, session_data, lang)
        save_profile(sender, session_data)
        add_to_order_history(sender, order_id, session_data["order"])
        await send_manager_action_list(order_id, sender, f"🔔 PAID Order #{order_id}", get_order_text(session_data["order"]))
        await save_to_sheet(sender, session_data, order_id)
        saved_orders[order_id]["payment_status"] = "paid"
    return {"status": "ok"}
