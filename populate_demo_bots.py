"""
populate_demo_bots.py
Run: python populate_demo_bots.py
Creates 7 demo restaurant bots in DB. Credentials empty — set via Bot Builder.
Manager number pre-set. Same phone_number_id = multi-bot selector activates.
"""
import json
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from db import SessionLocal, WhatsappBot, User, hash_password, migrate_db

MANAGER_NUMBER = "+923351021321"

# ── Helper ────────────────────────────────────────────────────────────────────

def cat(cat_id, name, prefix, items):
    return {"id": f"cat_{cat_id}", "name": name, "prefix": prefix, "type": "normal", "items": items}

def item(iid, name, price, emoji, desc=""):
    return {"id": iid, "name": name, "price": price, "emoji": emoji, "desc": desc}

def make_config(categories, tax=8.0, delivery=3.0, min_order=15.0,
                upsell=None, deal_rules=None):
    return json.dumps({
        "categories": categories,
        "rules": {"tax": tax, "delivery": delivery, "min_order": min_order},
        "upsell_rules": upsell or {"burger_combo": False, "pizza_wings": False, "desserts": False},
        "deal_rules": deal_rules or {}
    })

# ── Bot Definitions ────────────────────────────────────────────────────────────

BOTS = []

# ── 1. US Fast Food ───────────────────────────────────────────────────────────
BOTS.append({
    "name": "us-fast-food",
    "business_name": "Quick Bites USA",
    "bot_type": "restaurant",
    "tax_rate": 0.08,
    "delivery_fee": 3.0,
    "system_prompt": "You are a friendly fast food ordering assistant for Quick Bites USA. Help customers order burgers, chicken, and combos quickly.",
    "config": make_config(
        categories=[
            cat("fastfood", "🍔 Burgers", "BG", [
                item("BG1", "Classic Cheeseburger", 5.0, "🍔", "Beef patty, cheese, lettuce, tomato"),
                item("BG2", "Double Cheeseburger", 7.0, "🍔", "Double beef patty, double cheese"),
                item("BG3", "Big Style Burger", 8.0, "🍔", "Triple patty, special sauce"),
                item("BG4", "Veggie Burger", 6.0, "🌱", "Plant-based patty, avocado"),
            ]),
            cat("chicken", "🍗 Chicken", "CK", [
                item("CK1", "Crispy Chicken Sandwich", 6.0, "🍗", "Crispy fillet, pickles, mayo"),
                item("CK2", "Spicy Chicken Deluxe", 7.0, "🌶️", "Spicy fillet, jalapeños, slaw"),
                item("CK3", "Chicken Nuggets 6pc", 5.0, "🍗", "Golden crispy nuggets"),
                item("CK4", "Chicken Nuggets 10pc", 7.0, "🍗", "Golden crispy nuggets"),
            ]),
            cat("sides", "🍟 Sides", "SD", [
                item("SD1", "Fries Small", 2.0, "🍟", "Crispy golden fries"),
                item("SD2", "Fries Large", 3.0, "🍟", "Large crispy fries"),
                item("SD3", "Onion Rings", 4.0, "🧅", "Beer-battered rings"),
                item("SD4", "Mozzarella Sticks", 5.0, "🧀", "Crispy with marinara"),
            ]),
            cat("drinks", "🥤 Drinks", "DR", [
                item("DR1", "Soft Drink", 2.0, "🥤", "Coke, Pepsi, Sprite"),
                item("DR2", "Milkshake", 4.0, "🍦", "Chocolate, Vanilla, Strawberry"),
                item("DR3", "Iced Coffee", 3.0, "☕", "Cold brew with cream"),
                item("DR4", "Bottled Water", 1.5, "💧", "Still water"),
            ]),
            cat("deals", "🔥 Combos & Deals", "DC", [
                item("DC1", "Classic Burger Combo", 8.0, "🔥", "Classic Cheeseburger + Fries + Drink"),
                item("DC2", "Double Burger Combo", 10.0, "🍔", "Any Double Burger + Large Fries + Drink"),
                item("DC3", "Chicken Sandwich Combo", 9.0, "🍗", "Crispy Chicken + Fries + Drink"),
                item("DC4", "Nuggets Combo", 10.0, "🍗", "Nuggets 10pc + Fries + Drink"),
                item("DC5", "Big Style Special", 12.0, "⭐", "Any Big Burger + Nuggets 6pc + Drink"),
                item("DC6", "Double + Mozzarella", 11.0, "🧀", "Any Double Burger + Mozzarella Sticks + Drink"),
                item("DC7", "2 Burgers Deal", 15.0, "👫", "2 Any Burgers + 2 Fries + 2 Drinks"),
                item("DC8", "Family Box", 20.0, "👨‍👩‍👧‍👦", "2 Burgers + Nuggets + 3 Fries"),
            ]),
        ],
        tax=8.0, delivery=3.0, min_order=15.0,
        upsell={"burger_combo": True, "pizza_wings": False, "desserts": False},
        deal_rules={
            "DC2": {"picks": ["fastfood"]},
            "DC5": {"picks": ["fastfood"]},
            "DC6": {"picks": ["fastfood"]},
            "DC7": {"picks": ["fastfood", "fastfood"]},
            "DC8": {"picks": ["fastfood", "fastfood"]},
        }
    )
})

