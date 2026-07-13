"""`change_requests` 表的 repository 函式（HITL 結構變更提案審批流程）。"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.repos.models import ChangeRequest


def _now() -> datetime:
    return datetime.now(UTC)


async def create_change_request(
    db: AsyncSession,
    db_name: str,
    ddl: str,
    reason: str = "",
    dry_run_ok: bool | None = None,
) -> ChangeRequest:
    """建立一筆變更提案，初始狀態為 'pending'。"""
    record = ChangeRequest(
        db_name=db_name, ddl=ddl, reason=reason, status="pending", dry_run_ok=dry_run_ok
    )
    db.add(record)
    await db.flush()
    return record


async def get_change_request(
    db: AsyncSession, change_request_id: uuid.UUID
) -> ChangeRequest | None:
    """依 id 取得變更提案。"""
    return await db.get(ChangeRequest, change_request_id)


async def list_change_requests(
    db: AsyncSession, *, status: str | None = None
) -> list[ChangeRequest]:
    """列出變更提案，依建立時間新到舊排序；`status` 給定時僅回傳該狀態的提案。"""
    stmt = select(ChangeRequest).order_by(ChangeRequest.created_at.desc())
    if status is not None:
        stmt = stmt.where(ChangeRequest.status == status)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def decide_change_request(
    db: AsyncSession, change_request_id: uuid.UUID, *, status: str, error: str | None = None
) -> ChangeRequest | None:
    """更新提案狀態。狀態轉為 'approved'/'rejected' 時記錄審批時間；其餘（executed/failed）
    僅更新狀態與錯誤訊息，不覆寫既有的審批時間。
    """
    record = await get_change_request(db, change_request_id)
    if record is None:
        return None
    record.status = status
    record.error = error
    if status in ("approved", "rejected"):
        record.decided_at = _now()
    await db.flush()
    return record
