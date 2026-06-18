"""
Миграция таблицы users — поля партнёрской программы:
- partner VARCHAR(100) NULL
- partner_balance INTEGER DEFAULT 0
- partner_pay INTEGER DEFAULT 0
- partner_flag BOOLEAN DEFAULT FALSE

Запуск из корня проекта:
  python -m config_bd.migrate_users_partner_fields
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from sqlalchemy import text

from config_bd.models import engine

_COLUMNS = (
    ("partner", "VARCHAR(100)"),
    ("partner_balance", "INTEGER DEFAULT 0"),
    ("partner_pay", "INTEGER DEFAULT 0"),
    ("partner_flag", "BOOLEAN DEFAULT FALSE"),
)


async def _existing_columns(conn) -> set[str]:
    result = await conn.execute(text("PRAGMA table_info(users)"))
    return {row[1] for row in result.fetchall()}


async def migrate() -> None:
    async with engine.begin() as conn:
        existing = await _existing_columns(conn)
        for name, col_type in _COLUMNS:
            if name in existing:
                continue
            await conn.execute(text(f"ALTER TABLE users ADD COLUMN {name} {col_type}"))

        await conn.execute(
            text("UPDATE users SET partner_balance = 0 WHERE partner_balance IS NULL")
        )
        await conn.execute(
            text("UPDATE users SET partner_pay = 0 WHERE partner_pay IS NULL")
        )
        await conn.execute(
            text("UPDATE users SET partner_flag = 0 WHERE partner_flag IS NULL")
        )

    print("OK: users partner, partner_balance, partner_pay, partner_flag.")


def main() -> None:
    asyncio.run(migrate())


if __name__ == "__main__":
    main()