# ── 2. US Chicken Chain ───────────────────────────────────────────────────────
BOTS.append({
    "name": "us-chicken-chain",
    "business_name": "Crispy Cluck USA",
    "bot_type": "restaurant",
    "tax_rate": 0.08,
    "delivery_fee": 3.0,
    "system_prompt": "You are a friendly ordering assistant for Crispy Cluck USA, a chicken restaurant. Help customers choose buckets, sandwiches, and family deals.",
    "config": make_config(
        categories=[
            cat("buckets", "🍗 Chicken Buckets", "BK", [
                item("BK1", "2pc Chicken", 7.0, "🍗", "2 pieces of fried chicken"),
                item("BK2", "5pc Bucket", 15.0, "🍗", "5 pieces crispy chicken"),
                item("BK3", "10pc Bucket", 25.0, "🍗", "10 pieces crispy chicken"),
                item("BK4", "15pc Family Bucket", 35.0, "🍗", "15 pieces — feeds the family"),
            ]),
            cat("sandwiches", "🍔 Sandwiches & Wraps", "SW", [
                item("SW1", "Chicken Burger", 6.0, "🍔", "Classic fried chicken burger"),
                item("SW2", "Zinger Burger", 7.0, "🌶️", "Spicy zinger fillet burger"),
                item("SW3", "BBQ Chicken Sandwich", 7.0, "🍖", "Smoky BBQ sauce chicken"),
                item("SW4", "Chicken Wrap", 6.0, "🌯", "Grilled chicken in soft wrap"),
            ]),
            cat("sides", "🍟 Sides", "SD", [
                item("SD1", "Fries", 3.0, "🍟", "Crispy golden fries"),
                item("SD2", "Coleslaw", 3.0, "🥗", "Creamy coleslaw"),
                item("SD3", "Mashed Potatoes", 4.0, "🥔", "Creamy mashed potatoes + gravy"),
                item("SD4", "Corn on the Cob", 3.0, "🌽", "Buttered corn"),
            ]),
            cat("drinks", "🥤 Drinks", "DR", [
                item("DR1", "Soft Drink", 2.0, "🥤", "Coke, Pepsi, Sprite"),
                item("DR2", "Lemonade", 3.0, "🍋", "Fresh lemonade"),
                item("DR3", "Iced Tea", 2.5, "🍵", "Sweet or unsweetened"),
                item("DR4", "Milkshake", 4.0, "🍦", "Chocolate or Vanilla"),
            ]),
            cat("deals", "🔥 Bucket Deals & Combos", "DC", [
                item("DC1", "2pc Chicken Combo", 9.0, "🍗", "2pc Chicken + Fries + Drink"),
                item("DC2", "Chicken Burger Combo", 9.0, "🍔", "Chicken Burger + Fries + Drink"),
                item("DC3", "Zinger Combo", 10.0, "🌶️", "Zinger Burger + Fries + Drink"),
                item("DC4", "5pc Bucket Meal", 18.0, "🍗", "5pc Chicken + 2 Fries + 2 Drinks"),
                item("DC5", "10pc Bucket Meal", 28.0, "🍗", "10pc Chicken + 3 Fries + 3 Drinks"),
                item("DC6", "Family Feast", 40.0, "👨‍👩‍👧‍👦", "15pc + 4 Fries + 4 Drinks"),
                item("DC7", "Mega Deal", 35.0, "⭐", "10pc + Any Sandwich + Sides + Drinks"),
            ]),
        ],
        tax=8.0, delivery=3.0, min_order=12.0,
        upsell={"burger_combo": False, "pizza_wings": False, "desserts": False},
        deal_rules={
            "DC7": {"picks": ["sandwiches"]},
        }
    )
})

