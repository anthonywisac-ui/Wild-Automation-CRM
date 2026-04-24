import json
import os

STRINGS = {}

def load_strings():
    global STRINGS
    current_dir = os.path.dirname(os.path.abspath(__file__))
    locales_dir = os.path.join(current_dir, "locales")
    
    if not os.path.exists(locales_dir):
        os.makedirs(locales_dir)
    
    for filename in os.listdir(locales_dir):
        if filename.endswith(".json"):
            lang = filename.replace(".json", "")
            try:
                with open(os.path.join(locales_dir, filename), "r", encoding="utf-8") as f:
                    STRINGS[lang] = json.load(f)
            except Exception as e:
                print(f"Error loading locale {lang}: {e}")

def t(lang, key):
    # Try requested lang, fallback to en, then to key itself
    lang_dict = STRINGS.get(lang, STRINGS.get("en", {}))
    return lang_dict.get(key, STRINGS.get("en", {}).get(key, key))

load_strings()

def reload_strings():
    load_strings()
