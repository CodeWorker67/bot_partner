from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from bot import sql
from config import ADMIN_IDS
from keyboard import BTN_BACK, create_kb
from lexicon import lexicon
from logging_config import logger

router = Router()

PAINT_PROMPTS = [
    ("total_users", "👥 Всего пользователей:"),
    ("visits_today", "• За сегодня:"),
    ("visits_week", "• За неделю:"),
    ("visits_month", "• За месяц:"),
    ("total_earned", "💎 Всего заработано:"),
    ("earned_bot", "⭐️ Заработано от платежей в боте:"),
    ("earned_partner", "💫 Заработано с партнёрских ботов:"),
    ("withdrawn", "💸 Выведено средств:"),
    ("balance", "📈 Текущий баланс:"),
    ("partner_since", "🗓 Партнёр с:"),
]


class PaintFSM(StatesGroup):
    waiting_value = State()


async def _partner_admin_stats_text(tg_id: int) -> str | None:
    user = await sql.get_user_object_by_user_id(tg_id)
    if user is None:
        return None
    if not user.partner_flag:
        return "not_partner"

    referrals = await sql.select_partner_count(tg_id)
    payments_sum = await sql.select_partner_referrals_payments_sum(tg_id)
    balance = user.partner_balance or 0
    paid_out = user.partner_pay or 0
    total_earned = balance + paid_out

    return (
        f"📊 <b>Статистика {tg_id}:</b>\n\n"
        f"👥 Друзей перешло (/start): <b>{referrals}</b>\n"
        f"💳 Приобретено подписок друзьями на: <b>{payments_sum} ₽</b>\n\n"
        f"💵 Заработок партнёра (всего): <b>{total_earned} ₽</b>\n"
        f"✅ Выведено: <b>{paid_out} ₽</b>\n"
        f"🏦 Осталось на вывод: <b>{balance} ₽</b>"
    )


@router.message(Command(commands=["paint"]))
async def paint_command(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return

    await state.clear()
    await state.set_state(PaintFSM.waiting_value)
    await state.update_data(paint_step=0, paint_values={})
    _, prompt = PAINT_PROMPTS[0]
    await message.answer(f"🎨 Режим /paint\n\nВведите значение для:\n{prompt}")


@router.message(PaintFSM.waiting_value)
async def paint_collect_value(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await state.clear()
        return

    value = (message.text or "").strip()
    if not value:
        await message.answer("❌ Отправьте текстовое значение.")
        return

    data = await state.get_data()
    step = int(data.get("paint_step", 0))
    values = dict(data.get("paint_values") or {})

    if step < 0 or step >= len(PAINT_PROMPTS):
        await state.clear()
        await message.answer("❌ Сессия /paint сброшена. Запустите команду снова.")
        return

    key, _ = PAINT_PROMPTS[step]
    values[key] = value
    next_step = step + 1

    if next_step >= len(PAINT_PROMPTS):
        await state.clear()
        text = lexicon["owner_stats"].format(
            values["total_users"],
            values["visits_today"],
            values["visits_week"],
            values["visits_month"],
            values["total_earned"],
            values["earned_bot"],
            values["earned_partner"],
            values["withdrawn"],
            values["balance"],
            values["partner_since"],
        )
        await message.answer(text, reply_markup=create_kb(1, owner_panel=BTN_BACK))
        return

    await state.update_data(paint_step=next_step, paint_values=values)
    _, prompt = PAINT_PROMPTS[next_step]
    await message.answer(f"Введите значение для:\n{prompt}")


@router.message(Command(commands=["partner"]))
async def partner_info_command(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    args = (message.text or "").split()
    if len(args) < 2:
        await message.answer(
            "❌ Использование: /partner <telegram_id>\nНапример: /partner 123456789"
        )
        return

    try:
        target_id = int(args[1].strip())
    except ValueError:
        await message.answer("❌ ID должен быть числом.")
        return

    try:
        text = await _partner_admin_stats_text(target_id)
    except Exception as e:
        logger.exception("/partner")
        await message.answer(f"❌ Ошибка: {e}")
        return

    if text is None:
        await message.answer(f"❌ Пользователь {target_id} не найден в базе данных.")
        return
    if text == "not_partner":
        await message.answer(
            f"❌ Пользователь {target_id} не участвует в партнёрской программе "
            f"(partner_flag = False)."
        )
        return

    await message.answer(text, parse_mode="HTML")


@router.message(Command(commands=["partner_remove"]))
async def partner_remove_command(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    args = (message.text or "").split()
    if len(args) < 3:
        await message.answer(
            "❌ Использование: /partner_remove <telegram_id> <сумма>\n"
            "Например: /partner_remove 123456789 500"
        )
        return

    try:
        target_id = int(args[1].strip())
        amount = int(args[2].strip())
    except ValueError:
        await message.answer("❌ ID и сумма должны быть целыми числами.")
        return

    ok, err = await sql.partner_record_payout(target_id, amount)
    if not ok:
        await message.answer(f"❌ {err}")
        return

    stats = await _partner_admin_stats_text(target_id)
    if stats and stats != "not_partner":
        await message.answer(
            f"✅ Списано <b>{amount} ₽</b> с баланса, добавлено в «Выведено».\n\n{stats}",
            parse_mode="HTML",
        )
    else:
        await message.answer(
            f"✅ Списано {amount} ₽ с баланса пользователя {target_id}, добавлено в partner_pay."
        )


@router.message(Command(commands=["pay_to_client"]))
async def pay_to_client_command(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    args = (message.text or "").split()
    if len(args) < 2:
        await message.answer(
            "❌ Использование: /pay_to_client <сумма>\n"
            "Например: /pay_to_client 3000\n"
            "Отрицательное число — коррекция (уменьшение partner_pay)."
        )
        return

    try:
        amount = int(args[1].strip())
    except ValueError:
        await message.answer("❌ Сумма должна быть целым числом.")
        return

    if amount == 0:
        await message.answer("❌ Сумма не может быть 0.")
        return

    ok, err = await sql.add_owner_partner_pay(amount)
    if not ok:
        await message.answer(f"❌ {err}")
        return

    settings = await sql.get_bot_settings() or {}
    total = settings.get("partner_balance", 0) or 0
    paid = settings.get("partner_pay", 0) or 0
    current = max(total - paid, 0)
    if amount > 0:
        action = f"К <b>partner_pay</b> добавлено <b>{amount} ₽</b>"
    else:
        action = f"Из <b>partner_pay</b> списано <b>{-amount} ₽</b> (коррекция)"
    await message.answer(
        f"✅ {action}.\n\n"
        f"Всего заработано: <b>{total} ₽</b>\n"
        f"Выведено: <b>{paid} ₽</b>\n"
        f"Текущий баланс: <b>{current} ₽</b>",
        parse_mode="HTML",
    )
