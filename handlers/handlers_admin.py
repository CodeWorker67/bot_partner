import random
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from bot import sql, x3, bot
from config import ADMIN_IDS, CHECKER_ID
from keyboard import create_kb, STYLE_PRIMARY, STYLE_SUCCESS, STYLE_DANGER, keyboard_sub_after_buy
from lexicon import lexicon
from logging_config import logger
import asyncio
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command

from sheduler.check_connect import check_connect
from tariff_resolve import panel_username, panel_username_for_site_user
from telegram_ids import is_telegram_chat_id

router = Router()

PRO_HWID_DEVICE_LIMIT = 5

_MSK = timezone(timedelta(hours=3))


def _msk_dt_str(dt: Optional[datetime]) -> str:
    if dt is None:
        return "Нет"
    if dt.tzinfo is None:
        aware = dt.replace(tzinfo=timezone.utc)
    else:
        aware = dt.astimezone(timezone.utc)
    return aware.astimezone(_MSK).strftime("%d-%m-%Y %H:%M МСК")


def _pay_dt_str(dt: Optional[datetime]) -> str:
    """Формат даты для /pay: YYYY-MM-DD HH:MM:SS (МСК)."""
    if dt is None:
        return "Нет"
    if dt.tzinfo is None:
        aware = dt.replace(tzinfo=timezone.utc)
    else:
        aware = dt.astimezone(timezone.utc)
    return aware.astimezone(_MSK).strftime("%Y-%m-%d %H:%M:%S")


def _pay_panel_sub_line(activ_result: dict) -> str:
    t = activ_result.get("time", "-")
    if t in (None, "", "-"):
        return "Нет"
    try:
        parsed = datetime.strptime(str(t).replace(" МСК", "").strip(), "%d-%m-%Y %H:%M")
        return parsed.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return str(t)


def _panel_sub_line(activ_result: dict) -> str:
    t = activ_result.get("time", "-")
    if t in (None, "", "-"):
        return "Нет"
    return str(t)


def _split_long_text(text: str, limit: int = 3800) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    rest = text
    while rest:
        parts.append(rest[:limit])
        rest = rest[limit:]
    return parts


def _panel_usernames_by_device(user) -> dict[int, str]:
    tg = None
    if user.user_id is not None and int(user.user_id) > 0:
        tg = int(user.user_id)
    elif user.linked_telegram_id is not None and int(user.linked_telegram_id) > 0:
        tg = int(user.linked_telegram_id)

    out: dict[int, str] = {}
    for device_slots in (3, 5, 10):
        if tg is not None:
            out[device_slots] = panel_username(tg, white=False, device_slots=device_slots)
        else:
            out[device_slots] = panel_username_for_site_user(
                int(user.user_id), white=False, device_slots=device_slots
            )
    return out

_ADD7ALL_PREVIEW_CB = "add7all_preview"
_ADD7ALL_YES_CB = "add7all_yes"
_ADD7ALL_NO_CB = "add7all_no"

_ADD7ALL_PROMO_TEXT = (
    "Самое время вернуться в ВПН ДЛЯ СВОИХ — дарим 7 дней тестдрайва новых серверов🟢\n\n"
    "Подключение займёт пару секунд\n\n"
    "Жми👇"
)

_ADD7ALL_TRIAL_KB = create_kb(
    1,
    styles={"trial_return_get": STYLE_SUCCESS},
    trial_return_get="🔥Получить ТРИАЛ",
)

_ADD7SUB_PROMO_TEXT = (
    "Уважаемые друзья!\n"
    "Мы столкнулись с аварией в датацентре.\n"
    "Проблема решена - бот снова заработал.\n"
    "В качестве компенсации добавляем Вам 7 дней к подписке!🔥"
)

_ADD7SUB_CONNECT_KB = create_kb(
    1,
    styles={"connect_vpn": STYLE_PRIMARY},
    connect_vpn="🔗 Подключить ВПН",
)


def _panel_username_for_user(user_id: int, device_slots: int) -> str:
    if user_id > 0:
        return panel_username(user_id, white=False, device_slots=device_slots)
    return panel_username_for_site_user(user_id, white=False, device_slots=device_slots)


def _hwid_limit_for_panel_username(username: str) -> int:
    if "white" in username:
        return 1
    if username.endswith("_3"):
        return 3
    if username.endswith("_10"):
        return 10
    return PRO_HWID_DEVICE_LIMIT


def _parse_sub_target(raw: str) -> tuple[int, str, str]:
    """
    Разбор цели /sub: telegram_id, username в панели, метка тарифа.
    Примеры: 123456789 → 5 устр.; 123456789_3; 123456789_10; 123456789_white.
    """
    raw = raw.strip()
    if raw.endswith("_white"):
        tg_id = int(raw[:-6])
        return tg_id, raw, "white"
    if raw.endswith("_10"):
        tg_id = int(raw[:-3])
        return tg_id, raw, "10"
    if raw.endswith("_3"):
        tg_id = int(raw[:-2])
        return tg_id, raw, "3"
    tg_id = int(raw)
    return tg_id, str(tg_id), "5"


_SUB_TIER_LABELS = {
    "5": "5 устройств",
    "3": "3 устройства",
    "10": "10 устройств",
    "white": "мобильный (white)",
}


def _add_days_to_subscription_end(
    end_dt: datetime, now: datetime, days: int = 7
) -> datetime:
    if end_dt.tzinfo is None:
        end_aware = end_dt.replace(tzinfo=timezone.utc)
    else:
        end_aware = end_dt.astimezone(timezone.utc)
    now_utc = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    if end_aware <= now_utc:
        return now_utc + timedelta(days=days)
    return end_aware + timedelta(days=days)


