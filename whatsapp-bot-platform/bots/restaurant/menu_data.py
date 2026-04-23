import json
import os

MENU = {}

def load_menu():
    global MENU
    # Get the directory where this file (menu_data.py) is located
    current_dir = os.path.dirname(os.path.abspath(__file__))
    menu_path = os.path.join(current_dir, "menu.json")
    try:
        with open(menu_path, "r", encoding="utf-8") as f:
            MENU = json.load(f)
        print("Menu loaded successfully from", menu_path)
    except Exception as e:
        print(f"Error loading menu: {e}")
        MENU = {}

def reload_menu():
    load_menu()

load_menu()