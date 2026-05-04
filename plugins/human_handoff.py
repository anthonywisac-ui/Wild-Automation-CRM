"""
Human Handoff Plugin — detect escalation requests and notify manager.
When customer says "human", "agent", "manager" etc., sends alert to manager
and puts session in "waiting_human" state (bot goes silent until reset).
"""
from typing import Optional
from plugins import BasePlugin


TRIGGER_PHRASES = [
    "human", "agent", "manager", "real person", "talk to someone",
    "speak to", "help me", "not working", "problem", "complaint",
    "انسان", "مدير", "مشكلة",  # Arabic
]


class HumanHandoffPlugin(BasePlugin):
    name = "human_handoff"
    title = "Human Handoff"
    description = "Detect escalation requests, notify manager, and pause bot for human takeover."
    config_schema = [
        {"key": "trigger_words", "label": "Trigger Words (comma-separated)",
         "type": "text", "default": "human,agent,manager,complaint,problem,help me"},
        {"key": "handoff_message", "label": "Customer Reply on Handoff", "type": "textarea",
         "default": "👤 I've notified our team and someone will be with you shortly! Please hold on."},
        {"key": "manager_alert", "label": "Manager Alert Message", "type": "textarea",
         "default": "🚨 Customer +{sender} is requesting human support.\nMessage: {message}"},
        {"key": "pause_bot", "label": "Pause bot after handoff (bot goes silent)",
         "type": "checkbox", "default": "true"},
    ]

    async def pre_message(self, sender, message, bot, session, config, db) -> Optional[str]:
        import json as _json
        try:
            # If already in human handoff mode, stay silent
            if session.get("human_handoff_active"):
                return "⏳ Our team will be with you shortly. Please wait."

            triggers_raw = config.get("trigger_words", "human,agent,manager,complaint,problem")
            triggers = [t.strip().lower() for t in triggers_raw.split(",") if t.strip()]
            msg_lower = message.lower()

            matched = any(t in msg_lower for t in triggers)
            if not matched:
                return None

            # Mark session as handed off
            pause = str(config.get("pause_bot", "true")).lower() == "true"
            if pause:
                session["human_handoff_active"] = True
                try:
                    from bots.restaurant.db import save_session_db
                    save_session_db(sender, bot.id, session, db_session=db)
                except Exception:
                    pass

            # Alert manager
            alert_tpl = config.get("manager_alert",
                "🚨 Customer +{sender} is requesting human support.\nMessage: {message}")
            alert = alert_tpl.format(sender=sender, message=message)
            try:
                from config import MANAGER_NUMBER
                manager_to = (bot.manager_number or MANAGER_NUMBER or "").lstrip("+")
                if manager_to:
                    from bots.restaurant.whatsapp_handlers import send_text_message
                    import asyncio
                    asyncio.create_task(send_text_message(manager_to, alert, bot))
            except Exception:
                pass

            return config.get("handoff_message",
                "👤 I've notified our team and someone will be with you shortly!")

        except Exception:
            pass
        return None