# ── 3. UK Peri Peri ───────────────────────────────────────────────────────────
BOTS.append({
    "name": "uk-peri-peri",
    "business_name": "Peri Peri UK",
    "bot_type": "restaurant",
    "tax_rate": 0.0,
    "delivery_fee": 0.0,
    "system_prompt": "You are a friendly ordering assistant for Peri Peri UK. Help customers choose grilled chicken, wraps, and great value meal deals. Prices are in GBP (£).",
    "config": make_config(
        categories=[
            cat("chicken", "🍗 Grilled Chicken", "CK", [
                item("CK1", "Half Chicken", 10.0, "🍗", "Flame-grilled half chicken"),
                item("CK2", "Full Chicken", 16.0, "🍗", "Flame-grilled full chicken"),
                item("CK3", "Chicken Wings 5pc", 7.0, "🍗", "Spicy peri peri wings"),
                item("CK4", "Chicken Thighs 3pc", 8.0, "🍗", "Juicy marinated thighs"),
            ]),
            cat("fastfood", "🌯 Wraps & Burgers", "WB", [
                item("WB1", "Chicken Wrap", 7.0, "🌯", "Peri peri chicken in soft wrap"),
                item("WB2", "Chicken Burger", 8.0, "🍔", "Flame-grilled chicken burger"),
                item("WB3", "Halloumi Wrap", 7.0, "🧀", "Grilled halloumi, peppers, salad"),
                item("WB4", "Veggie Burger", 7.0, "🌱", "Plant-based patty, peri sauce"),
            ]),
            cat("sides", "🍟 Sides", "SD", [
                item("SD1", "Peri Fries", 3.0, "🍟", "Seasoned peri peri fries"),
                item("SD2", "Spicy Rice", 3.0, "🍚", "Peri peri spiced rice"),
                item("SD3", "Garlic Bread", 3.0, "🥖", "Buttery garlic bread"),
                item("SD4", "Coleslaw", 2.5, "🥗", "Creamy coleslaw"),
            ]),
            cat("drinks", "🥤 Drinks", "DR", [
                item("DR1", "Soft Drink", 2.5, "🥤", "Coke, Pepsi, Sprite"),
                item("DR2", "Fresh Juice", 3.0, "🍊", "Orange, apple or mango"),
                item("DR3", "Iced Tea", 2.5, "🍵", "Peach or lemon"),
                item("DR4", "Milkshake", 4.0, "🍦", "Chocolate or Vanilla"),
            ]),
            cat("deals", "🔥 Meal Deals", "DC", [
                item("DC1", "Half Chicken Meal", 13.0, "🍗", "Half Chicken + 2 Sides + Drink"),
                item("DC2", "Wrap Meal", 10.0, "🌯", "Chicken Wrap + Fries + Drink"),
                item("DC3", "Burger Meal", 11.0, "🍔", "Chicken Burger + Side + Drink"),
                item("DC4", "Full Chicken Feast", 20.0, "⭐", "Full Chicken + 2 Sides + 2 Drinks"),
                item("DC5", "Wings Meal", 15.0, "🍗", "Wings 10pc + 2 Sides + Drink"),
                item("DC6", "Sharing Platter", 25.0, "👫", "Full Chicken + 4 Sides + 2 Drinks"),
                item("DC7", "Family Feast", 35.0, "👨‍👩‍👧‍👦", "2 Chickens + 5 Sides + Drinks"),
            ]),
        ],
        tax=0.0, delivery=0.0, min_order=10.0,
        upsell={"burger_combo": False, "pizza_wings": False, "desserts": False},
        deal_rules={
            "DC1": {"picks": ["sides", "sides"]},
            "DC3": {"picks": ["sides"]},
            "DC4": {"picks": ["sides", "sides"]},
            "DC5": {"picks": ["sides", "sides"]},
            "DC6": {"picks": ["sides", "sides"]},
        }
    )
})

