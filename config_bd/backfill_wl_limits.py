"""
Скрипт для существующей БД partner.db:

1. Добавляет поля тарификации Антиглушилка (trafic_wl, limit_wl, field_bool_2)
   и таблицу wl_traffic_meta.
2. trafic_wl = 0 у всех пользователей (все bot_id).
3. limit_wl = 10 GB, если хотя бы одна PRO-подписка (3/5/10 устройств) заканчивается
   сегодня (МСК) или позже; иначе limit_wl = 0.

Запуск из корня проекта (DATABASE_PATH из .env):
  python -m config_bd.backfill_wl_limits
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from sqlalchemy import text

from config_bd.models import engine
from wl_traffic.constants import WL_GB_PER_MONTH, WL_TIMEZONE

_ACTIVE_LIMIT_GB = float(WL_GB_PER_MONTH)

_ACTIVE_SUB_WHERE = """
    (
        (subscription_end_date IS NOT NULL AND subscription_end_date >= :today_start)
        OR (subscription_3_end_date IS NOT NULL AND subscription_3_end_date >= :today_start)
        OR (subscription_10_end_date IS NOT NULL AND subscription_10_end_date >= :today_start)
    )
"""


def _today_start_moscow() -> datetime:
    now = datetime.now(WL_TIMEZONE)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).replace(tzinfo=None)


async def _ensure_wl_columns(conn) -> None:
    result = await conn.execute(text("PRAGMA table_info(users)"))
    existing = {row[1] for row in result.fetchall()}
    for name, col_type in (
        ("trafic_wl", "FLOAT DEFAULT 0"),
        ("limit_wl", "FLOAT DEFAULT 0"),
        ("field_bool_2", "BOOLEAN DEFAULT FALSE"),
    ):
        if name not in existing:
            await conn.execute(text(f"ALTER TABLE users ADD COLUMN {name} {col_type}"))


async def _ensure_wl_meta(conn) -> None:
    await conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS wl_traffic_meta (
                id INTEGER PRIMARY KEY,
                last_closed_date DATE
            )
            """
        )
    )
    await conn.execute(
        text(
            "INSERT OR IGNORE INTO wl_traffic_meta (id, last_closed_date) VALUES (1, NULL)"
        )
    )


async def backfill() -> None:
    today_start = _today_start_moscow()
    today_label = today_start.date().isoformat()

    async with engine.begin() as conn:
        table_check = await conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
        )
        if not table_check.fetchone():
            print("ERROR: таблица users не найдена.")
            return

        await _ensure_wl_columns(conn)
        await _ensure_wl_meta(conn)

        await conn.execute(text("UPDATE users SET trafic_wl = 0"))

        await conn.execute(
            text(
                f"""
                UPDATE users
                SET limit_wl = :active_limit
                WHERE {_ACTIVE_SUB_WHERE}
                """
            ),
            {"active_limit": _ACTIVE_LIMIT_GB, "today_start": today_start},
        )

        await conn.execute(
            text(
                f"""
                UPDATE users
                SET limit_wl = 0
                WHERE NOT {_ACTIVE_SUB_WHERE}
                """
            ),
            {"today_start": today_start},
        )

        total = (await conn.execute(text("SELECT COUNT(*) FROM users"))).scalar_one()
        active = (
            await conn.execute(
                text(f"SELECT COUNT(*) FROM users WHERE {_ACTIVE_SUB_WHERE}"),
                {"today_start": today_start},
            )
        ).scalar_one()
        inactive = total - active

        bot_rows = (
            await conn.execute(
                text(
                    f"""
                    SELECT bot_id, COUNT(*) AS total,
                           SUM(CASE WHEN {_ACTIVE_SUB_WHERE} THEN 1 ELSE 0 END) AS active
                    FROM users
                    GROUP BY bot_id
                    ORDER BY bot_id
                    """
                ),
                {"today_start": today_start},
            )
        ).fetchall()

    print(
        f"OK: backfill WL limits для всех bot_id (today={today_label} MSK, "
        f"active_limit={_ACTIVE_LIMIT_GB:g} GB).\n"
        f"  users total={total}\n"
        f"  limit_wl={_ACTIVE_LIMIT_GB:g} GB (active PRO sub): {active}\n"
        f"  limit_wl=0 (no/expired PRO sub): {inactive}\n"
        f"  trafic_wl=0: all"
    )
    if bot_rows:
        print("\n  По bot_id:")
        for bot_id, bot_total, bot_active in bot_rows:
            print(
                f"    bot_id={bot_id}: total={bot_total}, "
                f"active={bot_active}, inactive={bot_total - bot_active}"
            )


def main() -> None:
    asyncio.run(backfill())


if __name__ == "__main__":
    main()
