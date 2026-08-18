"""Ежедневное накопление trafic_wl с WL-ноды (02:57 МСК)."""
from __future__ import annotations

from collections import defaultdict

from bot import sql, x3
from config import BOT_ID
from logging_config import logger
from wl_traffic.service import (
    billing_uid_from_panel_username,
    fetch_wl_traffic_gb_for_day,
    wl_traffic_day,
)


async def accumulate_wl_traffic_cron() -> None:
    try:
        day = wl_traffic_day()
        last_closed = await sql.get_wl_traffic_last_closed_date()
        if last_closed is not None and last_closed >= day:
            logger.info(
                f"accumulate_wl_traffic: WL-день {day.isoformat()} уже закрыт "
                f"(last_closed={last_closed.isoformat()}), пропуск"
            )
            return

        by_username, by_uuid = await fetch_wl_traffic_gb_for_day(x3, day)
        if not by_username and not by_uuid:
            logger.warning(
                f"accumulate_wl_traffic: нет данных legacy за {day.isoformat()}, пропуск"
            )
            return

        gb_by_billing_uid: dict[int, float] = defaultdict(float)
        skipped = 0

        for username, gb in by_username.items():
            if gb <= 0:
                continue
            billing_uid = billing_uid_from_panel_username(username, bot_id=BOT_ID)
            if billing_uid is None:
                skipped += 1
                continue
            gb_by_billing_uid[billing_uid] += gb

        updated = 0
        for billing_uid, gb in gb_by_billing_uid.items():
            await sql.add_trafic_wl(billing_uid, round(gb, 2))
            updated += 1

        await sql.set_wl_traffic_last_closed_date(day)

        logger.info(
            f"accumulate_wl_traffic: bot_id={BOT_ID} день={day.isoformat()} закрыт, "
            f"обновлено={updated} пропущено_username={skipped} "
            f"bulk={len(by_username)} username / {len(by_uuid)} uuid"
        )
    except Exception as e:
        logger.error(f"accumulate_wl_traffic_cron: {e}")
