"""Имя бота для подстановки в тексты (из Telegram get_me)."""
from __future__ import annotations

from aiogram import Bot

CASPER_BOT_USERNAME = "casper77bot"

# username (lower) -> (win photo file_id, discount photo file_id)
START_PRIZE_BOT_PHOTOS: dict[str, tuple[str, str]] = {
    CASPER_BOT_USERNAME: (
        "AgACAgQAAxkBAAEGi9dqlwN7D_06zVVD-fhwAxeuI-NFMQACCxJrGyrruFDhDSQqwDBHrQEAAwIAA3kAAz0E",
        "AgACAgQAAxkBAAEGi81qlwAB7nQu0AayUJyF4mdiwvVmM4cAAgQSaxsq67hQwsXm0NDMQHYBAAMCAAN5AAM9BA",
    ),
    "thtemhmsldjqioszfxbot": (
        "AgACAgQAAxkBAAEkjSFqwzBoXR9dX9ZmclPQXEHYcVVznwACYw5rG66LIFIJUg_7D_wSxAEAAwIAA3kAAz0E",
        "AgACAgQAAxkBAAEkjS5qwzC84PL_mcveSyVSj4X_mChDZQACZA5rG66LIFJoosuFS_mGTwEAAwIAA3kAAz0E",
    ),
}

_bot_display_name = "VPN"
_bot_username = ""


def bot_display_name() -> str:
    return _bot_display_name


def bot_username() -> str:
    return _bot_username


def _resolved_bot_username_lower() -> str:
    name = (_bot_username or "").lstrip("@").lower()
    if not name:
        from config import BOT_USERNAME

        name = (BOT_USERNAME or "").lstrip("@").lower()
    return name


def is_casper_bot() -> bool:
    return _resolved_bot_username_lower() == CASPER_BOT_USERNAME


def start_prize_photo_ids() -> tuple[str, str] | None:
    return START_PRIZE_BOT_PHOTOS.get(_resolved_bot_username_lower())


def is_start_prize_bot() -> bool:
    return start_prize_photo_ids() is not None


async def init_bot_display_name(bot: Bot) -> str:
    global _bot_display_name, _bot_username
    me = await bot.get_me()
    _bot_username = (me.username or "").lstrip("@")
    _bot_display_name = me.full_name or (f"@{_bot_username}" if _bot_username else "VPN")
    from lexicon import apply_bot_name_to_lexicon

    apply_bot_name_to_lexicon(_bot_display_name)
    from logging_config import logger

    logger.info(
        "Bot identity: @{} name={!r} start_prize={}",
        _bot_username or "-",
        _bot_display_name,
        is_start_prize_bot(),
    )
    return _bot_display_name