# ── 4. French Menu ────────────────────────────────────────────────────────────
BOTS.append({
    "name": "cafe-paris",
    "business_name": "Café Paris",
    "bot_type": "restaurant",
    "tax_rate": 0.0,
    "delivery_fee": 0.0,
    "system_prompt": "You are an elegant ordering assistant for Café Paris, a French restaurant. Help customers choose from our classic French menu. Prices in EUR (€).",
    "config": make_config(
        categories=[
            cat("mains", "🥩 Main Courses", "MN", [
                item("MN1", "Steak Frites", 18.0, "🥩", "Prime cut steak with fries"),
                item("MN2", "Grilled Chicken", 16.0, "🍗", "Herb-marinated grilled chicken"),
                item("MN3", "Salmon Fillet", 17.0, "🐟", "Pan-seared salmon, lemon butter"),
                item("MN4", "Beef Bourguignon", 19.0, "🍖", "Classic French braised beef"),
            ]),
            cat("starters", "🥗 Starters", "ST", [
                item("ST1", "French Onion Soup", 7.0, "🍲", "Classic gratinée"),
                item("ST2", "Caesar Salad", 8.0, "🥗", "Romaine, croutons, parmesan"),
                item("ST3", "Goat Cheese Salad", 9.0, "🧀", "Warm goat cheese, walnuts"),
                item("ST4", "Escargots", 10.0, "🐌", "Garlic herb butter escargots"),
            ]),
            cat("desserts", "🍰 Desserts", "DS", [
                item("DS1", "Chocolate Mousse", 7.0, "🍫", "Rich dark chocolate mousse"),
                item("DS2", "Crème Brûlée", 8.0, "🍮", "Classic vanilla crème brûlée"),
                item("DS3", "Apple Tart", 7.0, "🍎", "Rustic French apple tart"),
                item("DS4", "Ice Cream", 6.0, "🍨", "3 scoops, choice of flavour"),
            ]),
            cat("drinks", "☕ Drinks", "DR", [
                item("DR1", "Espresso", 2.0, "☕", "Double shot espresso"),
                item("DR2", "Cappuccino", 3.0, "☕", "Creamy cappuccino"),
                item("DR3", "Fresh Juice", 4.0, "🍊", "Orange or apple"),
                item("DR4", "Sparkling Water", 3.0, "💧", "San Pellegrino"),
            ]),
            cat("deals", "🍽️ Set Menus & Combos", "DC", [
                item("DC1", "Steak + Fries + Drink", 20.0, "🥩", "Any Steak + Fries + Drink"),
                item("DC2", "Chicken + Salad + Drink", 18.0, "🍗", "Grilled Chicken + Caesar + Drink"),
                item("DC3", "Classic Set Menu", 25.0, "⭐", "Starter + Any Main + Any Dessert"),
                item("DC4", "Steak Unlimited Fries", 22.0, "🥩", "Steak + Unlimited Fries + Sauce"),
                item("DC5", "Steak + Dessert + Coffee", 24.0, "🍰", "Any Steak + Any Dessert + Coffee"),
                item("DC6", "Salmon Set", 21.0, "🐟", "Salmon + Salad + Drink"),
            ]),
        ],
        tax=0.0, delivery=0.0, min_order=20.0,
        upsell={"burger_combo": False, "pizza_wings": False, "desserts": True},
        deal_rules={
            "DC1": {"picks": ["mains"]},
            "DC3": {"picks": ["starters", "mains", "desserts"]},
            "DC5": {"picks": ["mains", "desserts"]},
        }
    )
})

