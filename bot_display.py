"""Имя бота для подстановки в тексты (из Telegram get_me)."""
from __future__ import annotations

from aiogram import Bot

_bot_display_name = "VPN"


def bot_display_name() -> str:
    return _bot_display_name


async def init_bot_display_name(bot: Bot) -> str:
    global _bot_display_name
    me = await bot.get_me()
    _bot_display_name = me.full_name or (f"@{me.username}" if me.username else "VPN")
    from lexicon import apply_bot_name_to_lexicon

    apply_bot_name_to_lexicon(_bot_display_name)
    return _bot_display_name
