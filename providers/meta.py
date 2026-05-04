# providers/meta.py
#
# Thin wrapper around the Meta Cloud API used by the router's AI-fallback path.
# The restaurant bot's full send_* functions live in
# bots/restaurant/whatsapp_handlers.py and call _send_request directly.
# This class is used by whatsapp_router.py for simple text replies only.

from __future__ import annotations

import os
import logging

import aiohttp
from session import SharedSession

logger = logging.getLogger(__name__)

WHATSAPP_TOKEN         = os.getenv("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
META_API_VERSION       = os.getenv("WHATSAPP_API_VERSION", "v19.0")


class MetaProvider:
    def __init__(self, bot):
        self.bot      = bot
        self.token    = (getattr(bot, "meta_token", None) or WHATSAPP_TOKEN)
        self.phone_id = (getattr(bot, "phone_number_id", None) or WHATSAPP_PHONE_NUMBER_ID)

    async def send_text(self, to: str, message: str) -> bool:
        url     = f"https://graph.facebook.com/{META_API_VERSION}/{self.phone_id}/messages"
        headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": message},
        }
        try:
            session = await SharedSession.get_session()
            async with session.post(url, json=payload, headers=headers) as resp:
                if resp.status >= 400:
                    text = await resp.text()
                    logger.error(f"[MetaProvider] send failed {resp.status}: {text}")
                    return False
                return True
        except Exception as exc:
            logger.error(f"[MetaProvider] send exception: {exc}")
            return False
