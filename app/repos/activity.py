"""`activity_log` 表的 repository 函式（結構化 audit log）。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.repos.models import ActivityLog


async def log_activity(
    db: AsyncSession, event: str, detail_json: dict | None = None
) -> ActivityLog:
    """寫入一筆 audit log 事件。"""
    record = ActivityLog(event=event, detail_json=detail_json)
    db.add(record)
    await db.flush()
    return record


async def list_activity(db: AsyncSession, *, limit: int = 100) -> list[ActivityLog]:
    """依時間新到舊列出 audit log，預設最多 100 筆。"""
    stmt = select(ActivityLog).order_by(ActivityLog.created_at.desc()).limit(limit)
    result = await db.execute(stmt)
    return list(result.scalars().all())
