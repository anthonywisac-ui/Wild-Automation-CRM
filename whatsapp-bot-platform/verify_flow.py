import asyncio
import json
import time
from unittest.mock import MagicMock, AsyncMock, patch

# Mock all external dependencies before importing flow
import sys
from types import ModuleType

# Mock ai_utils
ai_utils_mock = ModuleType("ai_utils")
ai_utils_mock.get_ai_response = AsyncMock(return_value="I'm here to help with your order!")
sys.modules["bots.restaurant.ai_utils"] = ai_utils_mock

# Mock stripe_utils
stripe_mock = ModuleType("stripe_utils")
stripe_mock.create_stripe_checkout_session = AsyncMock(return_value="https://stripe.com/pay")
sys.modules["bots.restaurant.stripe_utils"] = stripe_mock

# Now import the flow after mocking
from bots.restaurant.flow import handle_flow, customer_sessions, saved_orders, get_bot_menu, new_session
from db import SessionLocal, WhatsappBot, User, Base, engine

async def run_verification():
    print("Starting Bot Flow Verification...")
    
    # 1. Setup Database State
    db = SessionLocal()
    bot = db.query(WhatsappBot).filter(WhatsappBot.bot_type == "restaurant").first()
    if not bot:
        print("FAIL: No restaurant bot found.")
        return
    
    # Pre-load menu into bot for testing
    MENU = get_bot_menu(bot.phone_number_id)
    
    async def mock_send(sender, message, bot=None):
        pass
    
    # Patch the handlers
    with patch("bots.restaurant.flow.send_text_message", new=AsyncMock()) as mock_text, \
         patch("bots.restaurant.flow.send_language_selection", new=AsyncMock()) as mock_lang, \
         patch("bots.restaurant.flow.send_main_menu", new=AsyncMock()) as mock_menu, \
         patch("bots.restaurant.flow.send_category_items", new=AsyncMock()) as mock_cat, \
         patch("bots.restaurant.flow.send_qty_control", new=AsyncMock()) as mock_qty, \
         patch("bots.restaurant.flow.send_order_confirmed", new=AsyncMock(return_value=12345)), \
         patch("bots.restaurant.flow.notify_manager", new=AsyncMock()), \
         patch("bots.restaurant.flow.send_quick_combo_upsell", new=AsyncMock()) as mock_upsell, \
         patch("bots.restaurant.flow.send_quick_upsell", new=AsyncMock()) as mock_quick_upsell, \
         patch("bots.restaurant.flow.notify_manager_status", new=AsyncMock()) as mock_escalate:
        
        sender = "test_user_123"
        customer_sessions.clear()
        
        # --- TEST 1: Basic Start ---
        print("\n[Test 1] Initial Message")
        await handle_flow(sender, "hi", bot=bot)
        print(f"PASS: Stage is {customer_sessions[sender]['stage']}")

        # --- TEST 2: Select Language ---
        print("\n[Test 2] Select Language")
        await handle_flow(sender, "LANG_EN", is_button=True, bot=bot)
        print(f"PASS: Lang set to {customer_sessions[sender]['lang']}")

        # --- TEST 3: Add Burger (Upsell Check) ---
        print("\n[Test 3] Add Burger & Check Upsell")
        await handle_flow(sender, "ADD_FF1", is_button=True, bot=bot)
        if mock_upsell.called:
            print("PASS: Upsell 'Make it a Combo' triggered.")
        else:
            print("FAIL: Upsell NOT triggered.")

        # --- TEST 4: DL1 Redirect Logic ---
        print("\n[Test 4] Add DL1 Without Burger")
        sender2 = "test_user_456"
        customer_sessions[sender2] = new_session(sender2)
        customer_sessions[sender2].update({"stage": "menu", "order": {}, "lang": "en", "menu": MENU})
        await handle_flow(sender2, "ADD_DL1", is_button=True, bot=bot)
        called_texts = [str(args[0][1]) for args in mock_text.call_args_list]
        if any("pick a burger" in t for t in called_texts):
            print("PASS: DL1 correctly redirected.")
        else:
            print(f"FAIL: DL1 did not redirect.")

        # --- TEST 5: BBQ Sides Logic ---
        print("\n[Test 5] BBQ Sides Trigger")
        await handle_flow(sender, "ADD_RB1", is_button=True, bot=bot)
        if customer_sessions[sender]["stage"] == "bbq_sides":
            print("PASS: RB1 triggered BBQ sides selection.")
        else:
            print(f"FAIL: BBQ sides stage not reached. Stage: {customer_sessions[sender]['stage']}")

        # --- TEST 6: Order Status Escalation ---
        print("\n[Test 6] Order Status Escalation")
        # Reset session to outside ordering stages
        customer_sessions[sender] = new_session(sender)
        customer_sessions[sender]["stage"] = "menu"
        order_id = 77777
        saved_orders[order_id] = {
            "timestamp": time.time() - (60 * 60), # 1 hour ago
            "delivery_type": "delivery",
            "sender": sender
        }
        await handle_flow(sender, "where is my order #77777", bot=bot)
        if mock_escalate.called:
            print("PASS: Escalation triggered.")
        else:
            print("FAIL: Escalation not triggered.")

        # --- TEST 7: Reservation Flow ---
        print("\n[Test 7] Reservation Flow")
        customer_sessions[sender] = new_session(sender)
        customer_sessions[sender]["stage"] = "menu"
        await handle_flow(sender, "book a table", bot=bot)
        if customer_sessions[sender]["stage"] == "reserve_date":
            print("PASS: Reservation flow started.")
        else:
            print(f"FAIL: Reservation stage not reached. Stage: {customer_sessions[sender]['stage']}")

    print("\nVerification Script Completed.")

if __name__ == "__main__":
    asyncio.run(run_verification())
