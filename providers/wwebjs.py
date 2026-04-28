# providers/wwebjs.py
#
# Handles all communication with the wa-bridge Node.js service.
# Your FastAPI bot calls THIS — it calls the bridge — bridge sends via WhatsApp.
# No data leaves your server. No third-party relay.
#
# Also owns:
#   - _meta_payload_to_text()  : converts Meta interactive payloads to numbered text
#   - store_menu_map() / get_menu_map() : maps user's "1/2/3" reply → real button ID

from __future__ import annotations

import os
import time
import logging
import asyncio
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

# ── Default bridge URL (override per-bot via bot.wwebjs_bridge_url) ──────────
DEFAULT_BRIDGE_URL = os.getenv("WWEBJS_BRIDGE_URL", "http://localhost:3000")
BRIDGE_API_KEY     = os.getenv("BRIDGE_API_KEY", "")

# ── In-memory menu map ────────────────────────────────────────────────────────
# Stores the last numbered-menu mapping per (session, phone) so that when a
# user types "1", "2", "3" we know which button ID it corresponds to.
# Format: { "session_name:phone": {"timestamp": float, "map": {"1": "CAT_DEALS", ...}} }
_menu_maps: dict = {}
MENU_MAP_TTL = 300  # seconds — clear mapping if user hasn't replied in 5 min


def store_menu_map(session_name: str, phone: str, menu_map: dict) -> None:
    """Called when a numbered menu is sent to a wwebjs user."""
    if not menu_map:
        return
    key = f"{session_name}:{phone}"
    _menu_maps[key] = {"timestamp": time.time(), "map": menu_map}


def get_menu_map(session_name: str, phone: str) -> Optional[dict]:
    """
    Returns the stored menu map for this user and clears it (one-time use).
    Returns None if no map exists or it has expired.
    """
    key = f"{session_name}:{phone}"
    entry = _menu_maps.pop(key, None)
    if not entry:
        return None
    if time.time() - entry["timestamp"] > MENU_MAP_TTL:
        return None
    return entry["map"]


# ── Text conversion ───────────────────────────────────────────────────────────

def _meta_payload_to_text(payload: dict) -> tuple[str, dict]:
    """
    Convert a Meta Cloud API interactive payload into:
      - plain text string (numbered list / button list)
      - menu_map dict  {"1": "CAT_DEALS", "2": "CAT_FASTFOOD", ...}

    Handles: type=text, type=interactive (list + button)
    Returns ("", {}) for unsupported types so we degrade gracefully.
    """
    msg_type = payload.get("type", "text")

    if msg_type == "text":
        body = payload.get("text", {}).get("body", "")
        return body, {}

    if msg_type != "interactive":
        return "", {}

    interactive = payload.get("interactive", {})
    itype       = interactive.get("type", "")
    header_text = interactive.get("header", {}).get("text", "")
    body_text   = interactive.get("body",   {}).get("text", "")
    footer_text = interactive.get("footer", {}).get("text", "")
    action      = interactive.get("action", {})

    lines:    list[str] = []
    menu_map: dict      = {}

    if header_text:
        lines.append(f"*{header_text}*")
    if body_text:
        lines.append(body_text)
    lines.append("")

    if itype == "list":
        counter  = 1
        sections = action.get("sections", [])
        for section in sections:
            title = section.get("title", "")
            if title and len(sections) > 1:
                lines.append(f"_{title}_")
            for row in section.get("rows", []):
                row_id   = row.get("id", "")
                row_title = row.get("title", "")
                row_desc  = row.get("description", "")
                menu_map[str(counter)] = row_id
                desc_part = f" - {row_desc}" if row_desc else ""
                lines.append(f"{counter}. {row_title}{desc_part}")
                counter += 1
        lines.append("")
        lines.append("Reply with a number 👆")

    elif itype == "button":
        buttons = action.get("buttons", [])
        for i, btn in enumerate(buttons, 1):
            btn_id    = btn.get("reply", {}).get("id", "")
            btn_title = btn.get("reply", {}).get("title", "")
            menu_map[str(i)] = btn_id
            lines.append(f"{i}. {btn_title}")
        lines.append("")
        lines.append("Reply with a number 👆")

    if footer_text:
        lines.append(f"\n_{footer_text}_")

    return "\n".join(lines), menu_map


# ── Provider class ────────────────────────────────────────────────────────────

