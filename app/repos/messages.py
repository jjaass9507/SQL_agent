"""`messages` 表的 repository 函式。"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.repos.models import Message


async def add_message(db: AsyncSession, session_id: uuid.UUID, role: str, content: str) -> Message:
    """新增一則訊息（role 為 'user' 或 'ai'）。"""
    record = Message(session_id=session_id, role=role, content=content)
    db.add(record)
    await db.flush()
    return record


async def list_messages(db: AsyncSession, session_id: uuid.UUID) -> list[Message]:
    """依建立時間由舊到新列出某 session 的所有訊息。"""
    stmt = (
        select(Message).where(Message.session_id == session_id).order_by(Message.created_at.asc())
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())
