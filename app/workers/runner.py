"""asyncio 背景 worker：輪詢 `jobs` 表，claim queued job（→running）交給對應 handler 執行，
結束時寫回終態（done/failed，失敗連同 `job.error`）。

程序內單一 asyncio task，介面預留可換成獨立 worker/queue。**不掛載進 `app.main`**——
由協調者在 FastAPI lifespan 呼叫 `start_worker()`/`stop_worker()` 接線；
測試直接呼叫 `poll_once()` 驅動單次輪詢，不需要真的跑背景迴圈。
"""

import asyncio
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.repos import jobs as jobs_repo
from app.repos.db import get_session_factory
from app.repos.models import Job
from app.workers import handlers

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL = 1.0

_HANDLERS = {
    "generate": handlers.handle_generate_job,
    "review": handlers.handle_review_job,
    "extra": handlers.handle_extra_job,
}

_worker_task: asyncio.Task | None = None


async def _queued_job_ids(session_factory: async_sessionmaker[AsyncSession]) -> list[uuid.UUID]:
    async with session_factory() as db:
        result = await db.execute(
            select(Job.id).where(Job.status == "queued").order_by(Job.created_at.asc())
        )
        return [row[0] for row in result.all()]


async def _process_job(
    job_id: uuid.UUID, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """claim（queued→running）→ 執行對應 handler → 寫終態（done/failed）。"""
    async with session_factory() as db:
        job = await jobs_repo.get_job(db, job_id)
        if job is None or job.status != "queued":
            return
        await jobs_repo.start_job(db, job_id)
        await db.commit()
        kind = job.kind

    handler = _HANDLERS.get(kind)
    async with session_factory() as db:
        job = await jobs_repo.get_job(db, job_id)
        try:
            if handler is None:
                raise ValueError(f"未知的 job kind：{kind}")
            await handler(db, job, session_factory)
            await jobs_repo.finish_job(db, job_id, status="done")
            await db.commit()
        except Exception as exc:
            logger.exception("worker_job_failed", extra={"job_id": str(job_id), "kind": kind})
            await db.rollback()
            async with session_factory() as fail_db:
                await jobs_repo.finish_job(fail_db, job_id, status="failed", error=str(exc))
                await fail_db.commit()


async def poll_once(session_factory: async_sessionmaker[AsyncSession] | None = None) -> int:
    """處理目前所有 queued 工作一輪；回傳處理的工作數。供測試直接驅動單次輪詢。"""
    session_factory = session_factory or get_session_factory()
    job_ids = await _queued_job_ids(session_factory)
    for job_id in job_ids:
        await _process_job(job_id, session_factory)
    return len(job_ids)


async def _poll_loop(interval: float) -> None:
    while True:
        try:
            await poll_once()
        except Exception:
            logger.exception("worker_poll_error")
        await asyncio.sleep(interval)


def start_worker(interval: float = DEFAULT_POLL_INTERVAL) -> asyncio.Task:
    """啟動程序內背景 worker（asyncio task）。由協調者於 app 啟動時（lifespan）呼叫。"""
    global _worker_task
    if _worker_task is None or _worker_task.done():
        _worker_task = asyncio.create_task(_poll_loop(interval))
    return _worker_task


async def stop_worker() -> None:
    """停止背景 worker，等待目前一輪輪詢結束後回傳。"""
    global _worker_task
    if _worker_task is not None:
        _worker_task.cancel()
        try:
            await _worker_task
        except asyncio.CancelledError:
            pass
        _worker_task = None
