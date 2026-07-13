"""`sessions` 表的 repository 函式（async，吃 AsyncSession，不自行開連線）。"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.repos.models import SessionRecord


async def create_session(
    db: AsyncSession,
    *,
    title: str = "未命名設計",
    mode: str = "design",
    user_id: uuid.UUID | None = None,
    context_text: str = "",
    context_tables_json: list | None = None,
    db_url_encrypted: str | None = None,
) -> SessionRecord:
    """建立一個新的 session。"""
    record = SessionRecord(
        title=title,
        mode=mode,
        user_id=user_id,
        context_text=context_text,
        context_tables_json=context_tables_json,
        db_url_encrypted=db_url_encrypted,
    )
    db.add(record)
    await db.flush()
    return record


async def get_session(db: AsyncSession, session_id: uuid.UUID) -> SessionRecord | None:
    """依 id 取得單一 session，不存在回傳 None。"""
    return await db.get(SessionRecord, session_id)


async def list_sessions(
    db: AsyncSession, *, user_id: uuid.UUID | None = None
) -> list[SessionRecord]:
    """列出 session，依建立時間新到舊排序；`user_id` 給定時僅回傳該使用者的 session。"""
    stmt = select(SessionRecord).order_by(SessionRecord.created_at.desc())
    if user_id is not None:
        stmt = stmt.where(SessionRecord.user_id == user_id)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def update_session(db: AsyncSession, session_id: uuid.UUID, **fields) -> SessionRecord | None:
    """更新指定欄位（title/mode/phase/context_text/context_tables_json/db_url_encrypted）。"""
    record = await get_session(db, session_id)
    if record is None:
        return None
    for key, value in fields.items():
        setattr(record, key, value)
    await db.flush()
    return record


async def delete_session(db: AsyncSession, session_id: uuid.UUID) -> bool:
    """刪除 session（其他表以 ON DELETE CASCADE 一併清除）。回傳是否有刪到資料。"""
    record = await get_session(db, session_id)
    if record is None:
        return False
    await db.delete(record)
    await db.flush()
    return True
