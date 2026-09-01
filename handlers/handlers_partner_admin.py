from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from bot import bot, sql, x3
from config import ADMIN_IDS, BOT_ID
from keyboard import BTN_BACK, create_kb
from lexicon import lexicon
from logging_config import logger
from tariff_resolve import panel_username
from wl_traffic.service import (
    any_pro_user_on_limited_squad,
    fetch_all_pro_panel_users,
    get_wl_used_gb_for_user,
    reassign_all_pro_to_active,
)

router = Router()


@router.message(F.photo, F.from_user.id.in_(ADMIN_IDS))
async def get_photo(message: Message):
    await message.answer(f"<code>{message.photo[-1].file_id}</code>", parse_mode="HTML")


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


def _pay_dt_str(dt) -> str:
    if dt is None:
        return "—"
    try:
        return dt.strftime("%d.%m.%Y %H:%M")
    except Exception:
        return str(dt)


def _pay_panel_sub_line(ar: dict) -> str:
    t = ar.get("time", "-")
    return t if t else "—"


@router.message(Command(commands=["pay"]))
async def pay_info_command(message: Message):
    """Сводка подписок и трафика Антиглушилка по telegram_id."""
    if message.from_user.id not in ADMIN_IDS:
        return

    args = (message.text or "").split()
    if len(args) < 2:
        await message.answer("❌ Использование: /pay <telegram_id>\nНапример: /pay 123456789")
        return

    try:
        target_id = int(args[1].strip())
    except ValueError:
        await message.answer("❌ ID должен быть числом.")
        return

    user = await sql.get_user_object_by_user_id(target_id)
    if not user:
        await message.answer(f"❌ Пользователь {target_id} не найден в базе данных.")
        return

    panel_lines: dict[int, str] = {}
    for device_slots in (3, 5, 10):
        uname = panel_username(target_id, BOT_ID, device_slots=device_slots)
        try:
            ar = await x3.activ(uname)
            panel_lines[device_slots] = _pay_panel_sub_line(ar)
        except Exception as e:
            logger.exception("/pay: панель %s устройств", device_slots)
            panel_lines[device_slots] = f"Ошибка: {e}"

    db_dates = {
        3: user.subscription_3_end_date,
        5: user.subscription_end_date,
        10: user.subscription_10_end_date,
    }

    trafic_wl, limit_wl = await sql.get_wl_limits(target_id)
    used_gb = await get_wl_used_gb_for_user(x3, target_id, trafic_wl)
    remaining_gb = max(0.0, round(limit_wl - used_gb, 2))

    pay_rows = await sql.get_user_subscription_payment_report(target_id)
    pay_lines: list[str] = []
    for tc, kind, days_s in pay_rows:
        pay_lines.append(f"• {_pay_dt_str(tc)} — {kind} — {days_s} дн.")

    body = (
        f"<b>/pay {target_id}</b> (bot_id={BOT_ID})\n\n"
        f"Подписка в БД 3 устройства — {_pay_dt_str(db_dates[3])}\n"
        f"Подписка в панели — 3 устройства — {panel_lines[3]}\n"
        f"Подписка в БД 5 устройств — {_pay_dt_str(db_dates[5])}\n"
        f"Подписка в панели — 5 устройств — {panel_lines[5]}\n"
        f"Подписка в БД 10 устройств — {_pay_dt_str(db_dates[10])}\n"
        f"Подписка в панели — 10 устройств — {panel_lines[10]}\n\n"
        f"📡 <b>Антиглушилка</b>\n"
        f"├ Лимит: <b>{limit_wl:.2f} GB</b>\n"
        f"├ Использовано: <b>{used_gb:.2f} GB</b>\n"
        f"└ Осталось: <b>{remaining_gb:.2f} GB</b>\n\n"
        f"<b>Платежи:</b>\n"
    )
    body += "\n".join(pay_lines) if pay_lines else "Нет"
    await message.answer(body, parse_mode="HTML")


@router.message(Command(commands=["delete"]))
async def delete_user_command(message: Message):
    """Удаление пользователя из БД этого бота по Telegram ID. Только ADMIN_IDS."""
    if message.from_user.id not in ADMIN_IDS:
        return

    try:
        args = (message.text or "").split()
        if len(args) < 2:
            await message.answer("❌ Использование: /delete <telegram_id>\nНапример: /delete 123456789")
            return

        user_id_to_delete = int(args[1].strip())
        user_data = await sql.get_user(user_id_to_delete)
        if not user_data:
            await message.answer(f"❌ Пользователь с ID {user_id_to_delete} не найден в базе данных.")
            return

        user_info = {
            "user_id": user_data[1],
            "ref": user_data[2],
            "in_panel": user_data[4],
        }

        if not await sql.delete_from_db(user_id_to_delete):
            await message.answer(
                f"❌ Ошибка при удалении пользователя {user_id_to_delete}.\n"
                "Возможно, пользователь уже был удалён или произошла ошибка базы данных."
            )
            return

        logger.info("Админ {} удалил пользователя {} из БД бота {}", message.from_user.id, user_id_to_delete, BOT_ID)
        await message.answer(
            f"✅ Пользователь успешно удалён из базы данных\n\n"
            f"📋 Информация об удалённом пользователе:\n"
            f"├ ID: {user_info['user_id']}\n"
            f"├ Реферер: {user_info['ref'] if user_info['ref'] else 'нет'}\n"
            f"└ Брал ключ: {'✅ да' if user_info['in_panel'] else '❌ нет'}\n"
            f"⚠️ Пользователь удалён только из базы данных бота.\n"
            f"   Подписка в панели управления (X3) остаётся активной."
        )
    except ValueError:
        await message.answer(
            "❌ Неверный формат Telegram ID.\n"
            "Используйте только цифры, например: /delete 123456789"
        )
    except Exception as e:
        logger.error("Ошибка в команде /delete: {}", e)
        await message.answer(f"❌ Произошла ошибка при выполнении команды: {str(e)}")


