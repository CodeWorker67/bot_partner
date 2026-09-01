"""Имя бота для подстановки в тексты (из Telegram get_me)."""
from __future__ import annotations

from aiogram import Bot

CASPER_BOT_USERNAME = "casper77bot"

_bot_display_name = "VPN"
_bot_username = ""


def bot_display_name() -> str:
    return _bot_display_name


def bot_username() -> str:
    return _bot_username


def is_casper_bot() -> bool:
    name = (_bot_username or "").lstrip("@").lower()
    if not name:
        from config import BOT_USERNAME

        name = (BOT_USERNAME or "").lstrip("@").lower()
    return name == CASPER_BOT_USERNAME


async def init_bot_display_name(bot: Bot) -> str:
    global _bot_display_name, _bot_username
    me = await bot.get_me()
    _bot_username = (me.username or "").lstrip("@")
    _bot_display_name = me.full_name or (f"@{_bot_username}" if _bot_username else "VPN")
    from lexicon import apply_bot_name_to_lexicon

    apply_bot_name_to_lexicon(_bot_display_name)
    from logging_config import logger

    logger.info(
        "Bot identity: @{} name={!r} casper_prize={}",
        _bot_username or "-",
        _bot_display_name,
        is_casper_bot(),
    )
    return _bot_display_name
