from __future__ import annotations

import sqlite3
from pathlib import Path


DB_PATH = Path("instance") / "x_archive.db"


def main() -> None:
    if not DB_PATH.exists():
        print("NO_STAMP")
        return

    connection = sqlite3.connect(DB_PATH)
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        has_users = "users" in tables
        has_alembic = "alembic_version" in tables
        alembic_rows = connection.execute("SELECT COUNT(*) FROM alembic_version").fetchone()[0] if has_alembic else 0
        needs_stamp = has_users and (not has_alembic or alembic_rows == 0)
        print("STAMP_HEAD" if needs_stamp else "NO_STAMP")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
