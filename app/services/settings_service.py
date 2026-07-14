"""平台設定（記憶後端狀態 + 業務資料庫連線）與稽核紀錄查詢的業務邏輯。

業務 DB 連線存於 `app_settings` repo，key="business_databases"，值為
`list[{name, db_url_encrypted}]`（DB Agent 讀取同一 key，格式需一致）。
db_url 絕不回傳前端——一律先 `decrypt_db_url` 再 `mask_db_url`。
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.repos import activity
from app.repos import settings as settings_repo
from app.repos.crypto import decrypt_db_url, encrypt_db_url, mask_db_url
from app.repos.models import ActivityLog
from app.services import dbops
from app.services.workbench_service import sanitize_db_error

_BUSINESS_DB_KEY = "business_databases"
_ALLOWED_SCHEMES = ("postgresql://", "postgres://")


class ConnectionTestFailed(Exception):
    """業務資料庫連線測試失敗。"""


async def _load_business_dbs(db: AsyncSession) -> list[dict]:
    record = await settings_repo.get_setting(db, _BUSINESS_DB_KEY)
    return list(record.value_json) if record and record.value_json else []


def _mask_entry(entry: dict) -> dict:
    url = decrypt_db_url(entry["db_url_encrypted"])
    return {"name": entry["name"], "masked_url": mask_db_url(url)}


async def get_settings_overview(db: AsyncSession) -> dict:
    """回傳目前記憶後端狀態（sqlite/postgresql）+ 業務 DB 清單（連線字串遮罩）。"""
    database_url = get_settings().database_url
    configured = database_url.startswith(_ALLOWED_SCHEMES)
    backend = "postgresql" if configured else "sqlite"
    entries = await _load_business_dbs(db)
    return {
        "configured": configured,
        "backend": backend,
        "masked_url": mask_db_url(database_url),
        "business_databases": [_mask_entry(e) for e in entries],
    }


async def upsert_business_database(db: AsyncSession, name: str, url: str) -> list[dict]:
    """新增或更新一筆業務資料庫連線：先測試連線成功才加密存入。"""
    if not url.startswith(_ALLOWED_SCHEMES):
        raise ValueError("僅支援 PostgreSQL 連線字串（postgresql://...）")
    try:
        await dbops.execute_query(url, "SELECT 1")
    except Exception as exc:
        raise ConnectionTestFailed(sanitize_db_error(str(exc))) from exc

    entries = [e for e in await _load_business_dbs(db) if e["name"] != name]
    entries.append({"name": name, "db_url_encrypted": encrypt_db_url(url)})
    await settings_repo.set_setting(db, _BUSINESS_DB_KEY, entries)
    await activity.log_activity(
        db, "business_db_configured", {"name": name, "masked_url": mask_db_url(url)}
    )
    return [_mask_entry(e) for e in entries]


async def remove_business_database(db: AsyncSession, name: str) -> list[dict]:
    """刪除一筆業務資料庫連線。"""
    entries = [e for e in await _load_business_dbs(db) if e["name"] != name]
    await settings_repo.set_setting(db, _BUSINESS_DB_KEY, entries)
    await activity.log_activity(db, "business_db_removed", {"name": name})
    return [_mask_entry(e) for e in entries]


async def list_activity(db: AsyncSession, *, limit: int = 100) -> list[ActivityLog]:
    """稽核紀錄，倒序 + limit（上限交給 caller 夾限）。"""
    return await activity.list_activity(db, limit=limit)
