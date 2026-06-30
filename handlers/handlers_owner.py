"""Панель партнёра (владелец бота)."""
import functools
import inspect
from datetime import datetime, timezone, timedelta

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Chat, Message, MessageOriginChannel

from bot import bot, sql
from config import OWNER_TG_ID, PARTNER_MIN_WITHDRAW, PARTNER_SUPPORT_URL, TARIFF_KEYS, TRIAL_DAYS_MAX, TRIAL_DAYS_MIN, DEFAULT_PRICES
from config_bd.partner_sql import parse_user_profile, pro_subscription_end_active, user_has_active_pro_subscription
from keyboard import BTN_BACK, create_kb, keyboard_owner_balance, keyboard_owner_main, keyboard_owner_prices, keyboard_owner_users
from lexicon import lexicon
from logging_config import logger
from tariff_resolve import OWNER_PRICE_SHORT

router = Router()
OWNER_USERS_PAGE_SIZE = 8


def _forwarded_channel(message: Message) -> Chat | None:
    origin = message.forward_origin
    if isinstance(origin, MessageOriginChannel):
        return origin.chat
    chat = message.forward_from_chat
    if chat and chat.type in ("channel", "supergroup"):
        return chat
    return None


def _is_invite_link(raw: str) -> bool:
    lower = raw.lower()
    if "t.me/" not in lower:
        return False
    path = raw[lower.index("t.me/") + len("t.me/"):].rstrip("/")
    return path.startswith("+") or path.startswith("joinchat/")


def _parse_channel_chat_id(raw: str) -> int | None:
    cleaned = raw.strip().replace(" ", "")
    if not cleaned.lstrip("-").isdigit():
        return None
    chat_id = int(cleaned)
    if chat_id == 0:
        return None
    return chat_id


def _parse_channel_lookup(raw: str) -> str | int | None:
    """@username, t.me/username или chat_id → аргумент get_chat."""
    raw = raw.strip()
    if not raw:
        return None

    if _is_invite_link(raw):
        return None

    if raw.startswith("@"):
        username = raw[1:].strip()
        if not username:
            return None
        return f"@{username}"

    chat_id = _parse_channel_chat_id(raw)
    if chat_id is not None:
        return chat_id

    lower = raw.lower()
    if "t.me/" not in lower:
        return None

    path = raw[lower.index("t.me/") + len("t.me/"):].rstrip("/")
    if not path:
        return None

    username = path.split("/")[0]
    if not username:
        return None
    return f"@{username}"


async def _user_channel_url(chat: Chat) -> str:
    if chat.username:
        return f"https://t.me/{chat.username}"
    return await bot.export_chat_invite_link(chat.id)


async def _save_partner_channel(
    message: Message,
    state: FSMContext,
    channel_id: int,
    channel_url: str,
) -> None:
    me = await bot.get_me()
    member = await bot.get_chat_member(channel_id, me.id)
    if member.status not in ("administrator", "creator"):
        await message.answer("❌ Бот не является администратором канала.")
        return

    await sql.update_bot_settings(
        channel_id=channel_id,
        channel_url=channel_url,
        channel_required=True,
    )
    await state.clear()
    await message.answer(
        f"✅ Канал настроен: {channel_url}\nОбязательная подписка включена.",
        reply_markup=keyboard_owner_main(),
    )


class OwnerFSM(StatesGroup):
    broadcast_text = State()
    channel_input = State()
    price_value = State()
    trial_days = State()
    owner_user_search = State()


def _owner_only(handler):
    @functools.wraps(handler)
    async def wrapper(event, *args, **kwargs):
        uid = event.from_user.id
        if uid != OWNER_TG_ID:
            if isinstance(event, CallbackQuery):
                await event.answer("Нет доступа", show_alert=True)
            return
        sig = inspect.signature(handler)
        filtered = {k: v for k, v in kwargs.items() if k in sig.parameters}
        return await handler(event, *args, **filtered)

    return wrapper


def _owner_balance_fields(settings: dict | None) -> tuple[int, int, int, int, int]:
    total = int((settings or {}).get("partner_balance", 0) or 0)
    own = int((settings or {}).get("balance_own_bot", 0) or 0)
    child = int((settings or {}).get("balance_child_bots", 0) or 0)
    paid = int((settings or {}).get("partner_pay", 0) or 0)
    current = max(total - paid, 0)
    return total, own, child, paid, current


