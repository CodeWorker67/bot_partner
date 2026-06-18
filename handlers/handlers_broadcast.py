import asyncio
import urllib.parse

from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    ContentType,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import sql
from botapi_sender import send_message
from config import ADMIN_IDS, BOT_URL, CHECKER_ID
from keyboard import (
    BTN_BACK,
    STYLE_DANGER,
    STYLE_PRIMARY,
    STYLE_SUCCESS,
    create_kb,
    keyboard_buy_device_tier,
    keyboard_start,
)
from logging_config import logger
from telegram_ids import is_telegram_chat_id

router = Router()

# ~20 сообщений/с на пользователя (1 API-вызов). В режиме pin — 3 вызова с паузами между шагами.
_BROADCAST_USER_DELAY = 0.05
_BROADCAST_PIN_STEP_DELAY = 0.04
_BROADCAST_PIN_USER_DELAY = 0.05

CB_CAT = "bcat:"
CB_AUD = "baud:"
CB_KB = "bktyp:"
CB_CONF = "bcf:"
CB_PIN = "bcpin:"
BCBTN = "bcbtn:"
BCACT = "bcact:"
BCST = "bcst:"

LINK_STYLE_LABELS = {
    "primary": "Основной (синий)",
    "success": "Зелёный",
    "danger": "Красный",
    "none": "Без цвета",
}

CATEGORY_LABELS = {
    "not_connected_subscribe_yes": "не подключены, подписка активна",
    "not_connected_subscribe_off": "не подключены, подписка неактивна",
    "connected_subscribe_off": "подключены, подписка неактивна",
    "connected_subscribe_yes": "подключены, подписка активна",
    "not_subscribed": "без подписки в панели",
    "connected_never_paid": "подключены, никогда не платили",
    "subscribed_all": "есть подписка в панели (с датой окончания)",
    "all_users": "все пользователи",
}

SCOPE_LABEL = {
    False: "всем без исключения",
    True: "только тем, кому сегодня не было отправки",
}

# callback_data и подписи как в keyboard.py (главное меню и тарифы)
CUSTOM_PRESETS = [
    ("free_vpn", "🔥 Попробовать бесплатно (legacy)", STYLE_SUCCESS),
    ("buy_vpn", "🛒 Купить подписку", STYLE_SUCCESS),
    ("connect_vpn", "🔗 Подключить ВПН", STYLE_PRIMARY),
    ("ref", "👥 Рефералка", STYLE_PRIMARY),
    ("buy_gift", "🎁 Подарить подписку", STYLE_SUCCESS),
    ("ref_invite", "Пригласить друзей🫶", STYLE_SUCCESS),
    ("buy_tier_3", "🔹 Тарифы на 3️⃣ устройства", STYLE_PRIMARY),
    ("buy_tier_5", "🔸 Тарифы на 5️⃣ устройств", STYLE_PRIMARY),
    ("buy_tier_10", "🏆 Тарифы на 🔟 устройств", STYLE_SUCCESS),
]


def _ref_invite_url(user_id: int) -> str:
    base = BOT_URL or ""
    return (
        f"https://t.me/share/url?url={base}?start=ref{user_id}"
        f"&text={urllib.parse.quote('Вот ссылка на быстрый ВПН для своих!')}"
    )


def _back_markup() -> InlineKeyboardMarkup:
    return create_kb(1, broadcast_cancel=BTN_BACK)


def _category_markup() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for key, label in CATEGORY_LABELS.items():
        b.button(text=label[:64], callback_data=f"{CB_CAT}{key}")
    b.adjust(1)
    b.row(InlineKeyboardButton(text=BTN_BACK, callback_data="broadcast_cancel"))
    return b.as_markup()


def _audience_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Всем", callback_data=f"{CB_AUD}all"),
                InlineKeyboardButton(
                    text="Не отсылать сегодняшним",
                    callback_data=f"{CB_AUD}skip_today",
                ),
            ],
            [InlineKeyboardButton(text=BTN_BACK, callback_data="broadcast_cancel")],
        ]
    )