async def _extend_subscription_tier(
    user_id: int,
    device_slots: int,
    db_end_date: datetime,
    now: datetime,
) -> bool:
    """+7 дней к тарифу в панели и БД (по дате из БД, если клиента нет в панели)."""
    username = _panel_username_for_user(user_id, device_slots)
    panel_resp = await x3.get_user_by_username(username)
    if panel_resp and panel_resp.get("response"):
        return await x3.updateClient(7, username, user_id)

    new_date = _add_days_to_subscription_end(db_end_date, now, 7)
    hw_lim = device_slots if device_slots in (3, 10) else PRO_HWID_DEVICE_LIMIT
    ok, actual = await x3.set_expiration_date(
        username, new_date, user_id, hwid_device_limit=hw_lim
    )
    if not ok or actual is None:
        return False
    if device_slots == 3:
        await sql.update_subscription_3_end_date(user_id, actual)
    elif device_slots == 10:
        await sql.update_subscription_10_end_date(user_id, actual)
    else:
        await sql.update_subscription_end_date(user_id, actual)
    return True


async def _add_7_sub_extend_user(user, now: datetime) -> Tuple[bool, bool]:
    """
    Продлевает все непустые PRO-даты пользователя.
    Возвращает (успех по всем тарифам, были ли тарифы для продления).
    """
    tiers = []
    if user.subscription_end_date is not None:
        tiers.append((5, user.subscription_end_date))
    if user.subscription_3_end_date is not None:
        tiers.append((3, user.subscription_3_end_date))
    if user.subscription_10_end_date is not None:
        tiers.append((10, user.subscription_10_end_date))
    if not tiers:
        return False, False

    all_ok = True
    for device_slots, end_dt in tiers:
        if not await _extend_subscription_tier(user.user_id, device_slots, end_dt, now):
            all_ok = False
    return all_ok, True


@router.message(F.video, F.from_user.id.in_(ADMIN_IDS))
async def get_video(message: Message):
    await message.answer(message.video.file_id)


@router.message(F.photo, F.from_user.id.in_(ADMIN_IDS))
async def get_photo(message: Message):
    await message.answer(message.photo[-1].file_id)


async def _partner_admin_stats_text(tg_id: int) -> Optional[str]:
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


@router.message(Command(commands=['partner']))
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


@router.message(Command(commands=['partner_remove']))
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


@router.message(Command(commands=['user']))
async def user_info(message: Message):

    # Проверка прав администратора
    if message.from_user.id not in ADMIN_IDS:
        return

    try:
        # Извлекаем аргументы команды
        args = message.text.split()

        if len(args) < 2:
            await message.answer("❌ Использование: /user <telegram_id>\nНапример: /user 123456789")
            return

        user_id = int(args[1].strip())

        # Проверяем, существует ли пользователь в БД
        user_data = await sql.get_user(user_id)

        if not user_data:
            await message.answer(f"❌ Пользователь с ID {user_id} не найден в базе данных.")
            return
        text = []
        for i in range(len(user_data)):
            if isinstance(user_data[i], datetime):
                item = user_data[i].strftime('%Y-%m-%d %H:%M:%S')
                text.append(item)
            elif user_data[i] is None:
                text.append('None')
            else:
                text.append(str(user_data[i]))
        text = '\n'.join(text)
        await message.answer(text)
    except Exception as e:
        await message.answer(f'Ошибка при формировании сообщения: {str(e)}')


@router.message(Command(commands=['pay']))
async def pay_info_command(message: Message):
    """Сводка подписок (БД / панель) по тарифам 3/5/10 устройств и успешные платежи."""
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

    usernames = _panel_usernames_by_device(user)
    panel_lines: dict[int, str] = {}
    for device_slots in (3, 5, 10):
        try:
            ar = await x3.activ(usernames[device_slots])
            panel_lines[device_slots] = _pay_panel_sub_line(ar)
        except Exception as e:
            logger.exception("/pay: панель %s устройств", device_slots)
            panel_lines[device_slots] = f"Ошибка: {e}"

    db_dates = {
        3: user.subscription_3_end_date,
        5: user.subscription_end_date,
        10: user.subscription_10_end_date,
    }

    pay_rows = await sql.get_user_subscription_payment_report(target_id)
    pay_lines: list[str] = []
    for tc, kind, days_s in pay_rows:
        ts = _pay_dt_str(tc)
        pay_lines.append(f"• {ts} — {kind} — {days_s} дн.")

    body = (
        f"<b>/pay {target_id}</b>\n\n"
        f"Подписка в БД бота 3 устройства — {_pay_dt_str(db_dates[3])}\n"
        f"Подписка в панели — 3 устройства — {panel_lines[3]}\n"
        f"Подписка в БД бота 5 устройства — {_pay_dt_str(db_dates[5])}\n"
        f"Подписка в панели — 5 устройства — {panel_lines[5]}\n"
        f"Подписка в БД бота 10 устройства — {_pay_dt_str(db_dates[10])}\n"
        f"Подписка в панели — 10 устройства — {panel_lines[10]}\n\n"
        f"<b>Платежи:</b>\n"
    )
    if pay_lines:
        body += "\n".join(pay_lines)
    else:
        body += "Нет"

    for chunk in _split_long_text(body):
        await message.answer(chunk)


