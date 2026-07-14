"""sessions API：CRUD、對話（SSE / JSON）、confirm、版本 restore、DB 匯入。

`/api/v1` 前綴由 `app/main.py` 掛載時統一加上，本檔路徑不自帶前綴。
"""

import json
from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.api.schemas.sessions import (
    ConfirmResponse,
    CreateSessionRequest,
    ImportDbRequest,
    ImportDbResponse,
    JobSummary,
    SendMessageRequest,
    SessionDetail,
    SessionSummary,
    TurnResponse,
    VersionOut,
)
from app.llm.provider import LLMProvider
from app.repos import sessions as sessions_repo
from app.repos import versions as versions_repo
from app.repos.models import Job, SchemaVersion, SessionRecord
from app.rules.spec_models import tables_from_json
from app.services import interview_service, session_service

router = APIRouter(prefix="/sessions", tags=["sessions"])

DbDep = Annotated[AsyncSession, Depends(get_db)]

# SSE 模式下，reply 文字模擬切成固定大小的 delta 增量送出（見本檔 docstring 設計說明）。
_DELTA_CHUNK_SIZE = 40


# -- request/response 轉換 -------------------------------------------------


def _to_summary(session: SessionRecord) -> SessionSummary:
    return SessionSummary(
        id=session.id,
        title=session.title,
        mode=session.mode,
        phase=session.phase,
        created_at=session.created_at,
    )


def _to_job_summary(job: Job) -> JobSummary:
    return JobSummary(
        id=job.id,
        kind=job.kind,
        status=job.status,
        progress_json=job.progress_json,
        error=job.error,
        created_at=job.created_at,
    )


def _to_detail(data: session_service.SessionDetailData) -> SessionDetail:
    session = data.session
    context_tables = (
        tables_from_json(session.context_tables_json) if session.context_tables_json else None
    )
    latest_version_num = None
    latest_tables = None
    latest_key_points = None
    if data.latest_version is not None:
        latest_version_num = data.latest_version.version_num
        if data.latest_version.tables_json:
            latest_tables = tables_from_json(data.latest_version.tables_json)
        latest_key_points = data.latest_version.key_points_json

    return SessionDetail(
        id=session.id,
        title=session.title,
        mode=session.mode,
        phase=session.phase,
        created_at=session.created_at,
        context_tables=context_tables,
        latest_version=latest_version_num,
        latest_tables=latest_tables,
        latest_key_points=latest_key_points,
        jobs=[_to_job_summary(j) for j in data.jobs],
    )


def _to_version_out(version: SchemaVersion) -> VersionOut:
    tables = tables_from_json(version.tables_json) if version.tables_json else None
    return VersionOut(
        version_num=version.version_num,
        tables=tables,
        key_points=version.key_points_json,
        created_at=version.created_at,
    )


def _sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _stream_turn(turn_response: TurnResponse) -> AsyncIterator[str]:
    """把已產出的完整回覆文字模擬成 delta 增量串流，最後送出 turn_done。

    設計取捨：`LLMProvider` 目前的 stream 路徑不解析 `response_model`
    （見 app/llm/provider.py `_chat_stream`，structured output 解析只在
    非串流路徑執行），Interviewer 需要同時拿到文字回覆與結構化 tables，
    因此改採「非串流呼叫拿到完整結果 → 模擬切塊送 delta → turn_done」，
    對前端行為等價（仍是 delta* → turn_done 的事件序列），且不需要
    每輪重複呼叫兩次 LLM。
    """
    reply = turn_response.reply
    for i in range(0, len(reply), _DELTA_CHUNK_SIZE):
        yield _sse_event("delta", {"delta": reply[i : i + _DELTA_CHUNK_SIZE]})
    yield _sse_event("turn_done", turn_response.model_dump(mode="json"))


# -- endpoints --------------------------------------------------------------


@router.post("", response_model=SessionSummary, status_code=201)
async def create_session(
    payload: CreateSessionRequest, db: DbDep
) -> SessionSummary:
    try:
        session = await session_service.create_session(
            db, title=payload.title, mode=payload.mode, db_url=payload.db_url
        )
    except session_service.DbConnectionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _to_summary(session)


@router.get("", response_model=list[SessionSummary])
async def list_sessions(db: DbDep) -> list[SessionSummary]:
    sessions = await session_service.list_sessions(db)
    return [_to_summary(s) for s in sessions]


@router.get("/{session_id}", response_model=SessionDetail)
async def get_session(session_id: UUID, db: DbDep) -> SessionDetail:
    detail = await session_service.get_session_detail(db, session_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="session not found")
    return _to_detail(detail)


@router.post("/{session_id}/messages")
async def send_message(
    session_id: UUID,
    payload: SendMessageRequest,
    db: DbDep,
    accept: str | None = Header(default=None),
):
    session = await sessions_repo.get_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")

    provider = LLMProvider.from_settings()
    turn = await interview_service.run_turn(db, provider, session, payload.content)
    turn_response = TurnResponse(
        reply=turn.reply,
        tables_ready=bool(turn.tables),
        tables=turn.tables,
        summary=turn.summary,
    )

    if accept and "text/event-stream" in accept:
        return StreamingResponse(_stream_turn(turn_response), media_type="text/event-stream")
    return turn_response


@router.post("/{session_id}/confirm", response_model=ConfirmResponse)
async def confirm_session(
    session_id: UUID, db: DbDep
) -> ConfirmResponse:
    try:
        job = await session_service.confirm_session(db, session_id)
    except session_service.SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    except session_service.ConfirmConflictError as exc:
        raise HTTPException(status_code=409, detail="session 目前不是 confirming 狀態") from exc
    return ConfirmResponse(session_id=session_id, phase="generating", job_id=job.id)


@router.get("/{session_id}/versions", response_model=list[VersionOut])
async def list_versions(
    session_id: UUID, db: DbDep
) -> list[VersionOut]:
    session = await sessions_repo.get_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    versions = await versions_repo.list_versions(db, session_id)
    return [_to_version_out(v) for v in versions]


@router.post("/{session_id}/versions/{version_num}/restore", response_model=VersionOut)
async def restore_version(
    session_id: UUID, version_num: int, db: DbDep
) -> VersionOut:
    try:
        restored = await session_service.restore_version(db, session_id, version_num)
    except session_service.SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    except session_service.VersionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="version not found") from exc
    return _to_version_out(restored)


@router.post("/{session_id}/import-db", response_model=ImportDbResponse)
async def import_db(
    session_id: UUID, payload: ImportDbRequest, db: DbDep
) -> ImportDbResponse:
    try:
        session = await session_service.import_db(db, session_id, payload.db_url)
    except session_service.SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    except session_service.DbConnectionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    tables = tables_from_json(session.context_tables_json) if session.context_tables_json else []
    return ImportDbResponse(table_count=len(tables), context_tables=tables)
