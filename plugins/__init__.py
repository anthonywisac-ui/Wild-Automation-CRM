"""
Plugin system for Wild CRM WhatsApp bots.

Each plugin lives in this directory and subclasses BasePlugin.
Plugins are discovered automatically — drop a .py file here, done.

Hook order:
  1. pre_message  — runs before bot flow; return str to intercept, None to pass through
  2. post_order   — runs after order confirmed (fire-and-forget)
"""
import importlib
import os
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ── Registry built at import time ────────────────────────────────────────────
_REGISTRY: dict = {}  # name -> plugin class


class BasePlugin:
    name: str = ""
    title: str = ""
    description: str = ""
    version: str = "1.0"
    config_schema: list = []  # list of {key, label, type, default, placeholder}

    async def pre_message(self, sender: str, message: str, bot, session: dict, config: dict, db) -> Optional[str]:
        """Return a reply string to intercept the message, or None to pass through."""
        return None

    async def post_order(self, order_id, session: dict, bot, config: dict, db):
        """Called after order confirmed. Fire-and-forget side effect."""
        pass


def _discover():
    here = os.path.dirname(__file__)
    for fname in os.listdir(here):
        if fname.startswith("_") or not fname.endswith(".py"):
            continue
        module_name = fname[:-3]
        try:
            mod = importlib.import_module(f"plugins.{module_name}")
            for attr in dir(mod):
                cls = getattr(mod, attr)
                if (isinstance(cls, type) and issubclass(cls, BasePlugin)
                        and cls is not BasePlugin and cls.name):
                    _REGISTRY[cls.name] = cls
        except Exception as e:
            logger.warning(f"Plugin load error ({module_name}): {e}")


_discover()


def list_plugins() -> list:
    return [
        {
            "name": cls.name,
            "title": cls.title,
            "description": cls.description,
            "version": cls.version,
            "config_schema": cls.config_schema,
        }
        for cls in _REGISTRY.values()
    ]


def get_plugin(name: str) -> Optional[BasePlugin]:
    cls = _REGISTRY.get(name)
    return cls() if cls else None


async def run_pre_message_hooks(sender: str, message: str, bot, db) -> Optional[str]:
    """Run all enabled plugins for bot. Return first non-None reply."""
    try:
        from db import BotPlugin
        rows = db.query(BotPlugin).filter(
            BotPlugin.bot_id == bot.id,
            BotPlugin.enabled == True
        ).all()
        for row in rows:
            plugin = get_plugin(row.plugin_name)
            if plugin is None:
                continue
            config = json.loads(row.config_json or "{}")
            # Lazy import to avoid circular
            try:
                from bots.restaurant.db import get_session_db
                session = get_session_db(sender, bot.id, db_session=db) or {}
            except Exception:
                session = {}
            reply = await plugin.pre_message(sender, message, bot, session, config, db)
            if reply:
                return reply
    except Exception as e:
        logger.error(f"Plugin hook error: {e}")
    return None


async def run_post_order_hooks(order_id, session: dict, bot, db):
    """Run post_order hooks for all enabled plugins."""
    try:
        from db import BotPlugin
        rows = db.query(BotPlugin).filter(
            BotPlugin.bot_id == bot.id,
            BotPlugin.enabled == True
        ).all()
        for row in rows:
            plugin = get_plugin(row.plugin_name)
            if plugin is None:
                continue
            config = json.loads(row.config_json or "{}")
            try:
                await plugin.post_order(order_id, session, bot, config, db)
            except Exception as e:
                logger.error(f"Plugin post_order error ({row.plugin_name}): {e}")
    except Exception as e:
        logger.error(f"Plugin post_order hook error: {e}")