# ── 5. Dubai / DXB ────────────────────────────────────────────────────────────
BOTS.append({
    "name": "dubai-cafe",
    "business_name": "Dubai Cafe",
    "bot_type": "restaurant",
    "tax_rate": 0.05,
    "delivery_fee": 0.0,
    "system_prompt": "You are a friendly ordering assistant for Operation Shawarma in Dubai. Help customers order wraps, platters, and street combos. Prices in AED.",
    "config": make_config(
        categories=[
            cat("wraps", "🥙 Wraps & Shawarma", "WR", [
                item("WR1", "Falafel Wrap", 12.0, "🧆", "Crispy falafel, tahini, salad"),
                item("WR2", "Chicken Shawarma", 15.0, "🥙", "Marinated chicken, garlic sauce"),
                item("WR3", "Beef Shawarma", 18.0, "🥙", "Spiced beef, tahini, pickles"),
                item("WR4", "Zaatar Wrap", 10.0, "🌿", "Zaatar, olive oil, cheese"),
            ]),
            cat("platters", "🍽️ Platters", "PL", [
                item("PL1", "Mixed Grill Platter", 35.0, "🍖", "Chicken + Beef + Kofta + sides"),
                item("PL2", "Chicken Shawarma Plate", 25.0, "🍗", "Chicken shawarma + rice + salad"),
                item("PL3", "Falafel Plate", 20.0, "🧆", "Falafel + hummus + salad + bread"),
                item("PL4", "BBQ Chicken Plate", 30.0, "🔥", "BBQ chicken + rice + sides"),
            ]),
            cat("sides", "🧆 Sides", "SD", [
                item("SD1", "Hummus", 10.0, "🧆", "Creamy homemade hummus"),
                item("SD2", "Fries", 8.0, "🍟", "Crispy fries"),
                item("SD3", "Stuffed Vine Leaves", 12.0, "🌿", "Rice-stuffed vine leaves"),
                item("SD4", "Arabic Salad", 10.0, "🥗", "Tomato, cucumber, herbs"),
            ]),
            cat("drinks", "🥤 Drinks", "DR", [
                item("DR1", "Fresh Juice", 8.0, "🍊", "Orange, mango or mixed"),
                item("DR2", "Laban", 6.0, "🥛", "Salted or plain laban"),
                item("DR3", "Soft Drink", 5.0, "🥤", "Pepsi, 7UP, Mirinda"),
                item("DR4", "Karak Tea", 4.0, "☕", "Spiced cardamom tea"),
            ]),
            cat("deals", "🔥 Street Combos", "DC", [
                item("DC1", "Shawarma Combo", 22.0, "🥙", "Any Shawarma + Fries + Drink"),
                item("DC2", "Falafel Wrap Meal", 15.0, "🧆", "Falafel Wrap + Drink"),
                item("DC3", "Chicken + Laban", 18.0, "🥙", "Chicken Shawarma + Laban"),
                item("DC4", "2 Shawarmas Deal", 35.0, "👫", "2 Any Shawarmas + Fries + 2 Drinks"),
                item("DC5", "Wrap Box (3 Wraps)", 40.0, "📦", "3 Any Wraps + Fries"),
                item("DC6", "Mixed Grill Combo", 60.0, "🍖", "Mixed Grill Platter + 2 Sides + Drinks"),
                item("DC7", "Family Box", 55.0, "👨‍👩‍👧‍👦", "4 Any Wraps + 2 Fries + Drinks"),
            ]),
        ],
        tax=0.05, delivery=0.0, min_order=30.0,
        upsell={"burger_combo": False, "pizza_wings": False, "desserts": False},
        deal_rules={
            "DC1": {"picks": ["wraps"]},
            "DC4": {"picks": ["wraps", "wraps"]},
            "DC5": {"picks": ["wraps", "wraps", "wraps"]},
            "DC6": {"picks": ["platters", "sides", "sides"]},
            "DC7": {"picks": ["wraps", "wraps", "wraps", "wraps"]},
        }
    )
})