class WwebjsProvider:
    """
    Sends messages through the local wa-bridge service.
    Converts all Meta interactive payloads to numbered plain-text menus.
    """

    def __init__(self, bot):
        self.bot          = bot
        self.session_name = getattr(bot, "wwebjs_session", None) or ""
        self.bridge_url   = (
            getattr(bot, "wwebjs_bridge_url", None)
            or DEFAULT_BRIDGE_URL
        ).rstrip("/")

    # ── Core send ─────────────────────────────────────────────────────────────

    async def send_text(self, to: str, message: str) -> bool:
        """Send a plain text message via wa-bridge."""
        if not self.session_name:
            logger.error(f"[WwebjsProvider] bot '{self.bot.name}' has no wwebjs_session set")
            return False
        if not message or not to:
            return False

        url     = f"{self.bridge_url}/sessions/{self.session_name}/send-text"
        headers = {"Content-Type": "application/json"}
        if BRIDGE_API_KEY:
            headers["X-Bridge-Key"] = BRIDGE_API_KEY

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json={"to": to, "message": message},
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logger.error(
                            f"[WwebjsProvider] send failed {resp.status} for "
                            f"session='{self.session_name}': {body}"
                        )
                        return False
                    return True

        except asyncio.TimeoutError:
            logger.error(f"[WwebjsProvider] send timeout for session='{self.session_name}'")
            return False
        except Exception as exc:
            logger.error(f"[WwebjsProvider] send exception: {exc}")
            return False

    # ── Payload dispatcher ────────────────────────────────────────────────────

    async def dispatch_payload(self, payload: dict) -> bool:
        """
        Entry point called from _send_request in whatsapp_handlers.py.
        Converts the Meta payload to text, stores the menu map, sends.
        """
        to = payload.get("to", "")
        if not to:
            return False

        text, menu_map = _meta_payload_to_text(payload)

        if not text:
            logger.warning(
                f"[WwebjsProvider] Empty text after conversion "
                f"(type={payload.get('type')}), skipping"
            )
            return False

        # Persist menu map so the next incoming "1"/"2" can be translated
        if menu_map:
            store_menu_map(self.session_name, to, menu_map)

        return await self.send_text(to, text)


# ── Bridge management helpers (used by CMS routes) ───────────────────────────

async def bridge_start_session(session_name: str, bridge_url: str = DEFAULT_BRIDGE_URL) -> dict:
    """Tell the bridge to start a new session. Returns bridge response."""
    url     = f"{bridge_url.rstrip('/')}/sessions/{session_name}/start"
    headers = {"Content-Type": "application/json"}
    if BRIDGE_API_KEY:
        headers["X-Bridge-Key"] = BRIDGE_API_KEY
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers,
                                timeout=aiohttp.ClientTimeout(total=30)) as resp:
            return await resp.json()


async def bridge_get_qr(session_name: str, bridge_url: str = DEFAULT_BRIDGE_URL) -> dict:
    """Fetch the current QR string from the bridge."""
    url     = f"{bridge_url.rstrip('/')}/sessions/{session_name}/qr"
    headers = {}
    if BRIDGE_API_KEY:
        headers["X-Bridge-Key"] = BRIDGE_API_KEY
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers,
                               timeout=aiohttp.ClientTimeout(total=10)) as resp:
            return await resp.json()


async def bridge_get_status(session_name: str, bridge_url: str = DEFAULT_BRIDGE_URL) -> str:
    """Returns status string: CONNECTED / SCAN_QR_CODE / DISCONNECTED / etc."""
    url     = f"{bridge_url.rstrip('/')}/sessions/{session_name}/status"
    headers = {}
    if BRIDGE_API_KEY:
        headers["X-Bridge-Key"] = BRIDGE_API_KEY
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers,
                                   timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json()
                return data.get("status", "UNKNOWN")
    except Exception:
        return "BRIDGE_UNREACHABLE"


async def bridge_delete_session(session_name: str, bridge_url: str = DEFAULT_BRIDGE_URL) -> bool:
    """Logout and delete a session from the bridge."""
    url     = f"{bridge_url.rstrip('/')}/sessions/{session_name}"
    headers = {}
    if BRIDGE_API_KEY:
        headers["X-Bridge-Key"] = BRIDGE_API_KEY
    async with aiohttp.ClientSession() as session:
        async with session.delete(url, headers=headers,
                                  timeout=aiohttp.ClientTimeout(total=30)) as resp:
            return resp.status == 200
