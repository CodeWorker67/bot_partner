"""
Adds new columns to users if missing.
Run from project root: python migrate_add_user_fields.py
"""
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "config_bd" / "speedgamer.db"

# (имя колонки, SQL-фрагмент после ADD COLUMN)
MIGRATIONS = [
    ("subscribtion", "TEXT"),
    ("white_subscription", "TEXT"),
    ("email", "TEXT"),
    ("password", "TEXT"),
    ("activation_pass", "TEXT"),
    ("field_str_1", "TEXT"),
    ("field_str_2", "TEXT"),
    ("field_str_3", "TEXT"),
    ("field_bool_1", "INTEGER NOT NULL DEFAULT 0"),
    ("field_bool_2", "INTEGER NOT NULL DEFAULT 0"),
    ("field_bool_3", "INTEGER NOT NULL DEFAULT 0"),
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
