"""
Auto FAQ Plugin — keyword-match incoming messages and reply with preset answers.
No AI needed. Great for: delivery time, location, prices, phone number.
Config: list of {keywords, answer} pairs.
"""
from typing import Optional
from plugins import BasePlugin


class AutoFaqPlugin(BasePlugin):
    name = "auto_faq"
    title = "Auto FAQ"
    description = "Auto-reply to common questions using keyword matching. No AI needed."
    config_schema = [
        {
            "key": "faqs",
            "label": "FAQ Entries (JSON array)",
            "type": "json",
            "default": '[{"keywords":["location","address","where"],"answer":"We are located at 123 Main St."},{"keywords":["delivery time","how long"],"answer":"Delivery takes 30-45 minutes."}]',
            "placeholder": '[{"keywords":["hours","open"],"answer":"We are open 10am-10pm daily."}]',
        },
        {
            "key": "case_sensitive",
            "label": "Case Sensitive Matching",
            "type": "checkbox",
            "default": "false",
        },
    ]

    async def pre_message(self, sender, message, bot, session, config, db) -> Optional[str]:
        import json as _json
        try:
            faqs_raw = config.get("faqs", "[]")
            faqs = _json.loads(faqs_raw) if isinstance(faqs_raw, str) else faqs_raw
            case_sensitive = str(config.get("case_sensitive", "false")).lower() == "true"
            text = message if case_sensitive else message.lower()

            for faq in faqs:
                keywords = faq.get("keywords", [])
                answer = faq.get("answer", "")
                if not answer:
                    continue
                for kw in keywords:
                    kw_check = kw if case_sensitive else kw.lower()
                    if kw_check in text:
                        return answer
        except Exception:
            pass
        return None
