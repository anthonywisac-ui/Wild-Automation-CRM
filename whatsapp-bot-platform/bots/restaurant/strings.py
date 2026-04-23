import json
STRINGS = {}
def load_strings():
    global STRINGS
    try:
        with open("locales/en.json", "r") as f:
            STRINGS["en"] = json.load(f)
    except:
        STRINGS["en"] = {}
def t(lang, key):
    return STRINGS.get(lang, STRINGS.get("en", {})).get(key, key)
load_strings()
def reload_strings():
    load_strings()