def _keyboard_type_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Клавиатура выбора тарифа", callback_data=f"{CB_KB}tariff")],
            [InlineKeyboardButton(text="Клавиатура стартовая", callback_data=f"{CB_KB}start")],
            [InlineKeyboardButton(text="Без клавиатуры", callback_data=f"{CB_KB}none")],
            [InlineKeyboardButton(text="Кастомные кнопки", callback_data=f"{CB_KB}custom")],
            [InlineKeyboardButton(text=BTN_BACK, callback_data="broadcast_cancel")],
        ]
    )


def _custom_presets_markup() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for cb_id, text, _st in CUSTOM_PRESETS:
        b.button(text=text[:64], callback_data=f"{BCBTN}{cb_id}")
    b.adjust(2)
    b.row(InlineKeyboardButton(text="Кнопка-ссылка", callback_data=f"{BCACT}link"))
    b.row(
        InlineKeyboardButton(
            text="Подарочная кнопка",
            callback_data=f"{BCACT}tgift",
        ),
    )
    b.row(
        InlineKeyboardButton(
            text="Завершить формирование клавиатуры",
            callback_data=f"{BCACT}done",
        ),
    )
    b.row(InlineKeyboardButton(text=BTN_BACK, callback_data="broadcast_cancel"))
    return b.as_markup()


def _pin_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Да", callback_data=f"{CB_PIN}y"),
                InlineKeyboardButton(text="Нет", callback_data=f"{CB_PIN}n"),
            ],
        ]
    )


def _confirm_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Да", callback_data=f"{CB_CONF}y"),
                InlineKeyboardButton(text="Нет", callback_data=f"{CB_CONF}n"),
            ],
        ]
    )


def _link_style_choice_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=LINK_STYLE_LABELS["primary"],
                    callback_data=f"{BCST}primary",
                    style=STYLE_PRIMARY,
                ),
            ],
            [
                InlineKeyboardButton(
                    text=LINK_STYLE_LABELS["success"],
                    callback_data=f"{BCST}success",
                    style=STYLE_SUCCESS,
                ),
            ],
            [
                InlineKeyboardButton(
                    text=LINK_STYLE_LABELS["danger"],
                    callback_data=f"{BCST}danger",
                    style=STYLE_DANGER,
                ),
            ],
            [InlineKeyboardButton(text=LINK_STYLE_LABELS["none"], callback_data=f"{BCST}none")],
            [InlineKeyboardButton(text="Отмена добавления кнопки", callback_data=f"{BCACT}lcancel")],
        ]
    )


def _format_kb_spec_lines(spec: list) -> str:
    lines = []
    for i, entry in enumerate(spec, start=1):
        if entry["kind"] == "cb":
            cb = entry["cb"]
            if isinstance(cb, str) and cb.startswith("trial_gift_"):
                lines.append(f"{i}. {entry['text']} (подарок trial: {cb})")
            else:
                lines.append(f"{i}. {entry['text']} (callback: {cb})")
        else:
            st = entry.get("style")
            if st == STYLE_PRIMARY:
                color = "основной"
            elif st == STYLE_SUCCESS:
                color = "зелёный"
            elif st == STYLE_DANGER:
                color = "красный"
            else:
                color = "без цвета"
            lines.append(f"{i}. {entry['text']} (ссылка, цвет: {color})")
    return "\n".join(lines) if lines else "(пусто)"


def _append_preset(spec: list, preset_id: str) -> None:
    for cb_id, text, style in CUSTOM_PRESETS:
        if cb_id == preset_id:
            if preset_id == "ref_invite":
                spec.append(
                    {
                        "kind": "url",
                        "text": text,
                        "ref_invite": True,
                        "style": style,
                    }
                )
            else:
                spec.append({"kind": "cb", "cb": cb_id, "text": text, "style": style})
            return