@router.message(Command(commands=['sub']))
async def set_subscription_date(message: Message):
    """Установка даты подписки в панели и в БД по слоту тарифа (5 / 3 / 10 / white)."""
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ Эта команда доступна только администраторам.")
        return

    try:
        args = message.text.split()
        if len(args) < 3:
            await message.answer(
                "❌ Использование:\n"
                "  /sub <telegram_id> <дата_время>         – подписка 5 устройств\n"
                "  /sub <telegram_id>_3 <дата_время>       – подписка 3 устройства\n"
                "  /sub <telegram_id>_10 <дата_время>      – подписка 10 устройств\n"
                "  /sub <telegram_id>_white <дата_время>   – мобильный тариф\n"
                "Примеры:\n"
                "  /sub 123456789 2026-02-01 17:14:27\n"
                "  /sub 123456789_3 2026-02-01 17:14:27\n"
                "  /sub 123456789_10 2026-02-01 17:14:27\n"
                "Формат даты: YYYY-MM-DD HH:MM:SS"
            )
            return

        try:
            user_id, username, tier = _parse_sub_target(args[1])
        except ValueError:
            await message.answer(
                "❌ Неверный идентификатор. Используйте telegram_id или telegram_id_3 / _10 / _white."
            )
            return

        date_str = " ".join(args[2:])

        date_formats = [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%d.%m.%Y %H:%M:%S",
            "%d.%m.%Y %H:%M",
        ]
        target_date = None
        for fmt in date_formats:
            try:
                target_date = datetime.strptime(date_str, fmt)
                target_date = target_date.replace(tzinfo=timezone.utc)
                break
            except ValueError:
                continue
        if target_date is None:
            await message.answer(f"❌ Неверный формат даты: {date_str}")
            return

        user_data = await sql.get_user(user_id)
        if not user_data:
            await message.answer("⚠️ Пользователь не найден в БД.")
            return

        hw_lim = _hwid_limit_for_panel_username(username)
        try:
            success, actual_date = await x3.set_expiration_date(
                username, target_date, user_id, hwid_device_limit=hw_lim
            )
        except Exception as e:
            logger.exception("Ошибка в команде /sub при обращении к панели")
            await message.answer(
                f"❌ Ошибка при обращении к панели VPN: {e}\n"
                "Проверьте доступность панели (PANEL_URL) и повторите команду."
            )
            return

        if not success or actual_date is None:
            panel_ok = await x3.test_connect()
            hint = (
                "Панель VPN не отвечает — проверьте PANEL_URL и доступность сервера."
                if not panel_ok
                else "Пользователь не найден и не удалось создать, либо панель вернула ошибку."
            )
            await message.answer(
                f"❌ Не удалось установить дату в панели.\n{hint}\nПодробности в логах."
            )
            return

        await x3._persist_subscription_db(sql, user_id, username, actual_date)

        notify_status = ""
        if is_telegram_chat_id(user_id):
            try:
                sub_link = await x3.sublink(username)
                user_text = lexicon["sub_granted_notify"].format(
                    tier=_SUB_TIER_LABELS.get(tier, tier),
                    end_date=_msk_dt_str(actual_date),
                )
                await bot.send_message(
                    user_id,
                    user_text,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                    reply_markup=keyboard_sub_after_buy(sub_link) if sub_link else None,
                )
                notify_status = "\n📨 Пользователь уведомлён."
            except Exception as e:
                logger.error(f"/sub: не удалось уведомить user={user_id}: {e}")
                notify_status = f"\n⚠️ Не удалось уведомить пользователя: {e}"
        else:
            notify_status = "\nℹ️ Уведомление не отправлено (не Telegram ID)."

        await message.answer(
            f"✅ Дата подписки успешно установлена!\n\n"
            f"👤 Пользователь: {user_id}\n"
            f"🔑 Панель: {username}\n"
            f"📅 Целевая дата (UTC): {target_date.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"📅 Установленная в панели дата (UTC): {actual_date.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"📝 Тариф: {_SUB_TIER_LABELS.get(tier, tier)}\n"
            f"💾 База данных обновлена."
            f"{notify_status}"
        )

    except Exception as e:
        logger.error(f"Ошибка в команде /sub: {e}")
        await message.answer(f"❌ Произошла ошибка: {str(e)}")


@router.message(Command(commands=['delete']))
async def delete_user_command(message: Message):
    """Удаление пользователя из БД по Telegram ID"""

    # Проверка прав администратора
    if message.from_user.id not in ADMIN_IDS:
        return

    try:
        # Извлекаем аргументы команды
        args = message.text.split()

        if len(args) < 2:
            await message.answer("❌ Использование: /delete <telegram_id>\nНапример: /delete 123456789")
            return

        user_id_to_delete = int(args[1].strip())

        # Проверяем, существует ли пользователь в БД
        user_data = await sql.get_user(user_id_to_delete)

        if not user_data:
            await message.answer(f"❌ Пользователь с ID {user_id_to_delete} не найден в базе данных.")
            return

        # Получаем информацию о пользователе для уведомления
        user_info = {
            "user_id": user_data[1],  # user_id
            "ref": user_data[2],  # ref
            "in_panel": user_data[4],  # in_panel
            "in_chanel": user_data[7] if len(user_data) > 7 else False  # in_chanel
        }

        # УДАЛЯЕМ ПОЛЬЗОВАТЕЛЯ ИЗ БД
        deletion_success = await sql.delete_from_db(user_id_to_delete)

        if deletion_success:
            # Логируем действие
            logger.info(f"Администратор {message.from_user.id} удалил пользователя {user_id_to_delete} из БД")

            # Формируем отчет об удалении
            report_message = (
                f"✅ Пользователь успешно удалён из базы данных\n\n"
                f"📋 Информация об удалённом пользователе:\n"
                f"├ ID: {user_info['user_id']}\n"
                f"├ Реферер: {user_info['ref'] if user_info['ref'] else 'нет'}\n"
                f"└ Брал ключ: {'✅ да' if user_info['in_panel'] else '❌ нет'}\n"
                f"⚠️ Пользователь удалён только из базы данных бота.\n"
                f"   Подписка в панели управления (X3) остаётся активной.\n"
                f"   Чтобы удалить полностью, используйте команду /gift на 0 дней."
            )

            await message.answer(report_message)

        else:
            await message.answer(f"❌ Ошибка при удалении пользователя {user_id_to_delete}.\n"
                                 "Возможно, пользователь уже был удалён или произошла ошибка базы данных.")

    except ValueError:
        await message.answer("❌ Неверный формат Telegram ID.\n"
                             "Используйте только цифры, например: /delete 123456789")
    except Exception as e:
        logger.error(f"Ошибка в команде /delete: {e}")
        await message.answer(f"❌ Произошла ошибка при выполнении команды: {str(e)}")


@router.message(Command("online"))
async def check_online(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    users_x3 = await x3.get_all_users()

    active_telegram_ids = []
    for user in users_x3:
        if user['userTraffic']['firstConnectedAt']:
            connected_str = user['userTraffic']['onlineAt']
            try:
                connected_dt = datetime.fromisoformat(connected_str.replace('Z', '+00:00'))
                connected_date = connected_dt.date()
                if connected_date == datetime.now().date():
                    telegram_id = user.get('telegramId')
                    if telegram_id is not None:
                        active_telegram_ids.append(int(telegram_id))
            except (ValueError, TypeError):
                continue

    full_tariff_ids = await sql.user_ids_with_full_tariff_payment(active_telegram_ids)
    count_pay = 0
    count_trial = 0
    for tg_id in active_telegram_ids:
        user_data = await sql.get_user(tg_id)
        if user_data:
            if tg_id in full_tariff_ids:
                count_pay += 1
            else:
                count_trial += 1
    await message.answer(
        f"Всего юзеров в панели: {len(users_x3)}\n"
        f"Юзеров, которые были онлайн сегодня: {len(active_telegram_ids)}\n"
        f"Юзеры с платной подпиской: {count_pay}\n"
        f"Юзеры на триале: {count_trial}"
    )


@router.message(Command("balance_panel"))
async def check_online(message: Message):
    squad_1 = ['494bf6ce-d62b-4929-a980-dfc14b8b5ddb']
    squad_2 = ['2e6f13b9-58a0-4f46-bd76-0d294f00ef18']
    success_count = 0
    fail_count = 0
    if message.from_user.id not in ADMIN_IDS:
        return
    users_x3 = await x3.get_all_users()
    for user in users_x3:
        await asyncio.sleep(0.3)
        random_squad = random.choice([squad_1, squad_2])
        username = user.get('username', '')
        if 'white' not in username and 'cascade-bridge-system' not in username:
            uuid = user.get('uuid')
            connect = user.get('firstConnectedAt')
            if uuid and connect:
                if await x3.update_user_squads(uuid, random_squad):
                    success_count += 1
                else:
                    fail_count += 1
    await message.answer(f"{len(users_x3)} - всего юзеров в панели\n{success_count + fail_count} - подключенных\n{success_count} - обновлено\n{fail_count} - ошибка")


@router.message(Command(commands=['sync_panel']))
async def sync_panel(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    await message.answer("🔄 Запускаю синхронизацию пользователей...")

    # 1. Получаем всех пользователей из панели и строим словарь {telegramId: user_data}
    users_panel = await x3.get_all_users()
    panel_dict = {}
    for user in users_panel:
        tg_id = user.get('telegramId')
        if tg_id is not None:
            panel_dict[tg_id] = user

    # 2. Получаем список пользователей, у которых is_pay_null=True и subscription_end_date=None
    users_for_sync = await sql.select_subscribed_not_in_chanel()

    # 3. Статистика
    updated = 0          # обновлено дат в БД
    added_to_panel = 0   # добавлено в панель
    not_found = 0        # не найдено в панели (остались в списке)

    # 4. Обрабатываем каждого пользователя из списка на синхронизацию
    if CHECKER_ID is not None:
        await bot.send_message(
            CHECKER_ID,
            'Добрый день. Мы создали Вам личный кабинет и начислили 5 дней пробного '
            'доступа.\nПерейдите по ссылке, нажав на кнопку 🔗 Подключить ВПН',
            reply_markup=create_kb(
                1,
                styles={'connect_vpn': STYLE_PRIMARY},
                connect_vpn='🔗 Подключить ВПН'))

    for user_id in users_for_sync:
        # Проверяем, есть ли пользователь в панели
        if user_id in panel_dict:
            user_data = panel_dict[user_id]

            # Получаем expireAt и преобразуем в datetime
            expire_str = user_data.get('expireAt')
            if expire_str:
                try:
                    expire_dt = datetime.fromisoformat(expire_str.replace('Z', '+00:00'))
                except Exception as e:
                    logger.error(f"Ошибка парсинга expireAt для {user_id}: {e}")
                    continue

                await sql.update_subscription_end_date(user_id, expire_dt)
                updated += 1
                logger.info(f"Обновлена дата для {user_id} до {expire_dt}")
        else:
            user_id_str = str(user_id)
            ud_sync = await sql.get_user(user_id)
            hw_lim = PRO_HWID_DEVICE_LIMIT
            result = await x3.addClient(5, user_id_str, user_id, hwid_device_limit=hw_lim)
            if result:
                added_to_panel += 1
                logger.info(f"Добавлен в панель пользователь {user_id} (day=0)")
                await bot.send_message(user_id,
                                       'Добрый день. Мы создали Вам личный кабинет и начислили 5 дней пробного '
                                       'доступа.\nПерейдите по ссылке, нажав на кнопку 🔗 Подключить ВПН',
                                       reply_markup=create_kb(
                                           1,
                                           styles={'connect_vpn': STYLE_PRIMARY},
                                           connect_vpn='🔗 Подключить ВПН'))
            else:
                not_found += 1
                logger.warning(f"Не удалось добавить в панель пользователя {user_id}")

    # 5. Итоговый отчёт
    report = (
        f"✅ Синхронизация завершена.\n"
        f"📊 Всего в панели: {len(users_panel)}\n"
        f"📋 Ожидало синхронизации: {len(users_for_sync)}\n"
        f"🔄 Обновлено дат в БД: {updated}\n"
        f"➕ Добавлено в панель (day=5): {added_to_panel}\n"
        f"❌ Не удалось добавить (ошибки): {not_found}"
    )
    await message.answer(report)
    logger.info(report)


@router.message(Command(commands=['shortuuid_export']))
async def shortuuid_export(message: Message):
    """Синхронизация shortUuid из панели в поля subscribtion / white_subscription в БД."""
    if message.from_user.id not in ADMIN_IDS:
        return

    await message.answer("🔄 Загружаю пользователей панели и записываю shortUuid в БД...")

    try:
        panel_users = await x3.get_all_users()
    except Exception as e:
        logger.error(f"shortuuid_export: панель: {e}")
        await message.answer(f"❌ Ошибка при запросе панели: {e}")
        return

    updated_sub = 0
    updated_white = 0
    skip_no_db = 0
    skip_no_tg = 0
    skip_no_short = 0
    errors = 0

    for user in panel_users:
        tg_id = user.get("telegramId")
        username = user.get("username") or ""
        if tg_id is None:
            if username.isdigit():
                tg_id = int(username)
            else:
                skip_no_tg += 1
                continue
        else:
            tg_id = int(tg_id)

        short_uuid = user.get("shortUuid")
        if not short_uuid:
            skip_no_short += 1
            continue

        db_user = await sql.get_user(tg_id)
        if not db_user:
            skip_no_db += 1
            continue

        is_white = "white" in username
        try:
            if is_white:
                await sql.update_white_subscription(tg_id, short_uuid)
                updated_white += 1
            else:
                await sql.update_subscribtion(tg_id, short_uuid)
                updated_sub += 1
            logger.success(f"shortuuid_export user {tg_id}: {short_uuid}")
        except Exception as e:
            errors += 1
            logger.error(f"shortuuid_export user {tg_id}: {e}")

    report = (
        f"✅ Готово.\n"
        f"📊 В панели записей: {len(panel_users)}\n"
        f"📝 subscribtion обновлено: {updated_sub}\n"
        f"📝 white_subscription обновлено: {updated_white}\n"
        f"⏭ без telegramId/username: {skip_no_tg}\n"
        f"⏭ без shortUuid: {skip_no_short}\n"
        f"⏭ нет в БД: {skip_no_db}\n"
        f"❌ ошибок записи: {errors}"
    )
    await message.answer(report)
    logger.info(report)


@router.message(Command(commands=['check_users']))
async def check_users_command(message: Message):
    """Проверка соответствия дат окончания подписки у оплаченных пользователей (has_discount=True)"""
    if message.from_user.id not in ADMIN_IDS:
        return

    await message.answer("🔄 Начинаю проверку пользователей с оплатами...")

    try:
        # 1. Получаем список оплаченных пользователей из БД
        users_with_discount = await sql.get_users_with_payment()
        total = len(users_with_discount)
        if total == 0:
            await message.answer("❌ Нет пользователей с оплатами.")
            return

        # 2. Получаем всех пользователей из панели (один запрос)
        panel_users = await x3.get_all_users()
        logger.info(f"Загружено {len(panel_users)} пользователей из панели")

        # 3. Строим словарь для быстрого поиска по telegramId и username
        panel_by_telegram = {}      # ключ: telegramId (int)
        panel_by_username = {}      # ключ: username (str)

        for user in panel_users:
            tg_id = user.get('telegramId')
            username = user.get('username')
            if tg_id is not None:
                panel_by_telegram[int(tg_id)] = user
            elif username:
                panel_by_username[username] = user

        # 4. Проходим по всем оплаченным пользователям и ищем их в панели
        mismatched = []      # кортежи (user_id, db_date, panel_date) для расхождений >=3ч
        not_found_in_panel = []  # пользователи, отсутствующие в панели
        processed = 0

        for user_id in users_with_discount:
            processed += 1
            if processed % 10 == 0:
                logger.info(f"Проверено {processed}/{total}")

            # Пытаемся найти пользователя в панели
            panel_user = panel_by_telegram.get(user_id)
            if panel_user is None:
                panel_user = panel_by_username.get(str(user_id))

            if panel_user is None:
                not_found_in_panel.append(user_id)
                continue

            expire_str = panel_user.get('expireAt')
            if not expire_str:
                # нет даты в панели – считаем расхождением (panel_date = None)
                db_expire = await sql.get_subscription_end_date(user_id)
                mismatched.append((user_id, db_expire, None))
                continue

            try:
                panel_expire = datetime.fromisoformat(expire_str.replace('Z', '+00:00'))
            except Exception:
                # не удалось распарсить дату панели
                db_expire = await sql.get_subscription_end_date(user_id)
                mismatched.append((user_id, db_expire, None))
                continue

            # Получаем дату из БД (обычная подписка)
            db_expire = await sql.get_subscription_end_date(user_id)
            panel_naive = panel_expire.replace(tzinfo=None)

            if db_expire is None:
                # нет даты в БД
                mismatched.append((user_id, None, panel_naive))
                continue

            db_naive = db_expire.replace(tzinfo=None)
            diff_hours = abs((panel_naive - db_naive).total_seconds()) / 3600

            if diff_hours >= 6:
                mismatched.append((user_id, db_naive, panel_naive))

        # 5. Формируем отчёт
        report_lines = []
        report_lines.append(f"📊 Результаты проверки:\n")
        report_lines.append(f"👥 Всего проверено: {total}")
        report_lines.append(f"❌ Расхождений в датах (>=6ч): {len(mismatched)}")
        report_lines.append(f"🔍 Не найдены в панели: {len(not_found_in_panel)}")

        # Если есть расхождения и их количество не превышает лимит для прямого вывода
        if mismatched or not_found_in_panel:
            if len(mismatched) <= 50 and len(not_found_in_panel) <= 50:
                if mismatched:
                    report_lines.append("\n🆔 Расхождения (команды для синхронизации):")
                    for uid, db_dt, panel_dt in mismatched:
                        db_str = db_dt.strftime('%Y-%m-%d %H:%M:%S') if db_dt else 'None'
                        panel_str = panel_dt.strftime('%Y-%m-%d %H:%M:%S') if panel_dt else 'None'
                        report_lines.append(f"/sub {uid} {db_str} /sub {uid} {panel_str}")
                if not_found_in_panel:
                    report_lines.append("\n🆔 Не найдены в панели:")
                    report_lines.extend(str(uid) for uid in not_found_in_panel)
                await message.answer("\n".join(report_lines))
            else:
                # Если много расхождений – отправляем файлом
                import io
                text_io = io.StringIO()
                text_io.write("user_id\tdb_date\tpanel_date\n")
                for uid, db_dt, panel_dt in mismatched:
                    db_str = db_dt.strftime('%Y-%m-%d %H:%M:%S') if db_dt else 'None'
                    panel_str = panel_dt.strftime('%Y-%m-%d %H:%M:%S') if panel_dt else 'None'
                    text_io.write(f"/sub {uid} {db_str} /sub {uid} {panel_str}\n")
                for uid in not_found_in_panel:
                    text_io.write(f"{uid}\tnot_found\n")
                text_io.seek(0)
                from aiogram.types import BufferedInputFile
                file_data = BufferedInputFile(text_io.getvalue().encode(), filename="check_users_report.txt")
                await message.answer_document(
                    document=file_data,
                    caption="\n".join(report_lines[:5])
                )
        else:
            await message.answer("✅ Все оплаченные пользователи синхронизированы (разница менее 3 часов).")

    except Exception as e:
        logger.exception("Ошибка в /check_users")
        await message.answer(f"❌ Ошибка: {str(e)}")


@router.message(Command(commands=['send_gift']))
async def send_gift_command(message: Message):
    """Отправляет подарок (3 дня подписки) пользователям, созданным 16 или 17 марта 2026,
    у которых in_panel=True, is_connect=False, is_delete=False."""
    if CHECKER_ID is None or message.from_user.id != CHECKER_ID:
        return

    await message.answer("🔄 Начинаю отправку подарков...")

    # Целевые даты
    target_dates = (datetime(2026, 3, 16), datetime(2026, 3, 17))

    # Получаем всех пользователей из БД (можно фильтровать на стороне Python, т.к. запрос сложный)
    all_users = await sql.get_all_users()  # список объектов Users

    # Фильтруем вручную
    candidates = [CHECKER_ID]
    for user in all_users:
        if user.is_delete:
            continue
        if not user.in_panel:
            continue
        if user.is_connect:
            continue
        if user.create_user.date() not in [d.date() for d in target_dates]:
            continue
        candidates.append(user.user_id)

    if not candidates:
        await message.answer("❌ Нет пользователей, удовлетворяющих условиям.")
        return
    else:
        await message.answer(f"Всего {len(candidates)} пользователей, удовлетворяющих условиям.")

    success_count = 0
    fail_count = 0

    # Текст сообщения
    gift_text = '''
🥵 Это была DDoS-атака!

Друзья, простите за временные неудобства. Из‑за перегрузки сервиса доступ мог быть нестабильным.

Мы столкнулись с мощной DDoS-атакой, если у вас <b>не открывался личный кабинет — проблема уже решена.</b>

🔥 Мы начислили вам <b>дополнительные 5 дней</b> к подписке, чтобы вы могли оценить ВПН ДЛЯ СВОИХ.

📱 Не можете настроить?
Если вы никак не могли разобраться с импортом конфигов — <b>смотрите видеоинструкцию</b>! Там всё разложено по полочкам.

🌐 Осталось только нажать кнопку «🔗 Подключить ВПН» — и вы в деле.
            '''

    for user_id in candidates[83:]:
        try:
            # Отправляем сообщение
            await bot.send_message(user_id,
                                   gift_text,
                                   reply_markup=create_kb(
                                       1,
                                       styles={
                                           'video_faq': STYLE_PRIMARY,
                                           'connect_vpn': STYLE_PRIMARY,
                                       },
                                       video_faq='🎥 Видеоинструкция',
                                       connect_vpn='🔗 Подключить ВПН'))
            # Добавляем 3 дня подписки
            result = await x3.updateClient(5, str(user_id), user_id)
            if result:
                success_count += 1
                logger.info(f"Подарок отправлен пользователю {user_id}")
            else:
                fail_count += 1
                logger.error(f"Не удалось обновить подписку для {user_id}")
            await asyncio.sleep(0.05)  # небольшая задержка
        except Exception as e:
            fail_count += 1
            logger.error(f"Ошибка при обработке {user_id}: {e}")

    await message.answer(
        f"✅ Рассылка подарков завершена.\n"
        f"👥 Найдено: {len(candidates)}\n"
        f"✅ Успешно: {success_count}\n"
        f"❌ Ошибок: {fail_count}"
    )


@router.message(Command(commands=['send_push']))
async def send_push_command(message: Message):
    """Отправляет информационное сообщение пользователям, созданным до 16 марта 2026,
    с активной подпиской (in_panel=True, subscription_end_date > now, is_delete=False)."""
    if CHECKER_ID is None or message.from_user.id != CHECKER_ID:
        return

    await message.answer("🔄 Начинаю отправку push-уведомления...")

    # Текущая дата
    now = datetime.now()
    # Пороговая дата (исключительно до 16 марта)
    threshold = datetime(2026, 3, 16)

    # Получаем всех пользователей
    all_users = await sql.get_all_users()

    # Фильтруем
    candidates = [CHECKER_ID]
    for user in all_users:
        if user.is_delete:
            continue
        if not user.in_panel:
            continue
        if user.create_user >= threshold:
            continue
        if not user.subscription_end_date or user.subscription_end_date <= now:
            continue
        candidates.append(user.user_id)

    if not candidates:
        await message.answer("❌ Нет пользователей, удовлетворяющих условиям.")
        return
    else:
        await message.answer(f"Всего {len(candidates)} пользователей, удовлетворяющих условиям.")

    push_text = '''
🥵 Это была DDoS-атака!

Друзья, простите за временные неудобства. Из‑за перегрузки сервиса доступ мог быть нестабильным.
Мы столкнулись с мощной DDoS-атакой, если у вас <b>не открывался личный кабинет — проблема уже решена.</b>

📱 Не можете настроить?
Если вы никак не могли разобраться с импортом конфигов — <b>смотрите видеоинструкцию</b>! Там всё разложено по полочкам.

🌐 Осталось только нажать кнопку «🔗 Подключить ВПН» — и вы снова в деле.
    '''

    success_count = 0
    fail_count = 0

    for user_id in candidates[22:]:
        try:
            await bot.send_message(user_id,
                                   push_text,
                                   reply_markup=create_kb(
                                       1,
                                       styles={
                                           'video_faq': STYLE_PRIMARY,
                                           'connect_vpn': STYLE_PRIMARY,
                                       },
                                       video_faq='🎥 Видеоинструкция',
                                       connect_vpn='🔗 Подключить ВПН'))
            success_count += 1
            logger.info(f"Push отправлен пользователю {user_id}")
            await asyncio.sleep(0.05)
        except Exception as e:
            fail_count += 1
            logger.error(f"Ошибка отправки для {user_id}: {e}")

    await message.answer(
        f"✅ Рассылка завершена.\n"
        f"👥 Найдено: {len(candidates)}\n"
        f"✅ Успешно: {success_count}\n"
        f"❌ Ошибок: {fail_count}"
    )


@router.message(Command(commands=['reset_bool3']))
async def reset_field_bool_3_all_command(message: Message):
    """Сброс field_bool_3 у всех пользователей (триал / одноразовые акции)."""
    if message.from_user.id not in ADMIN_IDS:
        return
    n = await sql.reset_field_bool_3_all()
    await message.answer(f"Готово: field_bool_3 = false у {n} записей в users.")
    logger.info(f"Админ {message.from_user.id}: сброс field_bool_3 для всех, обновлено строк: {n}")


@router.message(Command(commands=['add_7_to_all']))
async def add_7_to_all_command(message: Message):
    """
    Рассылка пользователям без активной PRO-подписки (3/5/10 устройств).
    Кнопка «ТРИАЛ»; +7 дней по нажатию, field_bool_3.
    """
    if message.from_user.id not in ADMIN_IDS:
        return

    user_ids = await sql.SELECT_USER_IDS_NO_ACTIVE_PRO_SUBSCRIPTION()
    n = len(user_ids)
    if not user_ids:
        await message.answer(
            "Нет пользователей: is_delete=False, нет PRO-подписки 3/5/10 "
            "или все даты окончания не позже чем 2 дня назад (UTC)."
        )
        return

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="▶️ Превью и подтверждение",
                    callback_data=_ADD7ALL_PREVIEW_CB,
                    style=STYLE_SUCCESS,
                )
            ]
        ]
    )
    await message.answer(
        f"К получателям рассылки: {n} чел.\n"
        f"(is_delete=False; по каждому тарифу 3/5/10: нет подписки или окончание 2+ дня назад UTC).\n\n"
        f"Дальше бот пришлёт вам превью текста с кнопкой «🔥Получить ТРИАЛ» и запрос подтверждения.\n"
        f"Начисление +7 дней — только по нажатию:\n"
        f"• нет PRO-подписки → 7 дней на 5 устройств;\n"
        f"• есть просроченные → +7 дней на тариф с макс. числом устройств среди просроченных.",
        reply_markup=kb,
    )


