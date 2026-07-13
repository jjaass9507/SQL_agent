"""驗證 `alembic upgrade head` 在 SQLite 檔案上可成功執行並建立全部資料表。"""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

EXPECTED_TABLES = {
    "users",
    "sessions",
    "messages",
    "schema_versions",
    "outputs",
    "jobs",
    "change_requests",
    "activity_log",
    "app_settings",
    "alembic_version",
}


def test_alembic_upgrade_head_creates_all_tables(tmp_path):
    db_path = tmp_path / "alembic_upgrade_test.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{db_path}"}

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr

    con = sqlite3.connect(db_path)
    try:
        rows = con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    finally:
        con.close()
    table_names = {row[0] for row in rows}

    assert EXPECTED_TABLES.issubset(table_names)
