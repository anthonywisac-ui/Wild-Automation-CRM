from db import SessionLocal, WhatsappBot, User
import json

def populate_restaurant_config():
    db = SessionLocal()
    try:
        # Find the restaurant bot
        bot = db.query(WhatsappBot).filter(WhatsappBot.bot_type == "restaurant").first()
        if not bot:
            print("No restaurant bot found to configure.")
            return

        config = {
            "categories": [
                {
                    "id": "cat_deals",
                    "name": "🔥 Hot Deals",
                    "type": "deal",
                    "prefix": "DL",
                    "display": "featured",
                    "items": [
                        {"name": "Burger Combo", "price": 15.99, "desc": "Double Smash + Fries + Drink", "emoji": "🔥", "addons": "Extra Patty:+3.0"},
                        {"name": "Family Pizza Deal", "price": 29.99, "desc": "2 Large Pizzas + 2L Drink", "emoji": "🍕", "addons": "Extra Toppings:+2.0"}
                    ]
                },
                {
                    "id": "cat_burgers",
                    "name": "🍔 Gourmet Burgers",
                    "type": "normal",
                    "prefix": "FF",
                    "display": "list",
                    "items": [
                        {"name": "Classic Smash", "price": 12.99, "desc": "Hand-pressed beef, lettuce, tomato, secret sauce", "emoji": "🍔", "addons": "Bacon:+1.5, Cheese:+1.0"},
                        {"name": "BBQ Bacon Burger", "price": 14.50, "desc": "Crispy bacon, smoky BBQ sauce, onion rings", "emoji": "🥓", "addons": "Caramelized Onions:+1.0"}
                    ]
                },
                {
                    "id": "cat_pizza",
                    "name": "🍕 Signature Pizzas",
                    "type": "normal",
                    "prefix": "PIZ",
                    "display": "grid",
                    "items": [
                        {"name": "Margherita", "price": 11.00, "desc": "Fresh mozzarella, basil, olive oil", "emoji": "🍃"},
                        {"name": "Pepperoni Feast", "price": 13.50, "desc": "Double pepperoni, extra cheese", "emoji": "🌶️"}
                    ]
                },
                {
                    "id": "cat_bbq",
                    "name": "🍖 BBQ Pit",
                    "type": "normal",
                    "prefix": "RB",
                    "display": "list",
                    "items": [
                        {"name": "Baby Back Ribs", "price": 18.50, "desc": "Smoked for 6 hours", "emoji": "🍖"}
                    ]
                },
                {
                    "id": "cat_desserts",
                    "name": "🍰 Sweet Endings",
                    "type": "upsell",
                    "prefix": "DS",
                    "display": "grid",
                    "items": [
                        {"name": "Chocolate Lava Cake", "price": 6.50, "desc": "Warm gooey center, vanilla ice cream", "emoji": "🍫"},
                        {"name": "NY Cheesecake", "price": 5.99, "desc": "Classic creamy cheesecake with berry sauce", "emoji": "🍓"}
                    ]
                },
                {
                    "id": "cat_drinks",
                    "name": "🥤 Cold Drinks",
                    "type": "drinks",
                    "prefix": "DRK",
                    "display": "list",
                    "items": [
                        {"name": "Coca Cola", "price": 2.50, "desc": "330ml Can", "emoji": "🥤"},
                        {"name": "Fresh Orange Juice", "price": 4.00, "desc": "Freshly squeezed", "emoji": "🍊"}
                    ]
                }
            ],
            "rules": {
                "tax": 5,
                "delivery": 3,
                "min_order": 20
            }
        }

        bot.config_json = json.dumps(config)
        db.commit()
        print(f"Successfully populated bot '{bot.name}' with premium restaurant menu.")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    populate_restaurant_config()
