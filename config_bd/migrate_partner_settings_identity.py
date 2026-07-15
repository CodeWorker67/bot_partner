"""
Добавляет в partner_bot_settings поля идентичности бота/партнёра:
- partner_username VARCHAR(255)
- bot_username VARCHAR(255)
- bot_display_name VARCHAR(255)
- source_bot_id BIGINT

Путь БД берётся из config.DATABASE_PATH (как в models.py).

Запуск из корня partner_bot:
  python -m config_bd.migrate_partner_settings_identity

Опционально:
  DATABASE_PATH=/root/database/partner.db python -m config_bd.migrate_partner_settings_identity
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from sqlalchemy import text

from config_bd.models import DATABASE_PATH, engine

_COLUMNS = (
    ("partner_username", "VARCHAR(255)"),
    ("bot_username", "VARCHAR(255)"),
    ("bot_display_name", "VARCHAR(255)"),
    ("source_bot_id", "BIGINT"),
)


async def _existing_columns(conn) -> set[str]:
    result = await conn.execute(text("PRAGMA table_info(partner_bot_settings)"))
    return {row[1] for row in result.fetchall()}


async def migrate() -> None:
    print(f"DB: {DATABASE_PATH}")
    async with engine.begin() as conn:
        tables = await conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='partner_bot_settings'")
        )
        if not tables.fetchone():
            raise SystemExit("Таблица partner_bot_settings не найдена — сначала запустите бота или init.")

        existing = await _existing_columns(conn)
        added: list[str] = []
        for name, col_type in _COLUMNS:
            if name in existing:
                continue
            await conn.execute(text(f"ALTER TABLE partner_bot_settings ADD COLUMN {name} {col_type}"))
            added.append(name)

    if added:
        print(f"OK: добавлены колонки: {', '.join(added)}")
    else:
        print("OK: все колонки уже существуют.")


def main() -> None:
    asyncio.run(migrate())


if __name__ == "__main__":
    main()
