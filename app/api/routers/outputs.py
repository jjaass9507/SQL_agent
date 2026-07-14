"""outputs router：已產出文件列表/zip 下載、on-demand extras 觸發、SSE 生成進度事件。

放在獨立檔案（而非 sessions router）以避免與另一組 agent 平行開發的 sessions API
檔案衝突（見 docs/v2_rebuild_plan.md 第十章 Phase 4 任務說明）。
"""

import asyncio
import io
import json
import uuid
import zipfile
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import check_session_access, get_current_user, get_db
from app.repos import jobs as jobs_repo
from app.repos import outputs as outputs_repo
from app.repos import sessions as sessions_repo
from app.repos import versions as versions_repo
from app.repos.db import get_session_factory
from app.repos.models import SessionRecord
from app.services.auth_service import CurrentUser
from app.services.generation_service import EXTRA_FILENAMES

router = APIRouter(tags=["outputs"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
CurrentUserDep = Annotated[CurrentUser | None, Depends(get_current_user)]

_SSE_POLL_INTERVAL = 0.5


async def _get_session_or_404(
    db: AsyncSession, session_id: uuid.UUID, current_user: CurrentUser | None = None
) -> SessionRecord:
    session = await sessions_repo.get_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session 不存在")
    await check_session_access(db, session, current_user)
    return session


@router.get("/sessions/{session_id}/outputs")
async def list_outputs(
    session_id: uuid.UUID, db: DbDep, current_user: CurrentUserDep
) -> list[dict]:
    """列出某 session 已產出的所有文件（含內容）。"""
    await _get_session_or_404(db, session_id, current_user)
    outputs = await outputs_repo.list_outputs(db, session_id)
    return [
        {"filename": o.filename, "content": o.content, "created_at": o.created_at.isoformat()}
        for o in outputs
    ]


@router.get("/sessions/{session_id}/outputs/zip")
async def download_outputs_zip(
    session_id: uuid.UUID, db: DbDep, current_user: CurrentUserDep
) -> Response:
    """把某 session 已產出的所有文件打包成 zip 下載。"""
    await _get_session_or_404(db, session_id, current_user)
    outputs = await outputs_repo.list_outputs(db, session_id)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for o in outputs:
            zf.writestr(o.filename, o.content or "")
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="session_{session_id}_outputs.zip"'},
    )


@router.post("/sessions/{session_id}/extras/{kind}/generate")
async def generate_extra(
    session_id: uuid.UUID, kind: str, db: DbDep, current_user: CurrentUserDep
) -> dict:
    """建立一個 kind="extra" 的 queued job，由背景 worker 消化。

    延伸產出所需的目標設計表結構取自最新一版 schema 快照；`incremental` 另外
    帶入 session 匯入的現有結構（`context_tables_json`）供差異比對。
    """
    session = await _get_session_or_404(db, session_id, current_user)
    if kind not in EXTRA_FILENAMES:
        raise HTTPException(status_code=400, detail=f"不支援的 extra kind：{kind}")

    latest_version = await versions_repo.get_latest_version(db, session_id)
    tables_json = (latest_version.tables_json if latest_version else None) or []
    if not tables_json:
        raise HTTPException(status_code=400, detail="session 尚無已確認的資料表結構")

    payload: dict = {"kind": kind, "tables": tables_json}
    if kind == "incremental":
        payload["context_tables"] = session.context_tables_json or []

    job = await jobs_repo.create_job(db, session_id, kind="extra", payload_json=payload)
    return {"job_id": str(job.id), "status": job.status}


async def _job_progress_stream(
    session_id: uuid.UUID,
    job_id: uuid.UUID | None,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    poll_interval: float = _SSE_POLL_INTERVAL,
) -> AsyncIterator[str]:
    """輪詢 job.progress_json 變化，推送 SSE `generation_status` event；job 終態後結束 stream。

    未指定 `job_id` 時，跟蹤該 session 最新建立的一個 job。
    """
    if job_id is None:
        async with session_factory() as db:
            jobs = await jobs_repo.list_jobs(db, session_id)  # 已依 created_at 新到舊排序
        if not jobs:
            return
        job_id = jobs[0].id

    last_sent: dict | None = None
    while True:
        async with session_factory() as db:
            job = await jobs_repo.get_job(db, job_id)
        if job is None:
            return

        payload = {
            "job_id": str(job.id),
            "kind": job.kind,
            "status": job.status,
            "progress": job.progress_json,
            "error": job.error,
        }
        if payload != last_sent:
            yield f"event: generation_status\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
            last_sent = payload

        if job.status in ("done", "failed"):
            return
        await asyncio.sleep(poll_interval)


@router.get("/sessions/{session_id}/events")
async def stream_generation_events(
    session_id: uuid.UUID,
    db: DbDep,
    current_user: CurrentUserDep,
    job_id: uuid.UUID | None = None,
) -> StreamingResponse:
    """SSE：生成/審查/extra 進度（取代前端輪詢）。`job_id` 省略時跟蹤最新一個 job。"""
    await _get_session_or_404(db, session_id, current_user)
    return StreamingResponse(
        _job_progress_stream(session_id, job_id, get_session_factory()),
        media_type="text/event-stream",
    )
