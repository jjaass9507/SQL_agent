"""SQL 工作台路由：唯讀查詢/EXPLAIN、結構瀏覽、NL2SQL、DDL dry-run 驗證、DDL 貼上匯入。

`api/` 只做 HTTP 進出與驗證，業務邏輯一律委派 `app.services.workbench_service`。
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import check_session_access, get_current_user, get_db
from app.api.schemas.workbench import (
    DDLImportRequest,
    DDLImportResponse,
    NL2SQLRequest,
    NL2SQLResponse,
    QueryRequest,
    QueryResult,
    SchemaTreeResponse,
    ValidateDDLResponse,
)
from app.config import get_settings
from app.llm.provider import LLMProvider
from app.repos import sessions as sessions_repo
from app.services import dbops
from app.services import workbench_service as svc
from app.services.auth_service import CurrentUser

router = APIRouter(tags=["workbench"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
CurrentUserDep = Annotated[CurrentUser | None, Depends(get_current_user)]


def _not_found(session_id: uuid.UUID) -> HTTPException:
    return HTTPException(status_code=404, detail=f"session {session_id} not found")


def _no_db() -> HTTPException:
    return HTTPException(status_code=400, detail="此 session 未設定資料庫連線")


async def _check_access(
    db: AsyncSession, session_id: uuid.UUID, current_user: CurrentUser | None
) -> None:
    """session 所有權驗證（不存在時沿用本 router 既有的 404 格式）。"""
    session = await sessions_repo.get_session(db, session_id)
    if session is None:
        raise _not_found(session_id)
    await check_session_access(db, session, current_user)


@router.post("/sessions/{session_id}/query", response_model=QueryResult)
async def query(session_id: uuid.UUID, body: QueryRequest, db: DbDep, current_user: CurrentUserDep):
    await _check_access(db, session_id, current_user)
    try:
        return await svc.run_query(db, session_id, body.sql)
    except svc.SessionNotFound:
        raise _not_found(session_id) from None
    except svc.NoDatabaseConfigured:
        raise _no_db() from None
    except dbops.QueryRejected as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.post("/sessions/{session_id}/explain", response_model=QueryResult)
async def explain(
    session_id: uuid.UUID, body: QueryRequest, db: DbDep, current_user: CurrentUserDep
):
    await _check_access(db, session_id, current_user)
    try:
        return await svc.run_explain(db, session_id, body.sql)
    except svc.SessionNotFound:
        raise _not_found(session_id) from None
    except svc.NoDatabaseConfigured:
        raise _no_db() from None
    except dbops.QueryRejected as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.get("/sessions/{session_id}/schema-tree", response_model=SchemaTreeResponse)
async def schema_tree(session_id: uuid.UUID, db: DbDep, current_user: CurrentUserDep):
    await _check_access(db, session_id, current_user)
    try:
        return await svc.get_schema_tree(db, session_id)
    except svc.SessionNotFound:
        raise _not_found(session_id) from None


@router.post("/sessions/{session_id}/nl2sql", response_model=NL2SQLResponse)
async def nl2sql(
    session_id: uuid.UUID, body: NL2SQLRequest, db: DbDep, current_user: CurrentUserDep
):
    await _check_access(db, session_id, current_user)
    llm = LLMProvider.from_settings()
    try:
        draft = await svc.generate_nl2sql(db, session_id, body.question, llm)
    except svc.SessionNotFound:
        raise _not_found(session_id) from None
    except svc.NoDatabaseConfigured:
        raise _no_db() from None
    except dbops.QueryRejected as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return NL2SQLResponse(sql=draft.sql, explanation=draft.explanation)


@router.post("/sessions/{session_id}/validate-ddl", response_model=ValidateDDLResponse)
async def validate_ddl(session_id: uuid.UUID, db: DbDep, current_user: CurrentUserDep):
    await _check_access(db, session_id, current_user)
    try:
        return await svc.validate_session_ddl(db, session_id)
    except svc.SessionNotFound:
        raise _not_found(session_id) from None


@router.post("/ddl-import", response_model=DDLImportResponse, status_code=201)
async def ddl_import(body: DDLImportRequest, db: DbDep, current_user: CurrentUserDep):
    try:
        result = await svc.import_ddl(db, body.title, body.ddl)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    # AUTH_ENABLED=true 時，匯入建立的 session 一樣寫入建立者（同 POST /sessions）。
    if get_settings().auth_enabled and current_user is not None:
        await sessions_repo.update_session(db, result["id"], user_id=current_user.id)
    return result
