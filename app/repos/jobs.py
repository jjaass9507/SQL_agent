"""`jobs` 表的 repository 函式（背景工作狀態機：queued → running → done/failed）。"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.repos.models import Job


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def create_job(
    db: AsyncSession, session_id: uuid.UUID, kind: str, payload_json: dict | None = None
) -> Job:
    """建立一個新工作，初始狀態為 'queued'。"""
    record = Job(session_id=session_id, kind=kind, status="queued", payload_json=payload_json)
    db.add(record)
    await db.flush()
    return record


async def get_job(db: AsyncSession, job_id: uuid.UUID) -> Job | None:
    """依 id 取得工作。"""
    return await db.get(Job, job_id)


async def list_jobs(db: AsyncSession, session_id: uuid.UUID) -> list[Job]:
    """依建立時間新到舊列出某 session 的所有工作。"""
    stmt = select(Job).where(Job.session_id == session_id).order_by(Job.created_at.desc())
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def start_job(db: AsyncSession, job_id: uuid.UUID) -> Job | None:
    """將工作標記為 'running' 並記錄開始時間。"""
    record = await get_job(db, job_id)
    if record is None:
        return None
    record.status = "running"
    record.started_at = _now()
    await db.flush()
    return record


async def update_job_progress(
    db: AsyncSession, job_id: uuid.UUID, progress_json: dict
) -> Job | None:
    """更新工作進度（例如每份文件的 waiting/loading/done/failed）。"""
    record = await get_job(db, job_id)
    if record is None:
        return None
    record.progress_json = progress_json
    await db.flush()
    return record


async def finish_job(
    db: AsyncSession, job_id: uuid.UUID, *, status: str, error: str | None = None
) -> Job | None:
    """將工作標記為結束狀態（'done' 或 'failed'）並記錄結束時間。"""
    record = await get_job(db, job_id)
    if record is None:
        return None
    record.status = status
    record.error = error
    record.finished_at = _now()
    await db.flush()
    return record
