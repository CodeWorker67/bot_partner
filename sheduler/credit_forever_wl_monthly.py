"""1-го числа каждого месяца: +10 GB лимита для тарифа Навсегда."""
from __future__ import annotations

import asyncio

from aiogram import Bot
from aiogram.exceptions import TelegramNetworkError

from bot import sql, x3
from logging_config import logger
from wl_traffic.constants import WL_GB_PER_MONTH
from wl_traffic.service import (
    get_wl_used_gb_for_user,
    restore_pro_squads_if_under_limit,
)
from wl_traffic.texts import format_wl_forever_monthly_credit


async def credit_forever_wl_monthly_cron(bot: Bot) -> None:
    """
    Начисляет +10 GB к limit_wl активным пользователям тарифа Навсегда
    (subscription_end_date >= 2030-01-01 и подписка ещё не истекла).
    """
    try:
        users = await sql.select_forever_active_users()
        if not users:
            logger.info("credit_forever_wl_monthly: нет активных пользователей Навсегда")
            return

        credited = 0
        for billing_uid in users:
            try:
                await sql.add_wl_limit(billing_uid, float(WL_GB_PER_MONTH))
                trafic_wl, limit_wl = await sql.get_wl_limits(billing_uid)
                used_gb = await get_wl_used_gb_for_user(x3, billing_uid, trafic_wl)
                await restore_pro_squads_if_under_limit(x3, billing_uid, used_gb, limit_wl)

                if billing_uid > 0:
                    for attempt in range(3):
                        try:
                            await bot.send_message(
                                chat_id=billing_uid,
                                text=format_wl_forever_monthly_credit(),
                                parse_mode="HTML",
                            )
                            break
                        except TelegramNetworkError:
                            if attempt < 2:
                                await asyncio.sleep(1.5 * (attempt + 1))
                                continue
                            raise
                        except Exception as e:
                            logger.warning(
                                f"credit_forever_wl_monthly: push uid={billing_uid}: {e}"
                            )
                            break

                credited += 1
            except Exception as e:
                logger.error(f"credit_forever_wl_monthly: uid={billing_uid}: {e}")

        logger.info(
            f"credit_forever_wl_monthly: начислено +{WL_GB_PER_MONTH:g} GB "
            f"пользователям: {credited}/{len(users)}"
        )
    except Exception as e:
        logger.error(f"credit_forever_wl_monthly_cron: {e}")
