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

from app.api.deps import check_session_access, get_current_user, get_db
from app.api.schemas.sessions import (
    ConfirmResponse,
    CreateSessionRequest,
    ImportDbRequest,
    ImportDbResponse,
    JobSummary,
    MessageOut,
    SendMessageRequest,
    SessionDetail,
    SessionSummary,
    TablesDdlRequest,
    TurnResponse,
    VersionOut,
)
from app.config import get_settings
from app.repos import activity as activity_repo
from app.repos import messages as messages_repo
from app.repos import sessions as sessions_repo
from app.repos import versions as versions_repo
from app.repos.models import Job, SchemaVersion, SessionRecord
from app.rules import ddl_parser
from app.rules.schema_diff import compute_diff
from app.rules.spec_models import tables_from_json
from app.services import agent_service, interview_service, provider_factory, session_service
from app.services.auth_service import CurrentUser

router = APIRouter(prefix="/sessions", tags=["sessions"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
CurrentUserDep = Annotated[CurrentUser | None, Depends(get_current_user)]

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

    # 差異比對一律在後端算：schema_diff 會比對型態／NULL／UNIQUE／索引，
    # 前端自行比對只看得出欄位有無，會把 VARCHAR(20)→VARCHAR(10) 判成「不變」。
    schema_diff = (
        compute_diff(latest_tables, context_tables) if latest_tables and context_tables else None
    )

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
        schema_diff=schema_diff,
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
    payload: CreateSessionRequest, db: DbDep, current_user: CurrentUserDep
) -> SessionSummary:
    try:
        session = await session_service.create_session(
            db, title=payload.title, mode=payload.mode, db_url=payload.db_url
        )
    except session_service.DbConnectionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if get_settings().auth_enabled and current_user is not None:
        session = await sessions_repo.update_session(db, session.id, user_id=current_user.id)
    return _to_summary(session)


@router.get("", response_model=list[SessionSummary])
async def list_sessions(db: DbDep, current_user: CurrentUserDep) -> list[SessionSummary]:
    sessions = await session_service.list_sessions(db)
    settings = get_settings()
    if settings.auth_enabled and current_user is not None and current_user.role != "admin":
        sessions = [s for s in sessions if s.user_id == current_user.id]
    return [_to_summary(s) for s in sessions]


@router.delete("/{session_id}", status_code=204)
async def delete_session(session_id: UUID, db: DbDep, current_user: CurrentUserDep) -> None:
    """刪除 session 及其訊息／版本／產出（models.py 的外鍵皆為 ON DELETE CASCADE）。"""
    record = await sessions_repo.get_session(db, session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="找不到這筆設計紀錄，可能已經被刪除了")
    await check_session_access(db, record, current_user)
    await sessions_repo.delete_session(db, session_id)
    await activity_repo.log_activity(db, "session.delete", {"session_id": str(session_id)})


@router.get("/{session_id}", response_model=SessionDetail)
async def get_session(session_id: UUID, db: DbDep, current_user: CurrentUserDep) -> SessionDetail:
    detail = await session_service.get_session_detail(db, session_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="找不到這個對話，可能已經被刪除了。")
    await check_session_access(db, detail.session, current_user)
    return _to_detail(detail)


@router.get("/{session_id}/messages", response_model=list[MessageOut])
async def list_messages(
    session_id: UUID, db: DbDep, current_user: CurrentUserDep
) -> list[MessageOut]:
    """依時間由舊到新回傳對話歷史，供前端重新整理後還原畫面。

    DB Agent 的 transcript 把 tool_call / tool_result 也以 role="ai" 的 JSON
    字串存在同一張表（見 app/services/agent_service.py 的 docstring），那些不是
    給人看的文字，這裡一律濾掉——本端點只服務需求收集對話。
    """
    session = await sessions_repo.get_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="找不到這筆設計紀錄，可能已經被刪除了")
    await check_session_access(db, session, current_user)

    records = await messages_repo.list_messages(db, session_id)
    return [
        MessageOut(role=r.role, content=r.content, created_at=r.created_at)
        for r in records
        if agent_service.decode_ai_content(r.content) is None
    ]