# ── 6. Pakistani / Desi ───────────────────────────────────────────────────────
BOTS.append({
    "name": "desi-karahi-house",
    "business_name": "Desi Karahi House",
    "bot_type": "restaurant",
    "tax_rate": 0.08,
    "delivery_fee": 3.0,
    "system_prompt": "You are a friendly ordering assistant for Desi Karahi House, an authentic Pakistani restaurant in the US. Help customers order biryani, karahi, BBQ, and desi combos.",
    "config": make_config(
        categories=[
            cat("mains", "🍛 Main Dishes", "MN", [
                item("MN1", "Chicken Biryani", 10.0, "🍛", "Aromatic basmati rice with chicken"),
                item("MN2", "Mutton Biryani", 12.0, "🍛", "Slow-cooked mutton biryani"),
                item("MN3", "Chicken Karahi", 15.0, "🍲", "Tomato-based karahi, fresh ginger"),
                item("MN4", "Mutton Karahi", 18.0, "🍲", "Slow-cooked mutton karahi"),
            ]),
            cat("bbq", "🍢 BBQ Grill", "BQ", [
                item("BQ1", "Seekh Kabab 2pc", 8.0, "🍢", "Minced beef seekh kabab"),
                item("BQ2", "Chicken Tikka", 10.0, "🍗", "Marinated grilled chicken tikka"),
                item("BQ3", "Malai Boti", 12.0, "🍗", "Creamy malai chicken boti"),
                item("BQ4", "BBQ Platter", 20.0, "🔥", "Seekh + Tikka + Malai Boti"),
            ]),
            cat("bread", "🫓 Breads", "BR", [
                item("BR1", "Naan", 2.0, "🫓", "Soft tandoori naan"),
                item("BR2", "Garlic Naan", 3.0, "🧄", "Buttery garlic naan"),
                item("BR3", "Roghni Naan", 3.0, "🫓", "Sesame topped roghni naan"),
                item("BR4", "Paratha", 3.0, "🫓", "Flaky layered paratha"),
            ]),
            cat("drinks", "🥤 Drinks", "DR", [
                item("DR1", "Lassi Sweet", 4.0, "🥛", "Sweet yogurt drink"),
                item("DR2", "Lassi Salted", 4.0, "🥛", "Salted yogurt drink"),
                item("DR3", "Soft Drink", 2.0, "🥤", "Coke, Sprite, Pepsi"),
                item("DR4", "Chai", 3.0, "☕", "Desi masala chai"),
            ]),
            cat("deals", "🔥 Desi Combos", "DC", [
                item("DC1", "Biryani Combo", 12.0, "🍛", "Chicken Biryani + Drink"),
                item("DC2", "Kabab Meal", 11.0, "🍢", "Seekh Kabab + Naan + Drink"),
                item("DC3", "Tikka Meal", 13.0, "🍗", "Chicken Tikka + Naan + Drink"),
                item("DC4", "BBQ Naan Deal", 22.0, "🔥", "BBQ Platter + 2 Breads + Drink"),
                item("DC5", "Malai Boti Meal", 15.0, "🍗", "Malai Boti + Naan + Drink"),
                item("DC6", "Karahi Family", 30.0, "👨‍👩‍👧‍👦", "Full Karahi + 4 Naan + Drinks"),
                item("DC7", "BBQ Family Combo", 35.0, "🔥", "BBQ Platter + 4 Naan + 4 Drinks"),
                item("DC8", "Mega Desi Deal", 40.0, "⭐", "Any Biryani + BBQ Item + Bread + Drinks"),
            ]),
        ],
        tax=0.08, delivery=3.0, min_order=15.0,
        upsell={"burger_combo": False, "pizza_wings": False, "desserts": False},
        deal_rules={
            "DC4": {"picks": ["bread", "bread"]},
            "DC6": {"picks": ["mains", "bread", "bread"]},
            "DC8": {"picks": ["mains", "bbq", "bread"]},
        }
    )
})