def _build_custom_reply_markup(spec: list, target_user_id: int) -> InlineKeyboardMarkup | None:
    if not spec:
        return None
    rows: list[list[InlineKeyboardButton]] = []
    for entry in spec:
        st = entry.get("style")
        if entry["kind"] == "cb":
            btn = InlineKeyboardButton(
                text=entry["text"],
                callback_data=entry["cb"],
                style=st,
            )
            rows.append([btn])
        else:
            if entry.get("ref_invite"):
                url = _ref_invite_url(target_user_id)
            else:
                url = str(entry["url"]).replace("{user_id}", str(target_user_id))
            btn = InlineKeyboardButton(text=entry["text"], url=url, style=st)
            rows.append([btn])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _resolve_reply_markup(
    keyboard_mode: str,
    custom_spec: list | None,
    target_user_id: int,
) -> InlineKeyboardMarkup | None:
    if keyboard_mode == "none":
        return None
    if keyboard_mode == "tariff":
        return keyboard_buy_device_tier()
    if keyboard_mode == "start":
        return keyboard_start()
    if keyboard_mode == "custom":
        return _build_custom_reply_markup(custom_spec or [], target_user_id)
    return None


def _broadcast_state_active(state_name: str | None) -> bool:
    return bool(state_name and state_name.startswith("BroadcastState"))


class BroadcastState(StatesGroup):
    waiting_for_message = State()
    waiting_for_category = State()
    waiting_for_audience = State()
    waiting_for_keyboard = State()
    custom_kb = State()
    custom_gift_trial_text = State()
    custom_gift_trial_days = State()
    custom_link_text = State()
    custom_link_url = State()
    custom_link_style = State()
    waiting_for_pin = State()
    confirm_send = State()


