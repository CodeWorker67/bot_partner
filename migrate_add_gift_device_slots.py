"""
Adds gifts.device_slots (число устройств подарочного тарифа).

Run from project root: python migrate_add_gift_device_slots.py
"""
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "config_bd" / "speedgamer.db"


def main() -> None:
    if not DB_PATH.is_file():
        raise SystemExit(f"Database not found: {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute("PRAGMA table_info(gifts)")
        existing = {row[1] for row in cur.fetchall()}
        if "device_slots" in existing:
            print("skip (exists): device_slots")
        else:
            conn.execute("ALTER TABLE gifts ADD COLUMN device_slots INTEGER DEFAULT 5")
            print("ok: device_slots")
        conn.commit()
    finally:
        conn.close()

    print("Done.")


if __name__ == "__main__":
    main()