# ── 7. Pizza Palace ───────────────────────────────────────────────────────────
BOTS.append({
    "name": "pizza-palace",
    "business_name": "Pizza Palace",
    "bot_type": "restaurant",
    "tax_rate": 0.08,
    "delivery_fee": 3.0,
    "system_prompt": "You are a friendly ordering assistant for Pizza Palace. Help customers build their perfect pizza order with classic and premium pizzas, wings, and great combo deals.",
    "config": make_config(
        categories=[
            cat("pizza", "🍕 Classic Pizzas", "PZ", [
                item("PZ1", "Margherita", 8.0, "🍕", "Fresh basil, tomato, mozzarella"),
                item("PZ2", "Pepperoni", 10.0, "🍕", "Loaded with crispy pepperoni"),
                item("PZ3", "Veggie Supreme", 9.0, "🌱", "Peppers, mushrooms, olives, onions"),
                item("PZ4", "BBQ Chicken", 11.0, "🍕", "Grilled chicken, BBQ sauce, red onion"),
                item("PZ5", "Hawaiian", 10.0, "🍍", "Ham, pineapple, mozzarella"),
            ]),
            cat("premium", "⭐ Premium Pizzas", "PM", [
                item("PM1", "Meat Lovers", 13.0, "🍖", "Pepperoni + sausage + bacon + ham"),
                item("PM2", "Chicken Ranch", 12.0, "🍗", "Chicken, ranch, red onion, cheddar"),
                item("PM3", "Supreme Deluxe", 13.0, "⭐", "Everything on it"),
                item("PM4", "Alfredo Chicken", 12.0, "🧀", "Creamy alfredo, chicken, spinach"),
            ]),
            cat("sides", "🍟 Sides & Starters", "SD", [
                item("SD1", "Garlic Bread", 4.0, "🥖", "Buttery garlic bread"),
                item("SD2", "Cheesy Breadsticks", 5.0, "🧀", "Mozzarella stuffed breadsticks"),
                item("SD3", "Chicken Wings 6pc", 7.0, "🍗", "Buffalo or BBQ wings"),
                item("SD4", "Chicken Wings 12pc", 12.0, "🍗", "Buffalo or BBQ wings"),
                item("SD5", "Fries", 3.0, "🍟", "Crispy golden fries"),
            ]),
            cat("desserts", "🍰 Desserts", "DS", [
                item("DS1", "Chocolate Lava Cake", 5.0, "🍫", "Warm molten chocolate cake"),
                item("DS2", "Brownie", 4.0, "🍫", "Classic chocolate brownie"),
                item("DS3", "Ice Cream", 3.0, "🍨", "2 scoops, choice of flavour"),
            ]),
            cat("drinks", "🥤 Drinks", "DR", [
                item("DR1", "Soft Drink", 2.0, "🥤", "Coke, Pepsi, Sprite"),
                item("DR2", "1.5L Drink", 4.0, "🥤", "Large bottle for sharing"),
                item("DR3", "Juice", 3.0, "🍊", "Apple, orange or mango"),
                item("DR4", "Water", 1.5, "💧", "Still water"),
            ]),
            cat("deals", "🔥 Pizza Deals", "DC", [
                item("DC1", "1 Pizza + Drink", 12.0, "🍕", "Any 1 Pizza + Soft Drink"),
                item("DC2", "Large Pizza Meal", 16.0, "🍕", "1 Large Pizza + Garlic Bread + Drink"),
                item("DC3", "Pizza + Wings Deal", 15.0, "🍗", "Any Pizza + Wings 6pc + Drink"),
                item("DC4", "2 Pizzas Deal", 20.0, "👫", "2 Any Pizzas + 2 Drinks"),
                item("DC5", "Date Night", 22.0, "❤️", "1 Large Pizza + Wings 6pc + Garlic Bread + 2 Drinks"),
                item("DC6", "Double Large + Wings", 30.0, "🔥", "2 Large Pizzas + Wings 12pc + 1.5L Drink"),
                item("DC7", "3 Pizza Party", 28.0, "🎉", "3 Any Pizzas + 3 Drinks"),
                item("DC8", "Family Box", 35.0, "👨‍👩‍👧‍👦", "2 Large Pizzas + Breadsticks + Dessert + Drink"),
                item("DC9", "BOGO Large Pizza", 20.0, "🎁", "Buy 1 Large Get 1 Medium Free"),
                item("DC10", "2 Large + 2 Sides", 40.0, "⭐", "2 Large Pizzas + 2 Sides + 2 Drinks"),
                item("DC11", "Party Deal", 60.0, "🎊", "4 Pizzas + Wings + 1.5L Drinks"),
            ]),
        ],
        tax=0.08, delivery=3.0, min_order=15.0,
        upsell={"burger_combo": False, "pizza_wings": True, "desserts": True},
        deal_rules={
            "DC1": {"picks": ["pizza"]},
            "DC2": {"picks": ["pizza"]},
            "DC3": {"picks": ["pizza"]},
            "DC4": {"picks": ["pizza", "pizza"]},
            "DC5": {"picks": ["pizza"]},
            "DC6": {"picks": ["pizza", "pizza"]},
            "DC7": {"picks": ["pizza", "pizza", "pizza"]},
            "DC8": {"picks": ["pizza", "pizza", "desserts"]},
            "DC9": {"picks": ["pizza", "pizza"]},
            "DC10": {"picks": ["pizza", "pizza"]},
            "DC11": {"picks": ["pizza", "pizza", "pizza", "pizza"]},
        }
    )
})

