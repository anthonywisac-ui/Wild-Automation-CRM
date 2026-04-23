#!/usr/bin/env python3
# generate_bot.py - Run this to create a new bot from config
import os, json, shutil
from pathlib import Path

def create_bot_from_config(config_file):
    with open(config_file, 'r', encoding='utf-8') as f:
        cfg = json.load(f)
    bot_name = cfg['bot_name']
    bot_type = cfg.get('bot_type', 'order')
    output_dir = Path(f"bots/{bot_name}")
    if output_dir.exists():
        print(f"Bot {bot_name} already exists. Delete it first.")
        return
    output_dir.mkdir(parents=True)
    
    # Copy template from restaurant bot (or use generic)
    template_dir = Path("bots/restaurant")
    if template_dir.exists():
        # Copy all files except db, sessions, etc.
        for item in template_dir.iterdir():
            if item.is_file() and item.name not in ['db.py', '__pycache__']:
                shutil.copy2(item, output_dir / item.name)
    # Then modify config.py with new values
    config_path = output_dir / 'config.py'
    if config_path.exists():
        with open(config_path, 'r') as f:
            content = f.read()
        # Replace BOT_NAME and other settings
        content = content.replace('restaurant', bot_name)
        with open(config_path, 'w') as f:
            f.write(content)
    # Create menu.json from config
    with open(output_dir / 'menu.json', 'w', encoding='utf-8') as f:
        json.dump(cfg.get('menu', {}), f, indent=2)
    # Create locales
    locales_dir = output_dir / 'locales'
    locales_dir.mkdir(exist_ok=True)
    for lang, strings in cfg.get('strings', {}).items():
        with open(locales_dir / f"{lang}.json", 'w', encoding='utf-8') as f:
            json.dump(strings, f, indent=2)
    print(f"✅ Bot '{bot_name}' created. Set BOT_TYPE={bot_name} in Railway to run it.")

if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        print("Usage: python generate_bot.py config.json")
    else:
        create_bot_from_config(sys.argv[1])