async def send_owner_menu(target: Message | CallbackQuery):
    text = lexicon["owner_panel_intro"]
    kb = keyboard_owner_main()
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=kb)
        await target.answer()
    else:
        await target.answer(text, reply_markup=kb)


@router.callback_query(F.data == "owner_panel")
@_owner_only
async def owner_panel_cb(callback: CallbackQuery):
    await send_owner_menu(callback)


@router.callback_query(F.data == "owner_stats")
@_owner_only
async def owner_stats(callback: CallbackQuery):
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = now - timedelta(days=7)
    month_start = now - timedelta(days=30)

    users = await sql.count_users()
    visits_today = await sql.count_bot_visits_since(today_start)
    visits_week = await sql.count_bot_visits_since(week_start)
    visits_month = await sql.count_bot_visits_since(month_start)

    settings = await sql.get_bot_settings()
    total, own, child, paid, current = _owner_balance_fields(settings)

    partner_since = (settings or {}).get("partner_since") if settings else None
    if partner_since:
        if partner_since.tzinfo is not None:
            partner_since = partner_since.astimezone(timezone.utc).replace(tzinfo=None)
        partner_since_txt = partner_since.strftime("%d.%m.%Y")
    else:
        partner_since_txt = "—"

    text = lexicon["owner_stats"].format(
        users, visits_today, visits_week, visits_month,
        total, own, child, paid, current, partner_since_txt,
    )
    await callback.message.edit_text(text, reply_markup=create_kb(1, owner_panel=BTN_BACK))
    await callback.answer()


@router.callback_query(F.data == "owner_broadcast")
@_owner_only
async def owner_broadcast_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(OwnerFSM.broadcast_text)
    await callback.message.edit_text(
        "✉️ Отправьте текст рассылки всем пользователям бота:",
        reply_markup=create_kb(1, owner_panel="❌ Отмена"),
    )
    await callback.answer()


