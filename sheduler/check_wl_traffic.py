"""Фоновая проверка лимита трафика Антиглушилка (каждые 30 минут)."""
from __future__ import annotations

import asyncio

from aiogram import Bot
from aiogram.exceptions import TelegramNetworkError

from bot import sql, x3
from config import CHECKER_ID
from keyboard import keyboard_wl_traffic_tariffs
from logging_config import logger
from wl_traffic.service import (
    all_pro_users_on_limited_squad,
    any_pro_user_on_limited_squad,
    compute_wl_used_gb,
    fetch_all_pro_panel_users,
    fetch_wl_traffic_gb_for_day,
    is_wl_check_skip_window,
    reassign_all_pro_to_active,
    reassign_all_pro_to_limited,
    should_send_wl_low_traffic_warning,
    wl_day_gb_for_panel_users,
    wl_traffic_day,
)
from wl_traffic.texts import (
    format_wl_checker_exceeded_report,
    format_wl_limit_exceeded,
    format_wl_traffic_low_warning,
)

_SQUAD_REASSIGN_DELAY_SEC = 10


async def _send_wl_limit_push(
    bot: Bot, billing_uid: int, limit_gb: float, used_gb: float,
) -> None:
    text = format_wl_limit_exceeded(limit_gb, used_gb)
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            await bot.send_message(
                chat_id=billing_uid,
                text=text,
                parse_mode="HTML",
                reply_markup=keyboard_wl_traffic_tariffs(back_callback="back_to_main"),
            )
            return
        except TelegramNetworkError as e:
            last_err = e
            if attempt < 2:
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            raise
    if last_err:
        raise last_err


async def _send_wl_low_traffic_push(
    bot: Bot, billing_uid: int, limit_gb: float, used_gb: float,
) -> None:
    text = format_wl_traffic_low_warning(limit_gb, used_gb)
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            await bot.send_message(
                chat_id=billing_uid,
                text=text,
                parse_mode="HTML",
                reply_markup=keyboard_wl_traffic_tariffs(back_callback="back_to_main"),
            )
            return
        except TelegramNetworkError as e:
            last_err = e
            if attempt < 2:
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            raise
    if last_err:
        raise last_err


async def _send_checker_exceeded_report(
    bot: Bot,
    exceeded: list[tuple[int, float, float]],
) -> None:
    if CHECKER_ID is None or not exceeded:
        return
    try:
        await bot.send_message(
            chat_id=CHECKER_ID,
            text=format_wl_checker_exceeded_report(exceeded),
        )
    except Exception as e:
        logger.warning(f"check_wl_traffic: не удалось отправить отчёт CHECKER_ID: {e}")


