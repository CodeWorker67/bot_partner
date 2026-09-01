import asyncio
from datetime import datetime

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InputMediaPhoto

from bot import bot, sql
from bot_display import is_casper_bot
from config import CHECKER_ID
from keyboard import (
    keyboard_buy_tiers,
    keyboard_start_prize_claim,
    keyboard_start_prize_hurry,
    keyboard_start_prize_reveal,
)
from lexicon import lexicon
from logging_config import logger
from telegram_ids import is_telegram_chat_id

router = Router()

_USER_TUPLE_RESERVE_FIELD = 8
_START_PRIZE_DELAY_SEC = 10
_CLAIM_WATCH_SEC = 600
_START_PRIZE_WIN_PHOTO = (
    "AgACAgQAAxkBAAEGi9dqlwN7D_06zVVD-fhwAxeuI-NFMQACCxJrGyrruFDhDSQqwDBHrQEAAwIAA3kAAz0E"
)
_START_PRIZE_DISCOUNT_PHOTO = (
    "AgACAgQAAxkBAAEGi81qlwAB7nQu0AayUJyF4mdiwvVmM4cAAgQSaxsq67hQwsXm0NDMQHYBAAMCAAN5AAM9BA"
)

_prize_scheduled: set[int] = set()
_claim_watchers: set[int] = set()


def schedule_start_prize(user_id: int) -> None:
    if not is_casper_bot() or not is_telegram_chat_id(user_id):
        return
    if user_id in _prize_scheduled:
        return
    _prize_scheduled.add(user_id)
    asyncio.create_task(_send_start_prize_later(user_id))


async def _notify_checker(text: str) -> None:
    if CHECKER_ID is None:
        return
    try:
        await bot.send_message(chat_id=CHECKER_ID, text=text)
    except Exception as e:
        logger.error("start_prize: не удалось отправить CHECKER_ID: {}", e)


def _fmt_pay_time(tc: datetime | None) -> str:
    if tc is None:
        return "—"
    return tc.strftime("%Y-%m-%d %H:%M")


def _has_paid(user_data) -> bool:
    return bool(
        user_data
        and len(user_data) > _USER_TUPLE_RESERVE_FIELD
        and user_data[_USER_TUPLE_RESERVE_FIELD]
    )


async def _edit_prize_photo(callback: CallbackQuery, photo: str, caption: str, reply_markup) -> None:
    message = callback.message
    if message and getattr(message, "photo", None):
        try:
            await message.edit_media(
                media=InputMediaPhoto(media=photo, caption=caption, parse_mode="HTML"),
                reply_markup=reply_markup,
            )
            return
        except TelegramBadRequest:
            logger.warning("start_prize: edit_media failed user_id={}", callback.from_user.id)
    chat_id = callback.from_user.id
    if message:
        try:
            await message.delete()
        except TelegramBadRequest:
            pass
    await bot.send_photo(
        chat_id=chat_id,
        photo=photo,
        caption=caption,
        parse_mode="HTML",
        reply_markup=reply_markup,
    )


async def _show_buy_self(callback: CallbackQuery) -> None:
    message = callback.message
    if message:
        try:
            await message.delete()
        except TelegramBadRequest:
            pass
    await bot.send_message(
        chat_id=callback.from_user.id,
        text=lexicon["buy"],
        reply_markup=keyboard_buy_tiers(),
    )


async def _send_start_prize_later(user_id: int) -> None:
    try:
        await asyncio.sleep(_START_PRIZE_DELAY_SEC)
        await bot.send_photo(
            chat_id=user_id,
            photo=_START_PRIZE_WIN_PHOTO,
            caption=lexicon["start_prize_win"],
            parse_mode="HTML",
            reply_markup=keyboard_start_prize_reveal(),
        )
        logger.info("start_prize: отправлено user_id={}", user_id)
    except Exception:
        _prize_scheduled.discard(user_id)
        logger.exception("start_prize: не удалось отправить user_id={}", user_id)


async def _watch_claim_purchase(user_id: int) -> None:
    try:
        await asyncio.sleep(_CLAIM_WATCH_SEC)
        user_data = await sql.get_user(user_id)
        if _has_paid(user_data):
            pay_rows = await sql.get_user_subscription_payment_report(user_id)
            lines = [
                f"• {_fmt_pay_time(tc)} — {kind} — {days}"
                for tc, kind, days in pay_rows
            ]
            body = f"{user_id} купил подписку со стартого байта"
            if lines:
                body += "\n" + "\n".join(lines)
            else:
                body += "\nНет confirmed-платежей"
            await _notify_checker(body)
            return

        await bot.send_photo(
            chat_id=user_id,
            photo=_START_PRIZE_DISCOUNT_PHOTO,
            caption=lexicon["start_prize_hurry"],
            parse_mode="HTML",
            reply_markup=keyboard_start_prize_hurry(),
        )
        logger.info("start_prize: hurry-пуш user_id={}", user_id)
    except Exception:
        logger.exception("start_prize: ошибка проверки покупки user_id={}", user_id)
    finally:
        _claim_watchers.discard(user_id)


@router.callback_query(F.data == "start_prize_reveal")
async def start_prize_reveal(callback: CallbackQuery):
    uid = callback.from_user.id
    await callback.answer()
    if not is_casper_bot():
        return
    try:
        await _edit_prize_photo(
            callback,
            _START_PRIZE_DISCOUNT_PHOTO,
            lexicon["start_prize_reveal"],
            keyboard_start_prize_claim(),
        )
    except Exception:
        logger.exception("start_prize_reveal failed user_id={}", uid)
        return

    await _notify_checker(f"{uid} нажал Узнать свой приз")
    if uid not in _claim_watchers:
        _claim_watchers.add(uid)
        asyncio.create_task(_watch_claim_purchase(uid))


@router.callback_query(F.data == "start_prize_claim")
async def start_prize_claim(callback: CallbackQuery):
    await callback.answer()
    if not is_casper_bot():
        return
    try:
        await _show_buy_self(callback)
    except Exception:
        logger.exception("start_prize_claim edit failed user_id={}", callback.from_user.id)


@router.callback_query(F.data == "start_prize_hurry")
async def start_prize_hurry(callback: CallbackQuery):
    await callback.answer()
    if not is_casper_bot():
        return
    try:
        await _show_buy_self(callback)
    except Exception:
        logger.exception("start_prize_hurry failed user_id={}", callback.from_user.id)