@router.message(OwnerFSM.broadcast_text)
@_owner_only
async def owner_broadcast_send(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await send_owner_menu(message)
        return
    user_ids = await sql.get_all_user_ids_for_broadcast()
    sent = failed = 0
    for uid in user_ids:
        try:
            await bot.send_message(uid, message.text)
            sent += 1
        except Exception:
            failed += 1
    await state.clear()
    await message.answer(
        f"✅ Рассылка завершена.\nОтправлено: {sent}\nОшибок: {failed}",
        reply_markup=keyboard_owner_main(),
    )


@router.callback_query(F.data == "owner_channel")
@_owner_only
async def owner_channel_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(OwnerFSM.channel_input)
    await callback.message.edit_text(
        "📢 Настройка канала для обязательной подписки:\n\n"
        "• @username или https://t.me/username — публичный канал\n"
        "• chat_id (например -1001234567890) — публичный или приватный\n"
        "• перешлите пост из канала — если не знаете chat_id\n\n"
        "Ссылки-приглашения t.me/+... не подойдут.\n"
        "Бот должен быть администратором канала.",
        reply_markup=create_kb(1, owner_panel="❌ Отмена"),
    )
    await callback.answer()


@router.message(OwnerFSM.channel_input)
@_owner_only
async def owner_channel_save(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await send_owner_menu(message)
        return
    forwarded = _forwarded_channel(message)
    if forwarded:
        try:
            chat = await bot.get_chat(forwarded.id)
            channel_url = await _user_channel_url(chat)
        except Exception as e:
            logger.warning(f"owner_channel forward resolve failed: {forwarded.id!r} — {e}")
            await message.answer(
                "❌ Не удалось настроить канал. Проверьте, что бот — администратор "
                "и имеет право «Приглашать пользователей»."
            )
            return
        await _save_partner_channel(message, state, chat.id, channel_url)
        return

    raw = (message.text or "").strip()
    if _is_invite_link(raw):
        await message.answer(
            "❌ Инвайт-ссылки t.me/+... не подходят.\n"
            "Укажите chat_id, @username или перешлите пост из канала."
        )
        return

    lookup = _parse_channel_lookup(raw)
    if lookup is None:
        await message.answer(
            "❌ Неверный формат.\n"
            "Укажите @channel, chat_id, ссылку t.me/username или перешлите пост из канала."
        )
        return

    try:
        chat = await bot.get_chat(lookup)
        channel_url = await _user_channel_url(chat)
    except Exception as e:
        logger.warning(f"owner_channel get_chat failed: {lookup!r} — {e}")
        await message.answer(
            "❌ Не удалось получить канал. Проверьте chat_id/ссылку и что бот добавлен "
            "в канал как администратор."
        )
        return

    await _save_partner_channel(message, state, chat.id, channel_url)


async def _owner_user_button_label(user) -> str:
    profile = parse_user_profile(user.field_str_2)
    name = (profile.get("full_name") or "").strip()
    username = (profile.get("username") or "").strip()
    if not name and not username:
        try:
            chat = await bot.get_chat(user.user_id)
            name = (chat.full_name or "").strip()
            username = (chat.username or "").strip()
        except Exception:
            pass
    if name and username:
        return f"{name} (@{username})"
    if username:
        return f"@{username}"
    if name:
        return name
    return f"ID {user.user_id}"


def _parse_iso_dt(raw: str) -> datetime | None:
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _fmt_dt(dt: datetime) -> str:
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.strftime("%d.%m.%Y %H:%M")


def _relative_days_ru(dt: datetime) -> str:
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    days = (now.date() - dt.astimezone(timezone.utc).date()).days
    if days <= 0:
        return "сегодня"
    if days == 1:
        return "вчера"
    return f"{days} дн. назад"


def _last_activity_dt(user, profile: dict) -> datetime | None:
    raw = profile.get("last_activity")
    if raw:
        parsed = _parse_iso_dt(raw)
        if parsed:
            return parsed
    if user.last_broadcast_date:
        return user.last_broadcast_date
    return user.create_user


def _subscription_line(user) -> str:
    if not user_has_active_pro_subscription(user):
        return "Нет подписки"
    active_ends = [
        d for d in (
            user.subscription_end_date,
            user.subscription_3_end_date,
            user.subscription_10_end_date,
        )
        if d and pro_subscription_end_active(d)
    ]
    if not active_ends:
        return "Нет подписки"
    latest = max(active_ends)
    return f"Активна до {_fmt_dt(latest)}"


async def _resolve_user_profile(user) -> tuple[str, str | None, str]:
    profile = parse_user_profile(user.field_str_2)
    name = (profile.get("full_name") or "").strip()
    username = (profile.get("username") or "").strip()
    language = (profile.get("language") or "").strip()
    if not name and not username:
        try:
            chat = await bot.get_chat(user.user_id)
            name = (chat.full_name or "").strip()
            username = (chat.username or "").strip()
            if not language:
                language = (chat.language_code or "").strip()
        except Exception:
            pass
    return name or f"ID {user.user_id}", username or None, language or "—"


async def _format_owner_user_detail(user) -> str:
    name, username, language = await _resolve_user_profile(user)
    profile = parse_user_profile(user.field_str_2)
    uid = user.user_id
    status = "✅ Активен" if not user.is_delete else "🚫 Заблокирован"
    username_line = f'<a href="https://t.me/{username}">@{username}</a>' if username else "—"
    balance = user.ref_balance or 0
    tx_count = await sql.count_user_transactions(uid)
    reg_dt = user.create_user or datetime.now()
    if reg_dt.tzinfo is None:
        reg_dt = reg_dt.replace(tzinfo=timezone.utc)
    last_dt = _last_activity_dt(user, profile) or reg_dt
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=timezone.utc)
    days_since = (datetime.now(timezone.utc).date() - reg_dt.astimezone(timezone.utc).date()).days
    return (
        "💻 <b>Управление пользователем</b>\n\n"
        "<b>Основная информация</b>\n"
        f"• Имя: {name}\n"
        f'• ID: <a href="tg://user?id={uid}">{uid}</a>\n'
        f"• Username: {username_line}\n"
        f"• Статус: {status}\n"
        f"• Язык: {language}\n\n"
        "<b>Финансы</b>\n"
        f"• Баланс: {balance} ₽\n"
        f"• Транзакций: {tx_count}\n\n"
        "<b>Активность</b>\n"
        f"• Регистрация: {_fmt_dt(reg_dt)}\n"
        f"• Последняя активность: {_relative_days_ru(last_dt)}\n"
        f"• Дней с регистрации: {days_since}\n\n"
        "<b>Подписка</b>\n"
        f"• {_subscription_line(user)}"
    )


async def _show_owner_users_list(
    target: Message | CallbackQuery,
    state: FSMContext,
    *,
    page: int = 0,
    notice: str = "",
):
    await state.clear()
    total = await sql.count_users()
    total_pages = max(1, (total + OWNER_USERS_PAGE_SIZE - 1) // OWNER_USERS_PAGE_SIZE)
    page = min(max(0, page), total_pages - 1)
    await state.update_data(owner_users_page=page)
    users = await sql.list_users(offset=page * OWNER_USERS_PAGE_SIZE, limit=OWNER_USERS_PAGE_SIZE)
    buttons = []
    for user in users:
        buttons.append((user.user_id, await _owner_user_button_label(user)))
    text = notice + lexicon["owner_users_intro"].format(page + 1, total_pages)
    kb = keyboard_owner_users(buttons, page=page, total_pages=total_pages)
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=kb)
        await target.answer()
    else:
        await target.answer(text, reply_markup=kb)


async def _show_owner_user_detail(target: Message | CallbackQuery, user, *, page: int = 0):
    text = await _format_owner_user_detail(user)
    kb = create_kb(1, **{f"owner_users_page:{page}": "⬅️ Назад к списку"})
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=kb)
        await target.answer()
    else:
        await target.answer(text, reply_markup=kb)


