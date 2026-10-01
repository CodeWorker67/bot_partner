"""Меню: текст или фото баннера партнёра (если задано в панели)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape
from typing import Optional, Union

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    InaccessibleMessage,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from bot import bot, sql, x3
from config import BOT_ID
from config_bd.models import Users
from config_bd.partner_sql import pro_subscription_end_active
from logging_config import logger
from tariff_resolve import panel_username
from utils.custom_emoji import emojify

MAIN_MENU_REPLY_TEXT = (
    "Кнопка <b>Главное меню</b> внизу — нажмите её, чтобы в любой момент вернуться в главное меню."
)
MAIN_MENU_BUTTON_TEXT = "Главное меню"

PROFILE_TIERS: tuple[tuple[str, int, str, str], ...] = (
    ("3", 3, "3 устройства", "subscription_3_end_date"),
    ("main", 5, "5 устройств", "subscription_end_date"),
    ("10", 10, "10 устройств", "subscription_10_end_date"),
)

CONNECT_BTN_BY_SLOT = {
    "3": "🔗 Подключить VPN (3 устройства)",
    "main": "🔗 Подключить VPN (5 устройств)",
    "10": "🔗 Подключить VPN (10 устройств)",
}


def reply_keyboard_main_menu() -> ReplyKeyboardMarkup:
    from keyboard import STYLE_PRIMARY

    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=MAIN_MENU_BUTTON_TEXT, style=STYLE_PRIMARY)],
        ],
        resize_keyboard=True,
    )


async def send_main_menu_hint(message: Message) -> None:
    await message.answer(
        MAIN_MENU_REPLY_TEXT,
        parse_mode="HTML",
        reply_markup=reply_keyboard_main_menu(),
    )


def _aware_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _format_date_msk(dt: datetime) -> str:
    return (_aware_utc(dt) + timedelta(hours=3)).strftime("%d.%m.%Y")


def end_date_status_text(sub_end) -> str:
    if sub_end is None:
        return "Нет подписки"
    if pro_subscription_end_active(sub_end):
        return f"Активна до {_format_date_msk(sub_end)}"
    return f"Истекла {_format_date_msk(sub_end)}"


def _tier_end(user: Optional[Users], attr: str):
    if user is None:
        return None
    return getattr(user, attr, None)


def has_any_device_subscription(user: Optional[Users]) -> bool:
    if user is None:
        return False
    return any(_tier_end(user, attr) is not None for _slot, _n, _label, attr in PROFILE_TIERS)


def has_active_device_subscription(user: Optional[Users]) -> bool:
    if user is None:
        return False
    return any(
        pro_subscription_end_active(_tier_end(user, attr))
        for _slot, _n, _label, attr in PROFILE_TIERS
    )


def _username_for_slot(uid: int, slot: str) -> str:
    if slot == "3":
        return panel_username(uid, BOT_ID, device_slots=3)
    if slot == "10":
        return panel_username(uid, BOT_ID, device_slots=10)
    return panel_username(uid, BOT_ID, device_slots=5)


async def profile_caption(fullname: str, user: Optional[Users], uid: int) -> str:
    lines = [f"👤 {fullname}"]
    for slot, _n, label, attr in PROFILE_TIERS:
        sub_end = _tier_end(user, attr)
        status = end_date_status_text(sub_end)
        lines.append(f"📲 {label}: {status}")
        if pro_subscription_end_active(sub_end):
            username = _username_for_slot(uid, slot)
            sub_url = await x3.sublink(username)
            if sub_url:
                lines.append(f"<code>{escape(str(sub_url))}</code>")
    return "\n".join(lines)


async def send_menu_message(
    chat_id: int,
    caption: str,
    reply_markup: InlineKeyboardMarkup,
    *,
    disable_web_page_preview: bool = False,
) -> None:
    """Новое сообщение с баннером партнёра (если задан) или текстом."""
    caption = emojify(caption)
    photo_id = await _menu_photo_file_id()
    extra: dict = {"parse_mode": "HTML", "reply_markup": reply_markup}
    if disable_web_page_preview:
        extra["disable_web_page_preview"] = True
    if photo_id:
        await bot.send_photo(chat_id, photo=photo_id, caption=caption, **extra)
    else:
        await bot.send_message(chat_id, caption, **extra)


async def _menu_photo_file_id() -> Optional[str]:
    settings = await sql.get_bot_settings()
    if not settings:
        return None
    fid = settings.get("menu_photo_file_id")
    if fid and str(fid).strip():
        return str(fid).strip()
    return None


async def _replace_photo_message(
    chat_id: int,
    message_id: int,
    photo_file_id: str,
    caption: str,
    reply_markup: InlineKeyboardMarkup,
) -> None:
    try:
        await bot.delete_message(chat_id, message_id)
    except TelegramBadRequest:
        pass
    await bot.send_photo(
        chat_id,
        photo=photo_file_id,
        caption=caption,
        parse_mode="HTML",
        reply_markup=reply_markup,
    )


async def edit_or_send_menu(
    source: Union[Message, CallbackQuery],
    caption: str,
    reply_markup: InlineKeyboardMarkup,
) -> None:
    caption = emojify(caption)
    photo_id = await _menu_photo_file_id()

    if isinstance(source, CallbackQuery):
        message = source.message
        chat_id = message.chat.id
        message_id = message.message_id
    else:
        message = source
        chat_id = message.chat.id
        message_id = message.message_id

    if not photo_id:
        if isinstance(source, CallbackQuery):
            if isinstance(message, InaccessibleMessage):
                await bot.send_message(
                    chat_id, caption, parse_mode="HTML", reply_markup=reply_markup
                )
                return
            try:
                if message.photo:
                    try:
                        await bot.delete_message(chat_id, message_id)
                    except TelegramBadRequest:
                        pass
                    await bot.send_message(
                        chat_id, caption, parse_mode="HTML", reply_markup=reply_markup
                    )
                    return
                await message.edit_text(
                    caption, parse_mode="HTML", reply_markup=reply_markup
                )
            except TelegramBadRequest as e:
                logger.warning("menu edit_text failed chat_id={}: {}", chat_id, e)
                await bot.send_message(
                    chat_id, caption, parse_mode="HTML", reply_markup=reply_markup
                )
        else:
            await source.answer(caption, parse_mode="HTML", reply_markup=reply_markup)
        return

    if isinstance(message, InaccessibleMessage):
        await _replace_photo_message(
            chat_id, message_id, photo_id, caption, reply_markup
        )
        return

    if message.photo:
        try:
            await message.edit_media(
                media=InputMediaPhoto(
                    media=photo_id,
                    caption=caption,
                    parse_mode="HTML",
                ),
                reply_markup=reply_markup,
            )
            return
        except TelegramBadRequest as e:
            logger.warning("menu edit_media failed chat_id={}: {}", chat_id, e)
            await _replace_photo_message(
                chat_id, message_id, photo_id, caption, reply_markup
            )
            return

    await _replace_photo_message(chat_id, message_id, photo_id, caption, reply_markup)


async def _slot_sublink(username: str) -> Optional[str]:
    return await x3.sublink(username)


async def _active_connect_buttons(
    uid: int, user: Optional[Users]
) -> list[tuple[str, str]]:
    buttons: list[tuple[str, str]] = []
    seen: set[str] = set()
    if user is not None:
        for slot, _n, _label, attr in PROFILE_TIERS:
            if slot in seen or slot not in CONNECT_BTN_BY_SLOT:
                continue
            if not pro_subscription_end_active(_tier_end(user, attr)):
                continue
            username = _username_for_slot(uid, slot)
            url = await _slot_sublink(username)
            if url:
                buttons.append((CONNECT_BTN_BY_SLOT[slot], url))
                seen.add(slot)
    return buttons


async def show_main_menu(
    source: Union[Message, CallbackQuery],
    *,
    send_hint: bool = False,
) -> None:
    from keyboard import keyboard_start

    user = source.from_user
    user_obj = await sql.get_user_object_by_user_id(user.id)
    fullname = user.full_name or user.first_name or "Пользователь"
    caption = await profile_caption(fullname, user_obj, user.id)
    in_panel = bool(user_obj and user_obj.in_panel)
    active = has_active_device_subscription(user_obj)

    is_owner = await sql.can_access_partner_panel(user.id)
    show_create_partner_bot = await sql.is_partner_bot_creation_enabled()

    if send_hint and isinstance(source, Message):
        await send_main_menu_hint(source)

    connect_buttons = await _active_connect_buttons(user.id, user_obj)
    kb = keyboard_start(
        connect_buttons=connect_buttons,
        show_manage=has_any_device_subscription(user_obj) or bool(connect_buttons),
        buy_primary=not active,
        show_trial=not in_panel,
        show_connect_callback=active and not connect_buttons,
        show_owner_panel=is_owner,
        show_create_partner_bot=False,
    )

    photo_id = await _menu_photo_file_id()
    caption = emojify(caption)

    if isinstance(source, CallbackQuery):
        await edit_or_send_menu(source, caption, kb)
        return

    if photo_id:
        await source.answer_photo(
            photo=photo_id,
            caption=caption,
            parse_mode="HTML",
            reply_markup=kb,
        )
    else:
        await source.answer(caption, parse_mode="HTML", reply_markup=kb)
