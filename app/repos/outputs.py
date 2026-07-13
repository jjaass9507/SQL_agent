"""`outputs` 表的 repository 函式（生成產出的文件內容，(session_id, filename) 唯一）。"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.repos.models import Output


async def upsert_output(
    db: AsyncSession, session_id: uuid.UUID, filename: str, content: str | None
) -> Output:
    """寫入或覆蓋某 session 下指定檔名的產出內容。"""
    record = await get_output(db, session_id, filename)
    if record is None:
        record = Output(session_id=session_id, filename=filename, content=content)
        db.add(record)
    else:
        record.content = content
    await db.flush()
    return record


async def get_output(db: AsyncSession, session_id: uuid.UUID, filename: str) -> Output | None:
    """取得單一產出檔案。"""
    stmt = select(Output).where(Output.session_id == session_id, Output.filename == filename)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def list_outputs(db: AsyncSession, session_id: uuid.UUID) -> list[Output]:
    """列出某 session 的所有產出檔案。"""
    stmt = select(Output).where(Output.session_id == session_id).order_by(Output.filename.asc())
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def delete_output(db: AsyncSession, session_id: uuid.UUID, filename: str) -> bool:
    """刪除單一產出檔案，回傳是否有刪到資料。"""
    record = await get_output(db, session_id, filename)
    if record is None:
        return False
    await db.delete(record)
    await db.flush()
    return True
