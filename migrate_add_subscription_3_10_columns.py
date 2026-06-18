"""
Adds subscription_3 / subscription_10 columns to users if missing.

SQLite ALTER TABLE applies to all rows at once; nullable columns are NULL for
existing users.

Run from project root: python migrate_add_subscription_3_10_columns.py
"""
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "config_bd" / "speedgamer.db"

MIGRATIONS = [
    ("subscription_3_end_date", "DATETIME"),
    ("subscription_10_end_date", "DATETIME"),
    ("subscribtion_3", "TEXT"),
    ("subscribtion_10", "TEXT"),
]


def main() -> None:
    if not DB_PATH.is_file():
        raise SystemExit(f"Database not found: {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute("PRAGMA table_info(users)")
        existing = {row[1] for row in cur.fetchall()}

        for name, coldef in MIGRATIONS:
            if name in existing:
                print(f"skip (exists): {name}")
                continue
            sql = f'ALTER TABLE users ADD COLUMN "{name}" {coldef}'
            conn.execute(sql)
            print(f"ok: {name}")
        conn.commit()
    finally:
        conn.close()

    print("Done.")


if __name__ == "__main__":
    main()
