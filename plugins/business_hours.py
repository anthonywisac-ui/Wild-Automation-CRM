"""
Business Hours Plugin — auto-reply "we're closed" outside working hours.
Config: open_time, close_time, days, timezone, closed_message.
"""
from datetime import datetime
from typing import Optional
from plugins import BasePlugin


class BusinessHoursPlugin(BasePlugin):
    name = "business_hours"
    title = "Business Hours"
    description = "Automatically reply to customers outside working hours."
    config_schema = [
        {"key": "open_time",  "label": "Opening Time",  "type": "time",   "default": "10:00"},
        {"key": "close_time", "label": "Closing Time",   "type": "time",   "default": "22:00"},
        {"key": "days", "label": "Open Days (comma-separated: mon,tue,wed,thu,fri,sat,sun)",
         "type": "text", "default": "mon,tue,wed,thu,fri,sat,sun"},
        {"key": "timezone_offset", "label": "UTC Offset (e.g. +5 for PKT, -5 for EST)",
         "type": "number", "default": "0"},
        {"key": "closed_message", "label": "Closed Reply", "type": "textarea",
         "default": "Sorry, we're closed right now! ⏰\nWe're open {open_time} – {close_time}.\nPlease message us during business hours."},
    ]

    async def pre_message(self, sender, message, bot, session, config, db) -> Optional[str]:
        try:
            open_time  = config.get("open_time",  "10:00")
            close_time = config.get("close_time", "22:00")
            days_str   = config.get("days", "mon,tue,wed,thu,fri,sat,sun")
            tz_offset  = float(config.get("timezone_offset", 0))
            closed_msg = config.get("closed_message",
                "Sorry, we're closed right now! ⏰\nWe're open {open_time} – {close_time}.")

            from datetime import timezone, timedelta
            tz = timezone(timedelta(hours=tz_offset))
            now = datetime.now(tz)

            day_map = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
            open_days = {day_map[d.strip().lower()] for d in days_str.split(",") if d.strip().lower() in day_map}

            if now.weekday() not in open_days:
                return closed_msg.format(open_time=open_time, close_time=close_time)

            oh, om = map(int, open_time.split(":"))
            ch, cm = map(int, close_time.split(":"))
            open_mins  = oh * 60 + om
            close_mins = ch * 60 + cm
            now_mins   = now.hour * 60 + now.minute

            if not (open_mins <= now_mins < close_mins):
                return closed_msg.format(open_time=open_time, close_time=close_time)

        except Exception:
            pass
        return None
