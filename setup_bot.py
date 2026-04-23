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
        else:
            print("Admin user already exists.")

        # 2. Create Restaurant Bot if missing
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
        else:
            print(f"Bot '{bot.name}' already exists.")

        # 3. Comprehensive Menu & Logic Config
        config = {
            "categories": [
                {
                    "id": "cat_deals",
                    "name": "HOT DEALS",
                    "type": "deal",
                    "prefix": "DL",
                    "display": "featured",
                    "items": [
                        {"id": "DL1", "name": "Solo Feast", "price": 12.00, "desc": "1 Burger + 1 Fries + 1 Coke", "emoji": "🍱"},
                        {"id": "DL2", "name": "Duo Pack", "price": 22.00, "desc": "2 Classic Burgers + 2 Drinks", "emoji": "👫"},
                        {"id": "DL3", "name": "Family Pizza Night", "price": 35.00, "desc": "2 Large Pizzas + 4 Drinks + Garlic Bread", "emoji": "👨‍👩‍👧‍👦"}
                    ]
                },
                {
                    "id": "cat_burgers",
                    "name": "PREMIUM BURGERS",
                    "type": "normal",
                    "prefix": "FF",
                    "display": "list",
                    "items": [
                        {"id": "FF1", "name": "Truffle Smash", "price": 14.50, "desc": "Wagyu beef, truffle aioli, swiss cheese", "emoji": "💎"},
                        {"id": "FF2", "name": "Spicy Zinger", "price": 11.99, "desc": "Fried chicken, jalapeños, spicy mayo", "emoji": "🔥"},
                        {"id": "FF3", "name": "The Beast", "price": 18.00, "desc": "Triple patty, bacon, fried egg", "emoji": "🦖"}
                    ]
                },
                {
                    "id": "cat_pizza",
                    "name": "ARTISAN PIZZAS",
                    "type": "normal",
                    "prefix": "PZ",
                    "display": "grid",
                    "items": [
                        {"id": "PZ1", "name": "Burrata Dream", "price": 16.00, "desc": "Fresh burrata, basil, balsamic glaze", "emoji": "🌿"},
                        {"id": "PZ2", "name": "Meat Overload", "price": 15.50, "desc": "Pepperoni, sausage, ham, bacon", "emoji": "🍖"}
                    ]
                },
                {
                    "id": "cat_bbq",
                    "name": "BBQ PIT",
                    "type": "normal",
                    "prefix": "BB",
                    "display": "list",
                    "items": [
                        {"id": "BB1", "name": "Half Rack Ribs", "price": 19.50, "desc": "Fall-off-the-bone ribs with honey glaze", "emoji": "🔥", "requires_sides": True},
                        {"id": "BB2", "name": "Brisket Plate", "price": 21.00, "desc": "12-hour smoked brisket slices", "emoji": "🥩", "requires_sides": True}
                    ]
                },
                {
                    "id": "cat_desserts",
                    "name": "SWEET UPSELLS",
                    "type": "upsell",
                    "prefix": "DS",
                    "display": "grid",
                    "items": [
                        {"id": "DS1", "name": "Nutella Crepe", "price": 7.50, "desc": "Served with strawberries and cream", "emoji": "🥞"},
                        {"id": "DS2", "name": "Warm Brownie", "price": 6.00, "desc": "With a scoop of vanilla bean ice cream", "emoji": "🍨"}
                    ]
                },
                {
                    "id": "cat_drinks",
                    "name": "DRINKS",
                    "type": "drinks",
                    "prefix": "DR",
                    "display": "list",
                    "items": [
                        {"id": "DR1", "name": "Iced Berry Tea", "price": 4.50, "desc": "House-made with fresh berries", "emoji": "🍓"},
                        {"id": "DR2", "name": "Vanilla Milkshake", "price": 5.50, "desc": "Extra thick and creamy", "emoji": "🍦"}
                    ]
                }
            ],
            "upsell_logic": {
                "threshold": 30.0,
                "suggest_category": "cat_desserts",
                "message": "You're already treating yourself! Why not add a little sweetness to your meal?"
            },
            "deals_logic": {
                "free_delivery_over": 50.0,
                "discount_over_100": 10.0,
                "combos": {
                    "DL1": ["FF1", "DR1", "Sides: Fries"],
                    "DL2": ["FF1", "FF1", "DR1", "DR1"]
                }
            }
        }

        bot.config_json = json.dumps(config)
        
        # Ensure user has this bot assigned
        user_bots = admin.bots
        if bot.name not in user_bots:
            user_bots.append(bot.name)
            admin.bots = user_bots

        db.commit()
        print(f"Full Configuration Pushed to Bot: {bot.name}")
        print("\n--- Summary ---")
        print(f"Categories: {len(config['categories'])}")
        print(f"Total Items: {sum(len(c['items']) for c in config['categories'])}")
        print(f"Upsell Threshold: ${config['upsell_logic']['threshold']}")
        print("----------------\n")
        print("Your bot is now fully loaded and ready for orders!")

    except Exception as e:
        db.rollback()
        print(f"Error during setup: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    setup_platform()
