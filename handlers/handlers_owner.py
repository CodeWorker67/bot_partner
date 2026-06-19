"""Панель партнёра (владелец бота)."""
import functools
import inspect

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot import bot, sql
from config import OWNER_TG_ID, PARTNER_MIN_WITHDRAW, PARTNER_SUPPORT_URL, TARIFF_KEYS, TRIAL_DAYS_MAX, TRIAL_DAYS_MIN, DEFAULT_PRICES
from keyboard import BTN_BACK, create_kb, keyboard_owner_main
from lexicon import lexicon
from logging_config import logger
from tariff_resolve import dct_desc

router = Router()


def _resolve_channel_input(raw: str) -> tuple[str, str] | None:
    """@username или t.me/... → (аргумент get_chat, URL для пользователей)."""
    raw = raw.strip()
    if not raw:
        return None

    if raw.startswith("@"):
        username = raw[1:].strip()
        if not username:
            return None
        return f"@{username}", f"https://t.me/{username}"

    lower = raw.lower()
    if "t.me/" not in lower:
        return None

    path = raw[lower.index("t.me/") + len("t.me/"):].rstrip("/")
    if not path:
        return None

    if path.startswith("+") or path.startswith("joinchat/"):
        url = f"https://t.me/{path}"
        return url, url

    username = path.split("/")[0]
    if not username:
        return None
    return f"@{username}", f"https://t.me/{username}"


class OwnerFSM(StatesGroup):
    broadcast_text = State()
    channel_input = State()
    price_key = State()
    price_value = State()
    trial_days = State()
    search_user = State()


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
    users = await sql.count_users()
    active = await sql.count_active_subscriptions()
    revenue = await sql.sum_revenue()
    trial = await sql.count_trial_users()
    paid = await sql.count_paid_users()
    conversion = f"{paid * 100 // trial}%" if trial else "—"
    online = await sql.get_latest_online()
    online_txt = f"{online.users_active}" if online else "—"
    text = lexicon["owner_stats"].format(users, active, revenue, online_txt, conversion)
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
        "📢 Отправьте @username канала или ссылку t.me/...\n"
        "Поддерживаются и приватные ссылки вида t.me/+...\n"
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
    raw = (message.text or "").strip()
    resolved = _resolve_channel_input(raw)
    if not resolved:
        await message.answer("❌ Неверный формат. Укажите @channel или ссылку.")
        return

    chat_lookup, channel_url = resolved
    try:
        chat = await bot.get_chat(chat_lookup)
    except Exception as e:
        logger.warning(f"owner_channel get_chat failed: {chat_lookup!r} — {e}")
        await message.answer(
            "❌ Не удалось получить канал. Проверьте ссылку и что бот добавлен в канал как администратор."
        )
        return
    channel_id = chat.id

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


@router.callback_query(F.data == "owner_users")
@_owner_only
async def owner_users(callback: CallbackQuery, state: FSMContext):
    await state.set_state(OwnerFSM.search_user)
    users = await sql.list_users(limit=10)
    lines = []
    for u in users:
        sub = "активна" if u.subscription_end_date or u.subscription_3_end_date or u.subscription_10_end_date else "нет"
        lines.append(f"<code>{u.user_id}</code> — {sub}")
    text = "👥 Последние пользователи:\n\n" + ("\n".join(lines) or "пусто")
    text += "\n\n🔍 Введите TG ID для поиска:"
    await callback.message.edit_text(text, reply_markup=create_kb(1, owner_panel=BTN_BACK))
    await callback.answer()


@router.message(OwnerFSM.search_user)
@_owner_only
async def owner_search_user(message: Message, state: FSMContext):
    await state.clear()
    try:
        tg_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Введите числовой TG ID.", reply_markup=keyboard_owner_main())
        return
    user = await sql.search_user_by_id(tg_id)
    if not user:
        await message.answer("Пользователь не найден.", reply_markup=keyboard_owner_main())
        return
    trial = "да" if user.field_bool_3 else "нет"
    paid = "да" if user.reserve_field else "нет"
    end = user.subscription_end_date or user.subscription_3_end_date or user.subscription_10_end_date
    await message.answer(
        f"👤 <b>{tg_id}</b>\n"
        f"Триал: {trial}\nПлатный: {paid}\n"
        f"Подписка до: {end or '—'}\n"
        f"В канале: {'да' if user.in_chanel else 'нет'}",
        reply_markup=keyboard_owner_main(),
    )


@router.callback_query(F.data == "owner_prices")
@_owner_only
async def owner_prices(callback: CallbackQuery, state: FSMContext):
    prices = await sql.get_prices()
    lines = [f"<code>{k}</code>: {prices.get(k, 0)} ₽ — {dct_desc.get(k, '')}" for k in TARIFF_KEYS]
    await state.set_state(OwnerFSM.price_key)
    await callback.message.edit_text(
        "💰 Текущие цены:\n\n" + "\n".join(lines) + "\n\nВведите ключ тарифа (например m1_d3):",
        reply_markup=create_kb(1, owner_panel=BTN_BACK),
    )
    await callback.answer()


@router.message(OwnerFSM.price_key)
@_owner_only
async def owner_price_key(message: Message, state: FSMContext):
    key = message.text.strip()
    if key not in TARIFF_KEYS:
        await message.answer("❌ Неизвестный тариф.", reply_markup=keyboard_owner_main())
        await state.clear()
        return
    await state.update_data(price_key=key)
    await state.set_state(OwnerFSM.price_value)
    default_p = DEFAULT_PRICES[key]
    await message.answer(
        f"Введите новую цену для {key} (₽).\n"
        f"Минимум — {default_p} ₽ (базовый тариф), можно только повышать."
    )


@router.message(OwnerFSM.price_value)
@_owner_only
async def owner_price_value(message: Message, state: FSMContext):
    data = await state.get_data()
    key = data.get("price_key")
    try:
        price = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Введите число.")
        return
    ok, err = await sql.set_price(key, price)
    await state.clear()
    await message.answer(
        f"✅ Цена {key} = {price} ₽" if ok else f"❌ {err}",
        reply_markup=keyboard_owner_main(),
    )


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
    balance = settings.get("partner_balance", 0) if settings else 0
    paid = settings.get("partner_pay", 0) if settings else 0
    text = lexicon["owner_balance"].format(balance, paid, PARTNER_MIN_WITHDRAW)
    if balance >= PARTNER_MIN_WITHDRAW:
        text += "\n\n" + lexicon["owner_withdraw_info"].format(balance, PARTNER_SUPPORT_URL)
    await callback.message.edit_text(
        text,
        reply_markup=create_kb(1, owner_panel=BTN_BACK),
    )
    await callback.answer()
