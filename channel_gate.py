"""Проверка обязательной подписки на канал партнёра."""
from functools import wraps
from typing import Callable

from aiogram.types import CallbackQuery, Message

from bot import bot, sql
from keyboard import channel_keyboard
from lexicon import lexicon
from logging_config import logger


async def needs_channel_block(user_id: int) -> tuple[bool, str | None]:
    settings = await sql.get_bot_settings()
    if not settings or not settings.get("channel_required"):
        return False, None
    user = await sql.get_user(user_id)
    if user and user[7]:
        return False, None
    url = settings.get("channel_url") or ""
    return True, url


async def verify_channel_subscription(user_id: int) -> bool:
    settings = await sql.get_bot_settings()
    if not settings or not settings.get("channel_id"):
        return True
    try:
        member = await bot.get_chat_member(settings["channel_id"], user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception as e:
        logger.warning(f"verify_channel_subscription {user_id}: {e}")
        return False


async def send_channel_required(target: Message | CallbackQuery, channel_url: str) -> None:
    text = lexicon["channel_required"]
    kb = channel_keyboard(channel_url)
    if isinstance(target, CallbackQuery):
        await target.message.answer(text, reply_markup=kb)
        await target.answer()
    else:
        await target.answer(text, reply_markup=kb)


def require_channel_sub(handler: Callable):
    @wraps(handler)
    async def wrapper(event, *args, **kwargs):
        user_id = event.from_user.id
        blocked, url = await needs_channel_block(user_id)
        if blocked:
            await send_channel_required(event, url or "")
            return
        return await handler(event, *args, **kwargs)

    return wrapper