async def check_wl_traffic_cron(bot: Bot) -> None:
    try:
        if is_wl_check_skip_window():
            logger.info("check_wl_traffic: окно 02:57–03:05 МСК, пропуск цикла")
            return

        users = await sql.select_users_active_subscription()
        if not users:
            return

        with_limit = sum(1 for _, _, limit_wl, _ in users if limit_wl > 0)
        if with_limit == 0:
            return

        day = wl_traffic_day()
        traffic_by_username, traffic_by_uuid = await fetch_wl_traffic_gb_for_day(
            x3, day, retries=1,
        )
        if not traffic_by_username and not traffic_by_uuid:
            logger.warning(
                f"check_wl_traffic: bulk-трафик WL-ноды за {day.isoformat()} не получен, пропуск"
            )
            return

        logger.info(
            f"check_wl_traffic: день={day.isoformat()} проверка {with_limit} пользователей, "
            f"bulk: {len(traffic_by_username)} username / {len(traffic_by_uuid)} uuid"
        )

        pending_limited: list[tuple[int, float, float]] = []
        pending_active: list[tuple[int, float, float]] = []

        for billing_uid, trafic_db, limit_wl, low_warning_sent in users:
            if limit_wl <= 0:
                continue

            try:
                panel_users = await fetch_all_pro_panel_users(x3, billing_uid)
                if not panel_users:
                    logger.warning(
                        f"check_wl_traffic: uid={billing_uid} — нет PRO-пользователей в панели"
                    )
                    continue

                day_gb = wl_day_gb_for_panel_users(
                    panel_users, traffic_by_username, traffic_by_uuid,
                )
                used_gb = compute_wl_used_gb(trafic_db, day_gb)

                logger.info(
                    f"check_wl_traffic: uid={billing_uid} "
                    f"trafic_wl={float(trafic_db or 0):.2f} day={day_gb:.2f} "
                    f"used={used_gb:.2f} limit={limit_wl:.2f} GB "
                    f"panel_users={len(panel_users)}"
                )

                if used_gb > limit_wl:
                    if all_pro_users_on_limited_squad(panel_users):
                        logger.info(
                            f"check_wl_traffic: uid={billing_uid} все PRO уже на limited squad, пропуск"
                        )
                        continue

                    try:
                        await _send_wl_limit_push(bot, billing_uid, limit_wl, used_gb)
                    except TelegramNetworkError as e:
                        logger.warning(
                            f"check_wl_traffic: push uid={billing_uid} — сеть Telegram: {e}"
                        )
                    except Exception as e:
                        logger.error(f"check_wl_traffic: push uid={billing_uid}: {e}")

                    pending_limited.append((billing_uid, limit_wl, used_gb))
                elif (
                    not low_warning_sent
                    and should_send_wl_low_traffic_warning(used_gb, limit_wl)
                ):
                    try:
                        await _send_wl_low_traffic_push(bot, billing_uid, limit_wl, used_gb)
                        await sql.update_field_bool_2(billing_uid, True)
                        logger.info(
                            f"check_wl_traffic: uid={billing_uid} push <1 GB "
                            f"({used_gb:.2f}/{limit_wl:.2f} GB), field_bool_2=True"
                        )
                    except TelegramNetworkError as e:
                        logger.warning(
                            f"check_wl_traffic: low-traffic push uid={billing_uid} — сеть: {e}"
                        )
                    except Exception as e:
                        logger.error(
                            f"check_wl_traffic: low-traffic push uid={billing_uid}: {e}"
                        )
                elif any_pro_user_on_limited_squad(panel_users):
                    pending_active.append((billing_uid, limit_wl, used_gb))

            except Exception as e:
                logger.error(f"check_wl_traffic: uid={billing_uid}: {e}")

        checker_limited: list[tuple[int, float, float]] = []

        if pending_limited:
            logger.info(
                f"check_wl_traffic: {len(pending_limited)} push, "
                f"пауза {_SQUAD_REASSIGN_DELAY_SEC} сек перед limited squad"
            )
            await asyncio.sleep(_SQUAD_REASSIGN_DELAY_SEC)

            for billing_uid, limit_gb, used_gb in pending_limited:
                try:
                    n = await reassign_all_pro_to_limited(x3, billing_uid)
                    if n == 0:
                        logger.warning(
                            f"check_wl_traffic: не удалось переназначить squad uid={billing_uid}"
                        )
                        continue
                    checker_limited.append((billing_uid, used_gb, limit_gb))
                    logger.info(
                        f"check_wl_traffic: лимит превышен uid={billing_uid} "
                        f"({used_gb:.2f}/{limit_gb:.2f} GB), squad -> limited ({n} акк.)"
                    )
                except Exception as e:
                    logger.error(f"check_wl_traffic: squad uid={billing_uid}: {e}")

        await _send_checker_exceeded_report(bot, checker_limited)

        for billing_uid, limit_gb, used_gb in pending_active:
            try:
                n = await reassign_all_pro_to_active(x3, billing_uid)
                if n == 0:
                    logger.warning(
                        f"check_wl_traffic: не удалось вернуть active squad uid={billing_uid}"
                    )
                    continue
                logger.info(
                    f"check_wl_traffic: лимит в норме uid={billing_uid} "
                    f"({used_gb:.2f}/{limit_gb:.2f} GB), squad -> active ({n} акк.)"
                )
            except Exception as e:
                logger.error(f"check_wl_traffic: active squad uid={billing_uid}: {e}")

    except Exception as e:
        logger.error(f"check_wl_traffic_cron: {e}")