@router.message(Command(commands=["broadcast"]))
async def broadcast_start(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("Эта команда доступна только администраторам.")
        return
    await state.clear()
    await message.answer(
        f"Отправьте сообщение для рассылки или нажмите «{BTN_BACK}» для отмены.",
        reply_markup=_back_markup(),
    )
    await state.set_state(BroadcastState.waiting_for_message)


@router.message(BroadcastState.waiting_for_message)
async def broadcast_waiting_for_message(message: Message, state: FSMContext):
    if message.content_type not in [
        ContentType.TEXT,
        ContentType.PHOTO,
        ContentType.VIDEO,
        ContentType.DOCUMENT,
        ContentType.VOICE,
        ContentType.AUDIO,
        ContentType.ANIMATION,
        ContentType.STICKER,
    ]:
        await message.answer("Этот тип контента не поддерживается для рассылки.")
        return

    await state.update_data(
        broadcast_message_id=message.message_id,
        broadcast_chat_id=message.chat.id,
        broadcast_content_type=message.content_type,
    )

    await message.answer(
        "Выберите категорию получателей:",
        reply_markup=_category_markup(),
    )
    await state.set_state(BroadcastState.waiting_for_category)


@router.callback_query(F.data.startswith(CB_CAT), StateFilter(BroadcastState.waiting_for_category))
async def broadcast_pick_category(callback: CallbackQuery, state: FSMContext):
    category = callback.data[len(CB_CAT) :]
    if category not in CATEGORY_LABELS:
        await callback.answer("Неизвестная категория.", show_alert=True)
        return
    await state.update_data(category=category)
    await callback.answer()
    await callback.message.answer(
        "Отослать всем или только тем, кому сегодня ещё не отправляли рассылку?",
        reply_markup=_audience_markup(),
    )
    await state.set_state(BroadcastState.waiting_for_audience)


@router.callback_query(F.data.startswith(CB_AUD), StateFilter(BroadcastState.waiting_for_audience))
async def broadcast_pick_audience(callback: CallbackQuery, state: FSMContext):
    tail = callback.data[len(CB_AUD) :]
    if tail == "all":
        exclude_today = False
    elif tail == "skip_today":
        exclude_today = True
    else:
        await callback.answer("Неверный выбор.", show_alert=True)
        return
    await state.update_data(exclude_today_broadcast=exclude_today)
    await callback.answer()
    await callback.message.answer(
        "Выберите клавиатуру под сообщением:",
        reply_markup=_keyboard_type_markup(),
    )
    await state.set_state(BroadcastState.waiting_for_keyboard)


@router.callback_query(F.data.startswith(CB_KB), StateFilter(BroadcastState.waiting_for_keyboard))
async def broadcast_pick_keyboard(callback: CallbackQuery, state: FSMContext, bot: Bot):
    mode = callback.data[len(CB_KB) :]
    if mode not in ("none", "tariff", "start", "custom"):
        await callback.answer("Неверный выбор.", show_alert=True)
        return
    await callback.answer()
    if mode == "custom":
        await state.update_data(keyboard_mode="custom", custom_kb_spec=[])
        await callback.message.answer(
            "Добавьте кнопку — ниже список вариантов.\n"
            "Можно добавить «Кнопка-ссылка» (текст и URL), «Подарочная кнопка» (триал в панели) "
            "или завершить формирование.",
            reply_markup=_custom_presets_markup(),
        )
        await state.set_state(BroadcastState.custom_kb)
        return

    await state.update_data(keyboard_mode=mode, custom_kb_spec=[])
    await _ask_pin_message(callback.message, state)


@router.callback_query(F.data.startswith(BCBTN), StateFilter(BroadcastState.custom_kb))
async def broadcast_custom_add_preset(callback: CallbackQuery, state: FSMContext):
    pid = callback.data[len(BCBTN) :]
    if not any(pid == x[0] for x in CUSTOM_PRESETS):
        await callback.answer("Неизвестная кнопка.", show_alert=True)
        return
    data = await state.get_data()
    spec = list(data.get("custom_kb_spec") or [])
    _append_preset(spec, pid)
    await state.update_data(custom_kb_spec=spec)
    await callback.answer()
    await callback.message.answer(
        f"Кнопка добавлена. Ваша клавиатура:\n{_format_kb_spec_lines(spec)}",
        reply_markup=_custom_presets_markup(),
    )


@router.callback_query(F.data == f"{BCACT}link", StateFilter(BroadcastState.custom_kb))
async def broadcast_custom_link_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.answer("Введите текст кнопки-ссылки одним сообщением:")
    await state.set_state(BroadcastState.custom_link_text)


@router.callback_query(F.data == f"{BCACT}tgift", StateFilter(BroadcastState.custom_kb))
async def broadcast_custom_trial_gift_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.answer(
        "Введите текст кнопки «подарка» одним сообщением (как она отобразится в рассылке):",
    )
    await state.set_state(BroadcastState.custom_gift_trial_text)


@router.message(BroadcastState.custom_gift_trial_text)
async def broadcast_custom_trial_gift_text(message: Message, state: FSMContext):
    if not message.text or not message.text.strip():
        await message.answer("Нужен непустой текст кнопки.")
        return
    await state.update_data(trial_gift_btn_text=message.text.strip()[:64])
    await message.answer("Введите количество дней триала (целое число, например: 7):")
    await state.set_state(BroadcastState.custom_gift_trial_days)


@router.message(BroadcastState.custom_gift_trial_days)
async def broadcast_custom_trial_gift_days(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    if not raw.isdigit() or int(raw) < 1 or int(raw) > 999:
        await message.answer("Нужно целое число дней от 1 до 999.")
        return
    days = int(raw)
    data = await state.get_data()
    text = (data.get("trial_gift_btn_text") or "").strip()
    if not text:
        await message.answer("Сессия устарела. Начните «Подарочная кнопка» снова.")
        await state.set_state(BroadcastState.custom_kb)
        return
    spec = list(data.get("custom_kb_spec") or [])
    spec.append(
        {
            "kind": "cb",
            "cb": f"trial_gift_{days}",
            "text": text,
            "style": STYLE_SUCCESS,
        }
    )
    await state.update_data(
        custom_kb_spec=spec,
        trial_gift_btn_text=None,
    )
    await message.answer(
        f"Кнопка добавлена. Ваша клавиатура:\n{_format_kb_spec_lines(spec)}",
        reply_markup=_custom_presets_markup(),
    )
    await state.set_state(BroadcastState.custom_kb)


@router.message(BroadcastState.custom_link_text)
async def broadcast_custom_link_text(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("Нужен текстовый заголовок кнопки.")
        return
    await state.update_data(link_btn_text=message.text.strip())
    await message.answer("Введите URL кнопки (https://...):")
    await state.set_state(BroadcastState.custom_link_url)


@router.message(BroadcastState.custom_link_url)
async def broadcast_custom_link_url(message: Message, state: FSMContext):
    if not message.text or not message.text.strip().lower().startswith(("http://", "https://")):
        await message.answer("Нужен корректный URL, начинающийся с http:// или https://")
        return
    await state.update_data(link_btn_url=message.text.strip())
    await message.answer(
        "Выберите цвет кнопки (акцент в клиентах Telegram):",
        reply_markup=_link_style_choice_markup(),
    )
    await state.set_state(BroadcastState.custom_link_style)


@router.callback_query(F.data == f"{BCACT}lcancel", StateFilter(BroadcastState.custom_link_style))
async def broadcast_custom_link_cancel(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(link_btn_text=None, link_btn_url=None)
    await callback.message.answer(
        "Добавление кнопки-ссылки отменено.\nДобавьте кнопку или завершите формирование:",
        reply_markup=_custom_presets_markup(),
    )
    await state.set_state(BroadcastState.custom_kb)


@router.callback_query(F.data.startswith(BCST), StateFilter(BroadcastState.custom_link_style))
async def broadcast_custom_link_pick_style(callback: CallbackQuery, state: FSMContext):
    key = callback.data[len(BCST) :]
    style_map = {
        "primary": STYLE_PRIMARY,
        "success": STYLE_SUCCESS,
        "danger": STYLE_DANGER,
        "none": None,
    }
    if key not in style_map:
        await callback.answer("Неизвестный вариант.", show_alert=True)
        return
    data = await state.get_data()
    text = (data.get("link_btn_text") or "").strip()
    url = (data.get("link_btn_url") or "").strip()
    if not text or not url:
        await callback.answer("Сессия устарела. Начните кнопку-ссылку снова.", show_alert=True)
        await state.set_state(BroadcastState.custom_kb)
        return
    spec = list(data.get("custom_kb_spec") or [])
    spec.append(
        {
            "kind": "url",
            "text": text,
            "url": url,
            "ref_invite": False,
            "style": style_map[key],
        }
    )
    await state.update_data(custom_kb_spec=spec, link_btn_text=None, link_btn_url=None)
    await callback.answer()
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.message.answer(
        f"Кнопка добавлена. Ваша клавиатура:\n{_format_kb_spec_lines(spec)}",
        reply_markup=_custom_presets_markup(),
    )
    await state.set_state(BroadcastState.custom_kb)


@router.callback_query(F.data == f"{BCACT}done", StateFilter(BroadcastState.custom_kb))
async def broadcast_custom_done(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await _ask_pin_message(callback.message, state)


async def _ask_pin_message(message: Message, state: FSMContext):
    await message.answer(
        "Прикрепить сообщение в чате у каждого получателя?",
        reply_markup=_pin_markup(),
    )
    await state.set_state(BroadcastState.waiting_for_pin)


@router.callback_query(F.data.startswith(CB_PIN), StateFilter(BroadcastState.waiting_for_pin))
async def broadcast_pick_pin(callback: CallbackQuery, state: FSMContext, bot: Bot):
    tail = callback.data[len(CB_PIN) :]
    if tail == "y":
        pin_message = True
    elif tail == "n":
        pin_message = False
    else:
        await callback.answer("Неверный выбор.", show_alert=True)
        return
    await state.update_data(pin_message=pin_message)
    await callback.answer()
    await _send_preview_and_confirm(callback.message, state, bot)


async def _send_preview_and_confirm(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    category = data.get("category")
    exclude_today = bool(data.get("exclude_today_broadcast"))
    keyboard_mode = data.get("keyboard_mode")
    custom_spec = data.get("custom_kb_spec") or []
    b_mid = data.get("broadcast_message_id")
    b_cid = data.get("broadcast_chat_id")

    if not category or keyboard_mode is None or not b_mid or not b_cid:
        await message.answer("Ошибка состояния рассылки. Начните сначала с /broadcast")
        await state.clear()
        return

    n = await sql.count_users_for_broadcast(category, exclude_today)
    if n == 0:
        await message.answer("Нет пользователей по выбранным условиям.")
        await state.clear()
        return

    preview_uid = message.chat.id
    markup = _resolve_reply_markup(keyboard_mode, custom_spec, preview_uid)
    try:
        await bot.copy_message(
            chat_id=message.chat.id,
            from_chat_id=b_cid,
            message_id=b_mid,
            reply_markup=markup,
        )
    except Exception as e:
        logger.error(f"Broadcast preview copy failed: {e}")
        await message.answer(f"Не удалось показать превью: {e}")
        await state.clear()
        return

    cat_label = CATEGORY_LABELS.get(category, category)
    scope = SCOPE_LABEL[exclude_today]
    pin_label = (
        "с прикреплением в чате"
        if data.get("pin_message")
        else "без прикрепления"
    )
    await message.answer(
        f"Подтвердить отправку {n} пользователям в категории «{cat_label}», "
        f"{scope}, {pin_label}?",
        reply_markup=_confirm_markup(),
    )
    await state.set_state(BroadcastState.confirm_send)


@router.callback_query(F.data == f"{CB_CONF}n", StateFilter(BroadcastState.confirm_send))
async def broadcast_confirm_no(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    try:
        await callback.message.edit_text("Отправка отменена, состояние сброшено.")
    except Exception:
        await callback.message.answer("Отправка отменена, состояние сброшено.")


@router.callback_query(F.data == f"{CB_CONF}y", StateFilter(BroadcastState.confirm_send))
async def broadcast_confirm_yes(callback: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    b_mid = data.get("broadcast_message_id")
    b_cid = data.get("broadcast_chat_id")
    b_ct = data.get("broadcast_content_type")
    category = data.get("category")
    exclude_today = bool(data.get("exclude_today_broadcast"))
    keyboard_mode = data.get("keyboard_mode")
    custom_spec = data.get("custom_kb_spec") or []
    pin_message = bool(data.get("pin_message"))

    if not b_mid or not b_cid or not b_ct or not category:
        await callback.answer("Ошибка данных.", show_alert=True)
        await state.clear()
        return

    user_ids = await sql.select_user_ids_for_broadcast(category, exclude_today)
    if not user_ids:
        await callback.answer("Список пуст.", show_alert=True)
        await state.clear()
        try:
            await callback.message.edit_text("Нет получателей.")
        except Exception:
            pass
        return

    if CHECKER_ID is not None:
        user_ids = [*user_ids, CHECKER_ID]

    await callback.answer("Запуск рассылки…")
    try:
        await callback.message.edit_text("Рассылка выполняется…")
    except Exception:
        pass

    admin_chat_id = callback.message.chat.id
    count = 0
    for uid in user_ids:
        if not is_telegram_chat_id(uid):
            continue
        markup = _resolve_reply_markup(keyboard_mode, custom_spec, uid)
        try:
            if pin_message:
                try:
                    await bot.unpin_all_chat_messages(chat_id=uid)
                except Exception as unpin_err:
                    logger.warning(f"Broadcast: unpin failed for {uid}: {unpin_err}")
                await asyncio.sleep(_BROADCAST_PIN_STEP_DELAY)
                sent = await bot.copy_message(
                    chat_id=uid,
                    from_chat_id=b_cid,
                    message_id=b_mid,
                    reply_markup=markup,
                )
                await asyncio.sleep(_BROADCAST_PIN_STEP_DELAY)
                try:
                    await bot.pin_chat_message(
                        chat_id=uid,
                        message_id=sent.message_id,
                        disable_notification=True,
                    )
                except Exception as pin_err:
                    logger.warning(f"Broadcast: pin failed for {uid}: {pin_err}")
                await asyncio.sleep(_BROADCAST_PIN_USER_DELAY)
            else:
                await bot.copy_message(
                    chat_id=uid,
                    from_chat_id=b_cid,
                    message_id=b_mid,
                    reply_markup=markup,
                )
                await asyncio.sleep(_BROADCAST_USER_DELAY)
            await sql.update_broadcast_status(uid, "sent")
            count += 1
            if count % 1000 == 0:
                try:
                    await bot.send_message(
                        admin_chat_id,
                        f"Отослано {count} пользователям в процессе рассылки",
                    )
                except Exception as notify_err:
                    logger.warning(f"Broadcast: не удалось отправить прогресс админу: {notify_err}")
        except Exception as e:
            await sql.update_broadcast_status(uid, "failed")
            await sql.update_delete(uid, True)
            logger.error(f"Failed to send message to {uid}: {e}")

    logger.success(f"Send broadcast to {count} users")
    await bot.send_message(admin_chat_id, f"Сообщение успешно отправлено {count} пользователям.")
    await state.clear()


@router.callback_query(F.data == "broadcast_cancel")
async def cancel_broadcast(callback: CallbackQuery, state: FSMContext):
    st = await state.get_state()
    if not _broadcast_state_active(st):
        await callback.answer("Нечего отменять.")
        return
    await state.clear()
    await callback.answer("Отменено.")
    try:
        await callback.message.edit_text("Рассылка отменена.")
    except Exception:
        try:
            await callback.message.delete()
        except Exception:
            await callback.message.answer("Рассылка отменена.")


@router.message(Command("send_bot_api"))
async def admin_broadcast(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    users = await sql.select_connected_subscribe_yes()
    total = len(users)

    await message.answer(f"🚀 Начинаю рассылку для {total} пользователей...")

    success = 0
    blocked_updated = 0
    other_errors = 0
    text = """
🔥<b> Хорошие новости: Happ работает стабильно!</b>

Если у вас бывают обрывы связи — не терпите. Просто смените приложение на <b>Happ</b> или сразу напишите нам в <a href="https://t.me/suppzoomvpn">Поддержку</a>. Мы всё починим 🤝

📱 <b>Пользуетесь и всё нравится?</b>
Поделитесь с теми, кому нужен надёжный и быстрый ВПН для своих — пусть тоже подключатся 😉
        """
    button_text = "Пригласить друзей🫶"
    if CHECKER_ID is not None:
        url = f"https://t.me/share/url?url=https://t.me/zoomerskyvpn_bot?start=ref{CHECKER_ID}&text={urllib.parse.quote('Держи ссылку на ВПН для своих — быстро и только для своих!')}"
        send_message(chat_id=CHECKER_ID, text=text, button_text=button_text, url=url)
    for user_id in users:
        try:
            url = f"https://t.me/share/url?url=https://t.me/zoomerskyvpn_bot?start=ref{user_id}&text={urllib.parse.quote('Держи ссылку на ВПН для своих — быстро и только для своих!')}"
            response = send_message(chat_id=user_id, text=text, button_text=button_text, url=url)

            if not response.get("ok") and response.get("error_code") == 403:
                blocked_updated += 1
            elif response.get("ok"):
                success += 1
            else:
                other_errors += 1

        except Exception as e:
            error_text = str(e)
            if "403" in error_text or "blocked by the user" in error_text:
                blocked_updated += 1
            else:
                other_errors += 1
                logger.error(f"Ошибка для {user_id}: {e}")

        await asyncio.sleep(0.1)

    await message.answer(
        f"✅ Рассылка завершена.\n"
        f"📨 Успешно отправлено: {success}\n"
        f"🔒 Заблокировали бота (is_delete = False): {blocked_updated}\n"
        f"⚠️ Другие ошибки: {other_errors}"
    )
