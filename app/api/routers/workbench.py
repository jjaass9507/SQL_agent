"""SQL 工作台路由：唯讀查詢/EXPLAIN、結構瀏覽、NL2SQL、DDL dry-run 驗證、DDL 貼上匯入。

`api/` 只做 HTTP 進出與驗證，業務邏輯一律委派 `app.services.workbench_service`。
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import check_session_access, get_current_user, get_db, require_admin_role
from app.api.schemas.workbench import (
    ApproveQuestionRequest,
    BusinessDbNL2SQLRequest,
    BusinessDbQueryRequest,
    DDLImportRequest,
    DDLImportResponse,
    DictionaryEntryRequest,
    NL2SQLRequest,
    NL2SQLResponse,
    QueryRequest,
    QueryResult,
    SavedQuestionRequest,
    SchemaTreeResponse,
    ValidateDDLResponse,
    ValidateDDLTextRequest,
    ValidateDDLTextResponse,
)
from app.config import get_settings
from app.repos import sessions as sessions_repo
from app.services import dbops, provider_factory, saved_questions
from app.services import workbench_service as svc
from app.services.auth_service import CurrentUser

router = APIRouter(tags=["workbench"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
CurrentUserDep = Annotated[CurrentUser | None, Depends(get_current_user)]
# 業務資料庫範圍的端點沒有 session 可比對所有權，改為與 /agent/chat 同層級的
# 登入檢查（AUTH_ENABLED=false 時恆放行，行為不變）。
_AuthDep = Depends(get_current_user)
# 核可是「我確認過這個口徑」的背書，比一般查詢多一道門檻。
_AdminDep = Depends(require_admin_role)


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
    llm = await provider_factory.build_provider(db)
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


# ── 業務資料庫範圍的工作台 ───────────────────────────────────────────────
# DB Agent 頁沒有 session，操作對象是頂欄下拉選的業務資料庫。以下端點與上方
# session 範圍的版本共用 service 層核心，差別只在連線怎麼解析。


@router.post("/workbench/query", response_model=QueryResult, dependencies=[_AuthDep])
async def workbench_query(body: BusinessDbQueryRequest, db: DbDep):
    try:
        return await svc.run_query_on_business_db(db, body.db_name, body.sql)
    except svc.NoDatabaseConfigured as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except dbops.QueryRejected as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.post("/workbench/explain", response_model=QueryResult, dependencies=[_AuthDep])
async def workbench_explain(body: BusinessDbQueryRequest, db: DbDep):
    try:
        return await svc.run_explain_on_business_db(db, body.db_name, body.sql)
    except svc.NoDatabaseConfigured as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except dbops.QueryRejected as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.get("/workbench/schema-tree", response_model=SchemaTreeResponse, dependencies=[_AuthDep])
async def workbench_schema_tree(db: DbDep, db_name: str | None = None):
    try:
        return await svc.get_schema_tree_on_business_db(db, db_name)
    except svc.NoDatabaseConfigured as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.post("/workbench/nl2sql", response_model=NL2SQLResponse, dependencies=[_AuthDep])
async def workbench_nl2sql(body: BusinessDbNL2SQLRequest, db: DbDep):
    llm = await provider_factory.build_provider(db)
    try:
        draft = await svc.generate_nl2sql_on_business_db(db, body.db_name, body.question, llm)
    except svc.NoDatabaseConfigured as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except dbops.QueryRejected as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return NL2SQLResponse(sql=draft.sql, explanation=draft.explanation)


@router.get("/workbench/dictionary", dependencies=[_AuthDep])
async def workbench_dictionary(db: DbDep, db_name: str):
    """某個業務資料庫的所有表／欄位註記（key 為 `table` 或 `table|column`）。"""
    return await svc.get_data_dictionary(db, db_name)


@router.put("/workbench/dictionary", dependencies=[_AuthDep])
async def workbench_dictionary_upsert(body: DictionaryEntryRequest, db: DbDep):
    """新增或更新一則註記；note 與 owner 皆空白時視為刪除。"""
    return await svc.set_dictionary_entry(
        db, body.db_name, body.table, body.column, body.note, body.owner
    )


@router.get("/workbench/saved-questions", dependencies=[_AuthDep])
async def list_saved_questions(db: DbDep, db_name: str) -> list[dict]:
    """某個業務資料庫的常用問題（每項含核可狀態與是否已過期）。"""
    return await saved_questions.list_questions(db, db_name)


@router.post("/workbench/saved-questions", dependencies=[_AuthDep])
async def save_saved_question(body: SavedQuestionRequest, db: DbDep) -> dict:
    """新增或更新。更新且語法有變時會撤銷核可——改了語法等於換了口徑。"""
    return await saved_questions.save_question(
        db, body.db_name, body.question, body.sql, body.id
    )


@router.post("/workbench/saved-questions/{question_id}/approve", dependencies=[_AdminDep])
async def approve_saved_question(
    question_id: str, body: ApproveQuestionRequest, db: DbDep, current_user: CurrentUserDep
) -> dict:
    """標記口徑已確認。這是「我背書這個算法」，不該人人都能按。"""
    actor = getattr(current_user, "email", None) if current_user else "anonymous(admin-token)"
    result = await saved_questions.approve_question(db, body.db_name, question_id, actor)
    if result is None:
        raise HTTPException(status_code=404, detail="找不到這則常用問題，可能已經被刪除了。")
    return result


@router.delete("/workbench/saved-questions/{question_id}", dependencies=[_AuthDep])
async def delete_saved_question(question_id: str, db: DbDep, db_name: str) -> dict:
    if not await saved_questions.delete_question(db, db_name, question_id):
        raise HTTPException(status_code=404, detail="找不到這則常用問題，可能已經被刪除了。")
    return {"ok": True}


@router.post("/sessions/{session_id}/validate-ddl-text", response_model=ValidateDDLTextResponse)
async def validate_ddl_text(
    session_id: uuid.UUID, body: ValidateDDLTextRequest, db: DbDep, current_user: CurrentUserDep
):
    """驗證確認頁編輯器裡尚未存檔的 DDL（validate-ddl 驗的是產出後的文件）。"""
    await _check_access(db, session_id, current_user)
    try:
        return await svc.validate_ddl_text(db, session_id, body.ddl)
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
