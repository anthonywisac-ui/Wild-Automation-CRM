# providers/__init__.py
# Returns the correct WhatsApp message provider for a given bot.
# Usage: provider = get_provider(bot)

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from db import WhatsappBot


def get_provider(bot: "WhatsappBot"):
    """
    Factory: returns WwebjsProvider or MetaProvider based on bot.provider field.
    Falls back to MetaProvider if provider is unset or unknown.
    """
    if bot and getattr(bot, 'provider', 'meta') == 'wwebjs':
        from providers.wwebjs import WwebjsProvider
        return WwebjsProvider(bot)

    from providers.meta import MetaProvider
    return MetaProvider(bot)
