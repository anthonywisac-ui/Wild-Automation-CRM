# setup_bot.py - Full Platform & Bot Initialization
import os
import json
from db import SessionLocal, User, WhatsappBot, hash_password

def setup_platform():
    db = SessionLocal()
    print("Starting Full Bot Platform Setup...")

    try:
        # 1. Create Admin if missing
        admin = db.query(User).filter(User.username == "admin").first()
        if not admin:
            admin = User(
                username="admin",
                hashed_password=hash_password(os.getenv("ADMIN_PASSWORD", "admin123")),
                role="admin"
            )
            db.add(admin)
            db.commit()
            print("Admin user 'admin' created.")

        # 2. Create/Update Restaurant Bot
        bot = db.query(WhatsappBot).filter(WhatsappBot.name == "Wild Restaurant").first()
        if not bot:
            bot = WhatsappBot(
                owner_id=admin.id,
                name="Wild Restaurant",
                bot_type="restaurant",
                business_name="Wild Automation Kitchen",
                language="en",
                tax_rate=0.08,
                delivery_fee=5.0
            )
            db.add(bot)
            db.commit()
            db.refresh(bot)
            print(f"Bot '{bot.name}' created.")

        # 3. Comprehensive Menu & Logic Config (Aligned with flow.py)
        config = {
            "categories": [
                {
                    "id": "cat_deals",
                    "name": "HOT DEALS",
                    "type": "deal",
                    "prefix": "DL",
                    "display": "featured",
                    "items": [
                        {"id": "DL1", "name": "Burger Combo Deal", "price": 14.99, "desc": "1 Burger + Fries + Drink", "emoji": "🍱"},
                        {"id": "DL2", "name": "Duo Burger Pack", "price": 22.00, "desc": "2 Classic Burgers + 2 Sodas", "emoji": "👫"},
                        {"id": "DL3", "name": "Pizza & Wings Deal", "price": 28.00, "desc": "1 Large Pizza + 6 Wings + 2 Sodas", "emoji": "🍕"}
                    ]
                },
                {
                    "id": "cat_burgers",
                    "name": "PREMIUM BURGERS",
                    "type": "normal",
                    "prefix": "FF",
                    "display": "list",
                    "items": [
                        {"id": "FF1", "name": "Classic Smash", "price": 12.50, "desc": "Wagyu beef, secret sauce, cheese", "emoji": "🍔"},
                        {"id": "FF2", "name": "Spicy Zinger", "price": 11.99, "desc": "Fried chicken, jalapeños, spicy mayo", "emoji": "🔥"}
                    ]
                },
                {
                    "id": "cat_pizza",
                    "name": "ARTISAN PIZZAS",
                    "type": "normal",
                    "prefix": "PZ",
                    "display": "grid",
                    "items": [
                        {"id": "PZ1", "name": "Margherita", "price": 14.00, "desc": "Fresh burrata, basil, tomato", "emoji": "🌿"},
                        {"id": "PZ2", "name": "Meat Feast", "price": 16.50, "desc": "Pepperoni, ham, bacon, beef", "emoji": "🍖"}
                    ]
                },
                {
                    "id": "cat_bbq",
                    "name": "BBQ PIT",
                    "type": "normal",
                    "prefix": "BB",
                    "display": "list",
                    "items": [
                        {"id": "BB1", "name": "Baby Back Ribs", "price": 19.50, "desc": "Full rack with honey BBQ glaze", "emoji": "🍖", "requires_sides": True},
                        {"id": "BB2", "name": "Smoked Brisket", "price": 21.00, "desc": "12-hour slow smoked brisket", "emoji": "🥩", "requires_sides": True}
                    ]
                },
                {
                    "id": "cat_desserts",
                    "name": "DESSERTS",
                    "type": "upsell",
                    "prefix": "DS",
                    "display": "grid",
                    "items": [
                        {"id": "DS1", "name": "Lava Cake", "price": 7.50, "desc": "Warm chocolate with ice cream", "emoji": "🍫"},
                        {"id": "DS2", "name": "NY Cheesecake", "price": 6.50, "desc": "Classic creamy with berries", "emoji": "🍰"}
                    ]
                },
                {
                    "id": "cat_drinks",
                    "name": "DRINKS",
                    "type": "drinks",
                    "prefix": "DR",
                    "display": "list",
                    "items": [
                        {"id": "DR1", "name": "Coca Cola", "price": 2.50, "desc": "Cold 330ml can", "emoji": "🥤"},
                        {"id": "DR2", "name": "Mineral Water", "price": 1.50, "desc": "Refreshing spring water", "emoji": "💧"}
                    ]
                }
            ]
        }

        bot.config_json = json.dumps(config)
        
        # Ensure user has this bot assigned
        user_bots = admin.bots
        if bot.name not in user_bots:
            user_bots.append(bot.name)
            admin.bots = user_bots

        db.commit()
        print(f"Full Configuration Pushed to Bot: {bot.name}")
        print("Your bot is now fully loaded and ready for orders!")

    except Exception as e:
        db.rollback()
        print(f"Error during setup: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    setup_platform()
