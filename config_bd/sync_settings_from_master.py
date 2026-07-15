"""
Синхронизация identity-полей: Master API → локальная partner.db.

Не требует запущенного partner_api. Нужны:
  DATABASE_PATH=/root/database/partner.db
  MASTER_BOT_API_URL=https://...
  MASTER_BOT_API_KEY=<тот же, что PARTNER_BOT_API_KEY на мастере>

Запуск на VPS (из каталога шаблона или любого инстанса с .env / shared.env):

  export DATABASE_PATH=/root/database/partner.db
  export MASTER_BOT_API_URL=https://bot.zoomersky.online
  export MASTER_BOT_API_KEY=...

  python -m config_bd.sync_settings_from_master --dry-run
  python -m config_bd.sync_settings_from_master

Ключи можно взять из /root/partner_api/shared.env (тот же MASTER_BOT_*).
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from config import DATABASE_PATH, MASTER_BOT_API_KEY, MASTER_BOT_API_URL


def _master_url() -> str:
    url = (os.environ.get("MASTER_BOT_API_URL") or MASTER_BOT_API_URL or "").strip().rstrip("/")
    if not url:
        raise SystemExit(
            "Задайте MASTER_BOT_API_URL (например https://bot.zoomersky.online)."
        )
    return url


def _master_key() -> str:
    key = (os.environ.get("MASTER_BOT_API_KEY") or MASTER_BOT_API_KEY or "").strip()
    if not key:
        raise SystemExit(
            "Задайте MASTER_BOT_API_KEY (тот же ключ, что PARTNER_BOT_API_KEY на мастере).\n"
            "Пример: export MASTER_BOT_API_KEY=...  или пропишите в .env / shared.env"
        )
    return key


def _get_json(url: str, api_key: str) -> Dict[str, Any]:
    req = Request(
        url,
        headers={
            "X-Partner-Bot-Api-Key": api_key,
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise SystemExit(f"Master HTTP {e.code}: {detail}") from e
    except URLError as e:
        raise SystemExit(f"Не удалось достучаться до Master API: {e}") from e


def _existing_bot_ids(partner_db: Path) -> List[int]:
    if not partner_db.exists():
        raise SystemExit(f"Partner DB не найдена: {partner_db}")
    conn = sqlite3.connect(str(partner_db))
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(partner_bot_settings)")}
        missing = {
            "partner_username",
            "bot_username",
            "bot_display_name",
            "source_bot_id",
        } - cols
        if missing:
            raise SystemExit(
                f"Нет колонок: {', '.join(sorted(missing))}. "
                "Сначала: python -m config_bd.migrate_partner_settings_identity"
            )
        rows = conn.execute(
            "SELECT bot_id FROM partner_bot_settings ORDER BY bot_id"
        ).fetchall()
        return [int(r[0]) for r in rows]
    finally:
        conn.close()


def _fetch_master_items(bot_ids: List[int]) -> List[Dict[str, Any]]:
    base = _master_url()
    key = _master_key()
    # при большом числе id забираем весь список с мастера
    if len(bot_ids) <= 80:
        qs = urlencode({"ids": ",".join(str(i) for i in bot_ids)})
        url = f"{base}/api/partner/applications/settings?{qs}"
    else:
        url = f"{base}/api/partner/applications/settings"
    data = _get_json(url, key)
    items = data.get("items")
    if not isinstance(items, list):
        raise SystemExit(f"Неожиданный ответ master: {data!r}")
    return items


def _apply(
    partner_db: Path,
    items: List[Dict[str, Any]],
    existing: set[int],
    *,
    dry_run: bool,
) -> int:
    updates: list[tuple] = []
    for raw in items:
        try:
            bot_id = int(raw["bot_id"])
        except (KeyError, TypeError, ValueError):
            continue
        if bot_id not in existing:
            continue

        partner_username = raw.get("partner_username")
        if isinstance(partner_username, str):
            partner_username = partner_username.lstrip("@") or None
        bot_username = raw.get("bot_username")
        if isinstance(bot_username, str):
            bot_username = bot_username.lstrip("@") or None
        bot_display_name = raw.get("bot_display_name")
        if isinstance(bot_display_name, str):
            bot_display_name = bot_display_name.strip() or None
        source_bot_id = raw.get("source_bot_id")
        if source_bot_id is not None:
            try:
                source_bot_id = int(source_bot_id)
            except (TypeError, ValueError):
                source_bot_id = None

        print(
            f"  bot_id={bot_id}: partner=@{partner_username or '-'} "
            f"bot=@{bot_username or '-'} name={bot_display_name!r} source={source_bot_id}"
        )
        updates.append(
            (partner_username, bot_username, bot_display_name, source_bot_id, bot_id)
        )

    if dry_run:
        print(f"DRY-RUN: было бы обновлено {len(updates)} строк.")
        return 0

    if not updates:
        print("Нечего обновлять.")
        return 0

    conn = sqlite3.connect(str(partner_db))
    try:
        conn.executemany(
            """
            UPDATE partner_bot_settings
            SET partner_username = COALESCE(?, partner_username),
                bot_username = COALESCE(?, bot_username),
                bot_display_name = COALESCE(?, bot_display_name),
                source_bot_id = COALESCE(?, source_bot_id)
            WHERE bot_id = ?
            """,
            updates,
        )
        conn.commit()
        print(f"OK: обновлено {len(updates)} строк.")
        return len(updates)
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pull identity settings: Master API → local partner.db"
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--partner-db",
        type=Path,
        default=DATABASE_PATH,
        help=f"Путь к partner.db (по умолчанию DATABASE_PATH={DATABASE_PATH})",
    )
    args = parser.parse_args()

    partner_db: Path = args.partner_db
    existing_list = _existing_bot_ids(partner_db)
    existing = set(existing_list)
    print(f"Partner DB: {partner_db} ({len(existing_list)} bot_id)")
    if not existing_list:
        print("В partner_bot_settings нет записей — нечего обновлять.")
        return

    items = _fetch_master_items(existing_list)
    print(f"Ответ master: {len(items)} заявок")
    _apply(partner_db, items, existing, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
