"""
Переносит identity-поля из мастер-бота (PostgreSQL, partner_bot_applications)
в общую БД партнёрских ботов (SQLite, partner_bot_settings).

Обновляются только строки, у которых bot_id уже есть в partner_bot_settings
(сопоставление: partner_bot_applications.id == partner_bot_settings.bot_id).

Поля:
  partner_username, bot_username, bot_display_name, source_bot_id

Запуск из корня partner_bot (нужен доступ к PostgreSQL мастера и к partner.db):

  set DATABASE_PATH=C:\\path\\to\\partner.db
  set MASTER_DATABASE_URL=postgresql://user:pass@host:5432/dbname
  python -m config_bd.sync_settings_from_master

  # dry-run (только показать, что будет обновлено):
  python -m config_bd.sync_settings_from_master --dry-run

URL мастера также можно собрать из POSTGRES_* как в Zoomer:
  POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB, POSTGRES_HOST, POSTGRES_PORT
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote_plus

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from config import DATABASE_PATH


def _build_master_url(cli_url: Optional[str]) -> str:
    if cli_url:
        return cli_url
    env_url = (os.environ.get("MASTER_DATABASE_URL") or "").strip()
    if env_url:
        return env_url

    user = os.environ.get("POSTGRES_USER")
    password = os.environ.get("POSTGRES_PASSWORD")
    db = os.environ.get("POSTGRES_DB")
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5432")
    if user and password and db:
        return (
            f"postgresql://{quote_plus(user)}:{quote_plus(password)}"
            f"@{host}:{port}/{db}"
        )
    raise SystemExit(
        "Укажите MASTER_DATABASE_URL или POSTGRES_USER/POSTGRES_PASSWORD/POSTGRES_DB "
        "(опционально --master-url)."
    )


def _partner_bot_ids(partner_db: Path) -> List[int]:
    if not partner_db.exists():
        raise SystemExit(f"Partner DB не найдена: {partner_db}")
    conn = sqlite3.connect(str(partner_db))
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(partner_bot_settings)")}
        missing = {"partner_username", "bot_username", "bot_display_name", "source_bot_id"} - cols
        if missing:
            raise SystemExit(
                f"В partner_bot_settings нет колонок: {', '.join(sorted(missing))}. "
                "Сначала: python -m config_bd.migrate_partner_settings_identity"
            )
        rows = conn.execute("SELECT bot_id FROM partner_bot_settings ORDER BY bot_id").fetchall()
        return [int(r[0]) for r in rows]
    finally:
        conn.close()


def _fetch_master_rows(master_url: str, bot_ids: List[int]) -> Dict[int, Dict[str, Any]]:
    try:
        import psycopg2
        import psycopg2.extras
    except ImportError as e:
        raise SystemExit(
            "Нужен пакет psycopg2 (или psycopg2-binary): pip install psycopg2-binary"
        ) from e

    if not bot_ids:
        return {}

    # asyncpg URL → libpq
    dsn = master_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, partner_username, bot_username, bot_display_name, source_bot_id
                FROM partner_bot_applications
                WHERE id = ANY(%s)
                """,
                (bot_ids,),
            )
            return {int(row["id"]): dict(row) for row in cur.fetchall()}
    finally:
        conn.close()


def _apply_updates(
    partner_db: Path,
    updates: List[Tuple[int, Optional[str], Optional[str], Optional[str], Optional[int]]],
    *,
    dry_run: bool,
) -> int:
    if dry_run or not updates:
        return 0
    conn = sqlite3.connect(str(partner_db))
    try:
        conn.executemany(
            """
            UPDATE partner_bot_settings
            SET partner_username = ?,
                bot_username = ?,
                bot_display_name = ?,
                source_bot_id = ?
            WHERE bot_id = ?
            """,
            [
                (partner_username, bot_username, bot_display_name, source_bot_id, bot_id)
                for bot_id, partner_username, bot_username, bot_display_name, source_bot_id in updates
            ],
        )
        conn.commit()
        return conn.total_changes
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync identity fields from master → partner.db")
    parser.add_argument(
        "--partner-db",
        type=Path,
        default=DATABASE_PATH,
        help=f"Путь к partner.db (по умолчанию DATABASE_PATH={DATABASE_PATH})",
    )
    parser.add_argument("--master-url", type=str, default=None, help="postgresql://... URL мастера")
    parser.add_argument("--dry-run", action="store_true", help="Только показать план, без UPDATE")
    args = parser.parse_args()

    partner_db: Path = args.partner_db
    master_url = _build_master_url(args.master_url)
    bot_ids = _partner_bot_ids(partner_db)
    print(f"Partner DB: {partner_db} ({len(bot_ids)} bot_id)")
    if not bot_ids:
        print("Нечего обновлять.")
        return

    master_rows = _fetch_master_rows(master_url, bot_ids)
    print(f"Найдено в master partner_bot_applications: {len(master_rows)}")

    updates: List[Tuple[int, Optional[str], Optional[str], Optional[str], Optional[int]]] = []
    skipped_no_master = 0
    for bot_id in bot_ids:
        row = master_rows.get(bot_id)
        if not row:
            skipped_no_master += 1
            continue
        partner_username = row.get("partner_username")
        bot_username = row.get("bot_username")
        if isinstance(bot_username, str):
            bot_username = bot_username.lstrip("@") or None
        bot_display_name = row.get("bot_display_name")
        source_bot_id = row.get("source_bot_id")
        updates.append((bot_id, partner_username, bot_username, bot_display_name, source_bot_id))
        print(
            f"  bot_id={bot_id}: partner=@{partner_username or '-'} "
            f"bot=@{bot_username or '-'} name={bot_display_name!r} source={source_bot_id}"
        )

    if skipped_no_master:
        print(f"Пропущено (нет в master): {skipped_no_master}")

    if args.dry_run:
        print(f"DRY-RUN: было бы обновлено {len(updates)} строк.")
        return

    changed = _apply_updates(partner_db, updates, dry_run=False)
    print(f"OK: обновлено строк={len(updates)} (sqlite changes≈{changed})")


if __name__ == "__main__":
    main()