@router.callback_query(F.data == _ADD7ALL_PREVIEW_CB)
async def add_7_to_all_preview(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа.", show_alert=True)
        return

    await callback.answer()
    user_ids = await sql.SELECT_USER_IDS_NO_ACTIVE_PRO_SUBSCRIPTION()
    n = len(user_ids)
    if not user_ids:
        await callback.message.edit_text("Список пуст. Повторите /add_7_to_all.")
        return

    chat_id = callback.message.chat.id
    await callback.message.edit_text(
        "Ниже — превью рассылки и кнопка подтверждения отправки пользователям."
    )

    await bot.send_message(
        chat_id,
        _ADD7ALL_PROMO_TEXT,
        reply_markup=_ADD7ALL_TRIAL_KB,
    )

    confirm_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да",
                    callback_data=_ADD7ALL_YES_CB,
                    style=STYLE_SUCCESS,
                ),
                InlineKeyboardButton(
                    text="Нет",
                    callback_data=_ADD7ALL_NO_CB,
                    style=STYLE_DANGER,
                ),
            ]
        ]
    )
    await bot.send_message(
        chat_id,
        f"Человек в рассылке — {n}. Подтвердите отправку.",
        reply_markup=confirm_kb,
    )


@router.callback_query(F.data == _ADD7ALL_NO_CB)
async def add_7_to_all_cancel(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа.", show_alert=True)
        return
    await callback.answer()
    await callback.message.edit_text(
        "Отправка рассылки add_7_to_all отменена.",
        reply_markup=None,
    )


@router.callback_query(F.data == _ADD7ALL_YES_CB)
async def add_7_to_all_confirm(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа.", show_alert=True)
        return

    await callback.answer()
    user_ids = await sql.SELECT_USER_IDS_NO_ACTIVE_PRO_SUBSCRIPTION()
    if not user_ids:
        await callback.message.edit_text("Список пуст. Повторите /add_7_to_all.")
        return

    await callback.message.edit_text(
        f"⏳ Рассылка add_7_to_all: {len(user_ids)} получателей…"
    )

    admin_chat_id = callback.message.chat.id
    sent = 0
    failed = 0
    skipped_non_tg = 0

    for user_id in user_ids:
        if not is_telegram_chat_id(user_id):
            skipped_non_tg += 1
            await asyncio.sleep(0.1)
            continue
        try:
            await bot.send_message(
                user_id,
                _ADD7ALL_PROMO_TEXT,
                reply_markup=_ADD7ALL_TRIAL_KB,
            )
            sent += 1
            if sent % 1000 == 0:
                try:
                    await bot.send_message(
                        admin_chat_id,
                        f"add_7_to_all: отправлено сообщений — {sent}",
                    )
                except Exception as notify_err:
                    logger.warning(
                        "add_7_to_all: не удалось отправить прогресс админу: %s",
                        notify_err,
                    )
        except Exception as e:
            failed += 1
            logger.warning("add_7_to_all: не отправлено user_id=%s: %s", user_id, e)

        await asyncio.sleep(0.1)

    await callback.message.answer(
        "Готово (add_7_to_all).\n"
        f"• Отправлено: {sent}\n"
        f"• Ошибок: {failed}\n"
        f"• Пропущено (не Telegram chat_id): {skipped_non_tg}"
    )


@router.message(Command(commands=['add_7_sub']))
async def add_7_sub_command(message: Message):
    """
    Компенсация +7 дней ко всем непустым PRO-подпискам (3/5/10) и рассылка.
    Пользователи: in_panel=True, is_delete=False.
    """
    if message.from_user.id not in ADMIN_IDS:
        return

    now = datetime.now(timezone.utc)
    all_users = await sql.get_all_users()
    candidates = [
        u for u in all_users
        if not u.is_delete and u.in_panel
    ]
    n = len(candidates)
    if not candidates:
        await message.answer(
            "Нет пользователей: in_panel=True, is_delete=False."
        )
        return

    await message.answer(
        f"⏳ /add_7_sub: {n} пользователей (in_panel=True, is_delete=False)…"
    )

    admin_chat_id = message.chat.id
    processed = 0
    extended_ok = 0
    msg_sent = 0
    msg_failed = 0
    skipped_no_subs = 0
    extend_failed = 0
    skipped_non_tg = 0

    for user in candidates:
        processed += 1
        subs_ok, had_tiers = await _add_7_sub_extend_user(user, now)
        if not had_tiers:
            skipped_no_subs += 1
        elif subs_ok:
            extended_ok += 1
            if is_telegram_chat_id(user.user_id):
                try:
                    await bot.send_message(
                        user.user_id,
                        _ADD7SUB_PROMO_TEXT,
                        reply_markup=_ADD7SUB_CONNECT_KB,
                    )
                    msg_sent += 1
                except Exception as e:
                    msg_failed += 1
                    logger.warning(
                        "add_7_sub: не отправлено user_id=%s: %s",
                        user.user_id,
                        e,
                    )
            else:
                skipped_non_tg += 1
        else:
            extend_failed += 1
            logger.warning(
                "add_7_sub: не удалось продлить подписки user_id=%s",
                user.user_id,
            )

        if processed % 1000 == 0:
            try:
                await bot.send_message(
                    admin_chat_id,
                    (
                        f"add_7_sub: обработано {processed} / {n}\n"
                        f"• Продлено успешно: {extended_ok}\n"
                        f"• Сообщений отправлено: {msg_sent}\n"
                        f"• Ошибок продления: {extend_failed}\n"
                        f"• Без дат подписки: {skipped_no_subs}"
                    ),
                )
            except Exception as notify_err:
                logger.warning(
                    "add_7_sub: не удалось отправить прогресс админу: %s",
                    notify_err,
                )

        await asyncio.sleep(0.05)

    await message.answer(
        "Готово (/add_7_sub).\n"
        f"• Всего в выборке: {n}\n"
        f"• Обработано: {processed}\n"
        f"• Продлено успешно: {extended_ok}\n"
        f"• Сообщений отправлено: {msg_sent}\n"
        f"• Ошибок отправки: {msg_failed}\n"
        f"• Ошибок продления: {extend_failed}\n"
        f"• Без дат подписки (пропуск): {skipped_no_subs}\n"
        f"• Не Telegram chat_id (продлено, без сообщения): {skipped_non_tg}"
    )