@router.message(Command(commands=["reset_field_bool_2"]))
async def reset_field_bool_2_command(message: Message):
    """Сброс field_bool_2: у всех или у одного user_id."""
    if message.from_user.id not in ADMIN_IDS:
        return

    args = (message.text or "").split()
    if len(args) >= 2:
        try:
            target_id = int(args[1].strip())
        except ValueError:
            await message.answer("❌ Использование: /reset_field_bool_2 [telegram_id]")
            return
        user_row = await sql.get_user(target_id)
        if not user_row:
            await message.answer(f"❌ Пользователь {target_id} не найден.")
            return
        await sql.update_field_bool_2(target_id, False)
        await message.answer(f"Готово: field_bool_2 = false для user_id {target_id}.")
        logger.info("Админ {}: сброс field_bool_2 для {}", message.from_user.id, target_id)
        return

    n = await sql.reset_field_bool_2_all()
    await message.answer(f"Готово: field_bool_2 = false у {n} записей в users (bot_id={BOT_ID}).")
    logger.info("Админ {}: сброс field_bool_2 для всех, обновлено: {}", message.from_user.id, n)


@router.message(Command(commands=["add_traffic"]))
async def add_traffic_command(message: Message):
    """Админ: добавить GB к limit_wl."""
    if message.from_user.id not in ADMIN_IDS:
        return

    args = (message.text or "").split()
    if len(args) < 3:
        await message.answer(
            "❌ Использование: /add_traffic <telegram_id> <GB>\n"
            "Например: /add_traffic 123456789 10"
        )
        return

    try:
        target_id = int(args[1].strip())
        gb = float(args[2].strip().replace(",", "."))
    except ValueError:
        await message.answer("❌ ID и количество GB должны быть числами.")
        return

    if gb <= 0:
        await message.answer("❌ Количество GB должно быть больше 0.")
        return

    user = await sql.get_user_object_by_user_id(target_id)
    if not user:
        await message.answer(f"❌ Пользователь {target_id} не найден в базе данных.")
        return

    trafic_wl, _ = await sql.get_wl_limits(target_id)
    used_gb = await get_wl_used_gb_for_user(x3, target_id, trafic_wl)

    await sql.add_wl_limit(target_id, gb)
    _, limit_wl = await sql.get_wl_limits(target_id)
    remaining_gb = max(0.0, round(limit_wl - used_gb, 2))

    squad_note = ""
    if limit_wl > used_gb:
        panel_users = await fetch_all_pro_panel_users(x3, target_id)
        if any_pro_user_on_limited_squad(panel_users):
            n = await reassign_all_pro_to_active(x3, target_id)
            if n:
                squad_note = f"\n✅ Squad → active (Антиглушилка), {n} акк."
            else:
                squad_note = "\n⚠️ Не удалось переназначить squad в панели"

    admin_text = (
        f"✅ <b>Добавлено {gb:g} GB</b> для user <code>{target_id}</code>{squad_note}\n\n"
        f"├ Использовано: <b>{used_gb:.2f} GB</b>\n"
        f"├ Лимит: <b>{limit_wl:.2f} GB</b>\n"
        f"└ Осталось: <b>{remaining_gb:.2f} GB</b>"
    )
    await message.answer(admin_text, parse_mode="HTML")
    logger.info(
        "Админ {}: /add_traffic uid={} +{} GB used={:.2f} limit={:.2f}",
        message.from_user.id, target_id, gb, used_gb, limit_wl,
    )

    try:
        await bot.send_message(
            chat_id=target_id,
            text=lexicon["wl_traffic_admin_grant"].format(
                gb=gb,
                limit_gb=limit_wl,
                used_gb=used_gb,
                remaining_gb=remaining_gb,
            ),
            parse_mode="HTML",
            reply_markup=create_kb(1, back_to_main=BTN_BACK),
        )
    except Exception as e:
        await message.answer(f"⚠️ Лимит добавлен, но push пользователю не отправлен: {e}")
        logger.error("/add_traffic: push uid={}: {}", target_id, e)

