import os
from datetime import datetime

LOG_FILE = "logs/ai.log"

def log_ai(tag, message):
    os.makedirs("logs", exist_ok=True)

    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"\n[{datetime.now()}] {tag}: {message}\n")