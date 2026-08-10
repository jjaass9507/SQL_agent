"""部署用的資料庫工具：狀態檢查與遷移前備份。

為什麼是 Python 而不是寫在部署腳本裡：解析 `DATABASE_URL`（`sqlite+aiosqlite://`
與 `postgresql+asyncpg://` 兩種格式）、判斷該用複製檔案還是 `pg_dump`，這些邏輯
在 PowerShell 裡只能靠字串處理，而且沒辦法在 Linux 上驗證。放在這裡可以直接沿用
`app.config`（環境變數的唯一入口）與 SQLAlchemy 的 URL 解析，兩邊部署腳本共用。

    python scripts/deploy_db.py check                  # 目前版本、是否有待跑的遷移
    python scripts/deploy_db.py backup --out-dir backups

`check` 的離開碼有意義，供部署腳本判斷：
    0 = 已是最新，沒有待跑的遷移
    1 = 有待跑的遷移（正常情況，不是錯誤）
    2 = 連不上資料庫或設定有問題
"""

import argparse
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.engine.url import URL  # noqa: E402

from app.config import get_settings  # noqa: E402

_ROOT = Path(__file__).resolve().parent.parent

EXIT_UP_TO_DATE = 0
EXIT_PENDING = 1
EXIT_ERROR = 2


def _sync_url(url: URL) -> URL:
    """把 async driver 換成同步的——備份與版本查詢都不需要 async。"""
    driver_swap = {"aiosqlite": "pysqlite", "asyncpg": "psycopg2"}
    backend = url.get_backend_name()
    driver = url.get_driver_name()
    if driver in driver_swap:
        return url.set(drivername=f"{backend}+{driver_swap[driver]}")
    return url


def _alembic_revisions(url: URL) -> tuple[str | None, str | None]:
    """回傳（資料庫目前的版本, 遷移檔案的最新版本）。"""
    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_ROOT / "alembic"))
    head = ScriptDirectory.from_config(config).get_current_head()

    engine = create_engine(_sync_url(url))
    try:
        with engine.connect() as conn:
            current = MigrationContext.configure(conn).get_current_revision()
    finally:
        engine.dispose()
    return current, head


def _sqlite_path(url: URL) -> Path:
    if not url.database:
        raise ValueError("SQLite 的 DATABASE_URL 沒有指定檔案路徑")
    path = Path(url.database)
    return path if path.is_absolute() else (_ROOT / path).resolve()


def _backup_sqlite(url: URL, out_dir: Path, stamp: str) -> tuple[Path, str]:
    source = _sqlite_path(url)
    if not source.exists():
        raise FileNotFoundError(f"找不到 SQLite 資料庫檔案：{source}")
    target = out_dir / f"{source.stem}-{stamp}{source.suffix or '.db'}"
    # 用 SQLite 自己的 backup API 而不是複製檔案：複製正在寫入的檔案可能拿到
    # 不一致的快照（WAL 還沒 checkpoint），backup API 會處理這件事。
    import sqlite3

    with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:
        src.backup(dst)
    return target, f'Copy-Item "{target}" "{source}" -Force   # 還原前請先停掉服務'


def _backup_postgres(url: URL, out_dir: Path, stamp: str, pg_dump: str) -> tuple[Path, str]:
    exe = shutil.which(pg_dump) or pg_dump
    if not Path(exe).exists() and shutil.which(pg_dump) is None:
        raise FileNotFoundError(
            f"找不到 {pg_dump}。PostgreSQL 的 client 工具沒裝或不在 PATH，"
            f"可用 --pg-dump 指定完整路徑"
        )
    target = out_dir / f"{url.database}-{stamp}.dump"
    cmd = [exe, "--format=custom", "--file", str(target), "--dbname", str(url.database)]
    if url.host:
        cmd += ["--host", url.host]
    if url.port:
        cmd += ["--port", str(url.port)]
    if url.username:
        cmd += ["--username", url.username]

    env = dict(os.environ)
    if url.password:
        env["PGPASSWORD"] = url.password
    result = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"pg_dump 失敗（exit {result.returncode}）：{result.stderr.strip()}")

    # 還原指令要給 libpq 用，不能帶 SQLAlchemy 的 driver 後綴（`+asyncpg` pg_restore
    # 看不懂）。密碼一律不印進部署 log，改用 PGPASSWORD 提供。
    # 用 URL.create 重建而不是 url.set(password=None)——`set()` 把 None 當成
    # 「這個欄位不要改」，密碼會留著並被 render 成字面上的 `***`，貼上去必定失敗。
    libpq_url = URL.create(
        drivername="postgresql",
        username=url.username,
        host=url.host,
        port=url.port,
        database=url.database,
    ).render_as_string()
    restore = (
        f'pg_restore --clean --if-exists --dbname "{libpq_url}" "{target}"'
        f"\n               （需要密碼時先設 PGPASSWORD 環境變數）"
    )
    return target, restore


def cmd_check(args: argparse.Namespace) -> int:
    url = make_url(get_settings().database_url)
    print(f"DATABASE_URL : {url.render_as_string(hide_password=True)}")
    print(f"backend      : {url.get_backend_name()}")

    try:
        current, head = _alembic_revisions(url)
    except Exception as exc:  # noqa: BLE001 —— 這裡就是要把任何連線/設定問題轉成訊息
        print(f"無法讀取資料庫版本：{exc}", file=sys.stderr)
        return EXIT_ERROR

    print(f"目前版本     : {current or '(尚未建立任何資料表)'}")
    print(f"最新版本     : {head}")
    if current == head:
        print("狀態         : 已是最新，沒有待跑的遷移")
        return EXIT_UP_TO_DATE
    print("狀態         : 有待跑的遷移")
    return EXIT_PENDING


def cmd_backup(args: argparse.Namespace) -> int:
    url = make_url(get_settings().database_url)
    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = _ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")

    try:
        backend = url.get_backend_name()
        if backend == "sqlite":
            target, restore = _backup_sqlite(url, out_dir, stamp)
        elif backend == "postgresql":
            target, restore = _backup_postgres(url, out_dir, stamp, args.pg_dump)
        else:
            print(f"不支援的資料庫類型：{backend}", file=sys.stderr)
            return EXIT_ERROR
    except Exception as exc:  # noqa: BLE001 —— 備份失敗要讓部署腳本停下來
        print(f"備份失敗：{exc}", file=sys.stderr)
        return EXIT_ERROR

    size_mb = target.stat().st_size / 1024 / 1024
    print(f"備份完成     : {target}  ({size_mb:.2f} MB)")
    print(f"還原指令     : {restore}")
    return EXIT_UP_TO_DATE


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check", help="印出目前版本與是否有待跑的遷移")

    backup = sub.add_parser("backup", help="遷移前備份資料庫")
    backup.add_argument("--out-dir", default="backups", help="備份存放目錄（預設 backups/）")
    backup.add_argument("--pg-dump", default="pg_dump", help="pg_dump 執行檔路徑")

    args = parser.parse_args()
    return {"check": cmd_check, "backup": cmd_backup}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