# ── DB Insertion ──────────────────────────────────────────────────────────────

def run():
    # Ensure DB tables exist (safe to call multiple times)
    migrate_db()

    db = SessionLocal()
    try:
        admin = db.query(User).filter(User.username == "admin").first()
        if not admin:
            import os
            admin = User(
                username="admin",
                hashed_password=hash_password(os.getenv("ADMIN_PASSWORD", "admin123")),
                role="admin"
            )
            db.add(admin)
            db.commit()
            print("✅ Admin user created.")

        created, skipped = [], []
        for b in BOTS:
            existing = db.query(WhatsappBot).filter(WhatsappBot.name == b["name"]).first()
            if existing:
                skipped.append(b["name"])
                continue

            new_bot = WhatsappBot(
                owner_id=admin.id,
                name=b["name"],
                business_name=b["business_name"],
                bot_type=b.get("bot_type", "restaurant"),
                meta_token="",
                phone_number_id="",
                waba_id="",
                verify_token="",
                manager_number=MANAGER_NUMBER,
                ai_provider="groq",
                ai_api_key="",
                language="en",
                system_prompt=b.get("system_prompt", ""),
                tax_rate=b.get("tax_rate", 0.08),
                delivery_fee=b.get("delivery_fee", 3.0),
                config_json=b["config"],
                status="pending_config",
            )
            db.add(new_bot)
            db.flush()

            # Add to admin's bot list
            admin_bots = admin.bots
            if b["name"] not in admin_bots:
                admin_bots.append(b["name"])
                admin.bots = admin_bots

            created.append(b["name"])

        db.commit()
        print(f"\n✅ Created {len(created)} bots:")
        for n in created:
            print(f"   + {n}")
        if skipped:
            print(f"\n⚠️  Skipped (already exist): {', '.join(skipped)}")
        print("\n📋 Next steps:")
        print("   1. Open Bot Builder → set same phone_number_id for multi-bot selector")
        print("   2. Or set unique phone_number_id per bot for standalone bots")
        print("   3. Add Meta token + AI key via credentials modal")
        print(f"   4. Manager number already set: {MANAGER_NUMBER}")

    except Exception as e:
        db.rollback()
        print(f"❌ Error: {e}")
        import traceback; traceback.print_exc()
    finally:
        db.close()

if __name__ == "__main__":
    run()