@router.callback_query(F.data == "owner_users")
@_owner_only
async def owner_users(callback: CallbackQuery, state: FSMContext):
    await _show_owner_users_list(callback, state)


@router.callback_query(F.data.startswith("owner_users_page:"))
@_owner_only
async def owner_users_page(callback: CallbackQuery, state: FSMContext):
    page = int(callback.data.split(":", 1)[1])
    await _show_owner_users_list(callback, state, page=page)


@router.callback_query(F.data.startswith("owner_user_view:"))
@_owner_only
async def owner_user_view(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    page = int(data.get("owner_users_page", 0))
    try:
        user_id = int(callback.data.split(":", 1)[1])
    except ValueError:
        await callback.answer("Некорректный ID", show_alert=True)
        return
    user = await sql.search_user_by_id(user_id)
    if not user:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    await state.update_data(owner_users_page=page)
    await _show_owner_user_detail(callback, user, page=page)


@router.callback_query(F.data == "owner_users_search")
@_owner_only
async def owner_users_search_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(OwnerFSM.owner_user_search)
    await callback.message.edit_text(
        lexicon["owner_users_search"],
        reply_markup=create_kb(1, owner_users="❌ Отмена"),
    )
    await callback.answer()


@router.message(OwnerFSM.owner_user_search)
@_owner_only
async def owner_users_search_query(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    if not raw:
        await message.answer("❌ Введите Telegram ID или @username.")
        return

    user = None
    if raw.lstrip("@").isdigit():
        user = await sql.search_user_by_id(int(raw.lstrip("@")))
    else:
        user = await sql.search_user_by_username(raw)

    if not user:
        await message.answer(
            "❌ Пользователь не найден.",
            reply_markup=create_kb(1, owner_users="⬅️ К списку"),
        )
        return

    await state.clear()
    await _show_owner_user_detail(message, user)


async def _show_owner_prices(target: Message | CallbackQuery, state: FSMContext, *, notice: str = ""):
    await state.clear()
    prices = await sql.get_prices()
    overrides = await sql.get_price_overrides()
    text = notice + lexicon["owner_prices_intro"]
    kb = keyboard_owner_prices(prices, overrides)
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=kb)
        await target.answer()
    else:
        await target.answer(text, reply_markup=kb)


@router.callback_query(F.data == "owner_prices")
@_owner_only
async def owner_prices(callback: CallbackQuery, state: FSMContext):
    await _show_owner_prices(callback, state)


@router.callback_query(F.data.startswith("owner_price_edit:"))
@_owner_only
async def owner_price_edit(callback: CallbackQuery, state: FSMContext):
    key = callback.data.split(":", 1)[1]
    if key not in TARIFF_KEYS:
        await callback.answer("Неизвестный тариф", show_alert=True)
        return
    prices = await sql.get_prices()
    label = OWNER_PRICE_SHORT.get(key, key)
    base = DEFAULT_PRICES[key]
    current = prices[key]
    await state.set_state(OwnerFSM.price_value)
    await state.update_data(price_key=key)
    await callback.message.edit_text(
        lexicon["owner_price_edit"].format(label, base, current),
        reply_markup=create_kb(1, owner_prices="❌ Отмена"),
    )
    await callback.answer()


@router.message(OwnerFSM.price_value)
@_owner_only
async def owner_price_value(message: Message, state: FSMContext):
    data = await state.get_data()
    key = data.get("price_key")
    if key not in TARIFF_KEYS:
        await state.clear()
        await send_owner_menu(message)
        return

    raw = (message.text or "").strip()
    label = OWNER_PRICE_SHORT.get(key, key)

    if raw == "-":
        ok, err = await sql.reset_price(key)
        notice = f"✅ {label}: сброшено на базовую цену.\n\n" if ok else f"❌ {err}\n\n"
        await _show_owner_prices(message, state, notice=notice)
        return

    try:
        price = int(raw)
    except ValueError:
        await message.answer("❌ Введите целое число или «-» для сброса.")
        return

    ok, err = await sql.set_price(key, price)
    if not ok:
        await message.answer(f"❌ {err}")
        return

    notice = f"✅ {label}: {price} ₽\n\n"
    await _show_owner_prices(message, state, notice=notice)


@router.callback_query(F.data == "owner_trial")
@_owner_only
async def owner_trial(callback: CallbackQuery, state: FSMContext):
    settings = await sql.get_bot_settings()
    days = (settings or {}).get("trial_days", 3)
    await state.set_state(OwnerFSM.trial_days)
    await callback.message.edit_text(
        f"🎁 Текущий триал: <b>{days}</b> дн.\nВведите новое значение ({TRIAL_DAYS_MIN}–{TRIAL_DAYS_MAX}):",
        reply_markup=create_kb(1, owner_panel=BTN_BACK),
    )
    await callback.answer()


@router.message(OwnerFSM.trial_days)
@_owner_only
async def owner_trial_save(message: Message, state: FSMContext):
    try:
        days = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Введите число.")
        return
    ok, err = await sql.set_trial_days(days)
    await state.clear()
    await message.answer(
        f"✅ Триал: {days} дн." if ok else f"❌ {err}",
        reply_markup=keyboard_owner_main(),
    )


@router.callback_query(F.data == "owner_balance")
@_owner_only
async def owner_balance(callback: CallbackQuery):
    settings = await sql.get_bot_settings()
    total, own, child, paid, current = _owner_balance_fields(settings)
    text = lexicon["owner_balance"].format(total, own, child, paid, current, PARTNER_MIN_WITHDRAW)
    can_withdraw = current >= PARTNER_MIN_WITHDRAW
    if can_withdraw:
        text += "\n\n" + lexicon["owner_withdraw_hint"]
    await callback.message.edit_text(
        text,
        reply_markup=keyboard_owner_balance(show_withdraw=can_withdraw),
    )
    await callback.answer()


@router.callback_query(F.data == "owner_withdraw")
@_owner_only
async def owner_withdraw(callback: CallbackQuery):
    settings = await sql.get_bot_settings()
    _, _, _, _, current = _owner_balance_fields(settings)
    if current < PARTNER_MIN_WITHDRAW:
        await callback.answer(
            lexicon["owner_withdraw_alert"].format(PARTNER_MIN_WITHDRAW),
            show_alert=True,
        )
        return
    await callback.answer()
    await callback.message.edit_text(
        lexicon["owner_withdraw_info"].format(current, PARTNER_SUPPORT_URL),
        reply_markup=keyboard_owner_balance(show_withdraw=True),
    )