@router.get("/{session_id}/tables-ddl")
async def get_tables_ddl(session_id: UUID, db: DbDep, current_user: CurrentUserDep) -> dict:
    """把目前的設計輸出成可編輯的 CREATE TABLE 文字（確認頁的「以 DDL 編輯」）。"""
    detail = await session_service.get_session_detail(db, session_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="找不到這筆設計紀錄，可能已經被刪除了")
    await check_session_access(db, detail.session, current_user)

    version = detail.latest_version
    tables = tables_from_json(version.tables_json) if version and version.tables_json else []
    return {"ddl": ddl_parser.to_ddl(tables)}


@router.put("/{session_id}/tables-ddl", response_model=VersionOut)
async def put_tables_ddl(
    session_id: UUID, payload: TablesDdlRequest, db: DbDep, current_user: CurrentUserDep
) -> VersionOut:
    """以手改後的 DDL 取代目前設計，存成新版本（不覆寫，版本歷史保留）。"""
    session = await sessions_repo.get_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="找不到這筆設計紀錄，可能已經被刪除了")
    await check_session_access(db, session, current_user)

    try:
        version = await session_service.replace_tables_from_ddl(db, session_id, payload.ddl)
    except session_service.EmptyDdlError:
        raise HTTPException(
            status_code=422,
            detail="沒有解析出任何資料表，請確認每個區塊都是完整的 CREATE TABLE ... ( ... );",
        ) from None
    return _to_version_out(version)


@router.post("/{session_id}/messages")
async def send_message(
    session_id: UUID,
    payload: SendMessageRequest,
    db: DbDep,
    current_user: CurrentUserDep,
    accept: str | None = Header(default=None),
):
    session = await sessions_repo.get_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="找不到這個對話，可能已經被刪除了。")
    await check_session_access(db, session, current_user)

    provider = await provider_factory.build_provider(db)
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
    session_id: UUID, db: DbDep, current_user: CurrentUserDep
) -> ConfirmResponse:
    session = await sessions_repo.get_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="找不到這個對話，可能已經被刪除了。")
    await check_session_access(db, session, current_user)
    try:
        job = await session_service.confirm_session(db, session_id)
    except session_service.SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="找不到這個對話，可能已經被刪除了。") from exc
    except session_service.ConfirmConflictError as exc:
        raise HTTPException(status_code=409, detail="session 目前不是 confirming 狀態") from exc
    return ConfirmResponse(session_id=session_id, phase="generating", job_id=job.id)


@router.get("/{session_id}/versions", response_model=list[VersionOut])
async def list_versions(
    session_id: UUID, db: DbDep, current_user: CurrentUserDep
) -> list[VersionOut]:
    session = await sessions_repo.get_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="找不到這個對話，可能已經被刪除了。")
    await check_session_access(db, session, current_user)
    versions = await versions_repo.list_versions(db, session_id)
    return [_to_version_out(v) for v in versions]


@router.post("/{session_id}/versions/{version_num}/restore", response_model=VersionOut)
async def restore_version(
    session_id: UUID, version_num: int, db: DbDep, current_user: CurrentUserDep
) -> VersionOut:
    session = await sessions_repo.get_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="找不到這個對話，可能已經被刪除了。")
    await check_session_access(db, session, current_user)
    try:
        restored = await session_service.restore_version(db, session_id, version_num)
    except session_service.SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="找不到這個對話，可能已經被刪除了。") from exc
    except session_service.VersionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="找不到這個版本，可能已經被刪除了。") from exc
    return _to_version_out(restored)


@router.post("/{session_id}/import-db", response_model=ImportDbResponse)
async def import_db(
    session_id: UUID, payload: ImportDbRequest, db: DbDep, current_user: CurrentUserDep
) -> ImportDbResponse:
    existing = await sessions_repo.get_session(db, session_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="找不到這個對話，可能已經被刪除了。")
    await check_session_access(db, existing, current_user)
    try:
        session = await session_service.import_db(db, session_id, payload.db_url)
    except session_service.SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="找不到這個對話，可能已經被刪除了。") from exc
    except session_service.DbConnectionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    tables = tables_from_json(session.context_tables_json) if session.context_tables_json else []
    return ImportDbResponse(table_count=len(tables), context_tables=tables)
