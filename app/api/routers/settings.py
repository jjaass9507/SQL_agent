"""平台設定（記憶後端狀態 + 業務資料庫連線）與稽核紀錄 API。

本次工作範圍只允許新增 `app/api/schemas/workbench.py`（見任務檔案範圍），
沒有 `app/api/schemas/settings.py`，因此本檔的 request/response Pydantic
models 就地定義，不額外開新 schema 檔。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, require_admin_role
from app.services import settings_service as svc

router = APIRouter(tags=["settings"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
# 業務資料庫連線的增刪＝變更平台指向哪個正式庫，比照 change-requests 的審批門檻。
# AUTH_ENABLED=false 時走 ADMIN_TOKEN（未設定即 403，fail-closed）；true 時要求 JWT role=admin。
_AdminDep = Depends(require_admin_role)
# 讀取類端點：匿名模式維持開放（get_current_user 回 None 不擋），
# AUTH_ENABLED=true 時要求已登入——稽核紀錄與平台設定不該任人讀。
_AuthDep = Depends(get_current_user)


class BusinessDatabaseOut(BaseModel):
    name: str
    masked_url: str


class LLMBackendOut(BaseModel):
    id: str
    label: str
    configured: bool


class SettingsOut(BaseModel):
    configured: bool
    backend: str
    masked_url: str
    business_databases: list[BusinessDatabaseOut]
    agent_max_tool_calls: int
    agent_max_tool_calls_min: int
    agent_max_tool_calls_max: int
    llm_backend: str
    llm_backends: list[LLMBackendOut]


class BusinessDatabaseIn(BaseModel):
    name: str
    url: str


class BusinessDatabasesOut(BaseModel):
    business_databases: list[BusinessDatabaseOut]


class AgentSettingsIn(BaseModel):
    max_tool_calls: int


class AgentSettingsOut(BaseModel):
    max_tool_calls: int


class LLMBackendIn(BaseModel):
    backend: str


class LLMBackendSelectionOut(BaseModel):
    backend: str


class ActivityEntry(BaseModel):
    id: str
    event: str
    detail: dict | None = None
    created_at: str


@router.get("/settings", response_model=SettingsOut, dependencies=[_AuthDep])
async def get_settings_route(db: DbDep):
    return await svc.get_settings_overview(db)


@router.put("/settings/agent", response_model=AgentSettingsOut, dependencies=[_AdminDep])
async def update_agent_settings(body: AgentSettingsIn, db: DbDep):
    try:
        value = await svc.set_agent_max_tool_calls(db, body.max_tool_calls)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {"max_tool_calls": value}


@router.put(
    "/settings/llm-backend", response_model=LLMBackendSelectionOut, dependencies=[_AdminDep]
)
async def update_llm_backend(body: LLMBackendIn, db: DbDep):
    try:
        backend = await svc.set_llm_backend(db, body.backend)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {"backend": backend}


@router.post(
    "/settings/business-db", response_model=BusinessDatabasesOut, dependencies=[_AdminDep]
)
async def add_business_db(body: BusinessDatabaseIn, db: DbDep):
    name = body.name.strip()
    url = body.url.strip()
    if not name:
        raise HTTPException(status_code=400, detail="請填入資料庫名稱")
    if not url:
        raise HTTPException(status_code=400, detail="請填入連線字串")
    try:
        entries = await svc.upsert_business_database(db, name, url)
    except svc.ConnectionTestFailed as exc:
        raise HTTPException(status_code=400, detail=f"連線失敗：{exc}") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {"business_databases": entries}


@router.delete(
    "/settings/business-db", response_model=BusinessDatabasesOut, dependencies=[_AdminDep]
)
async def remove_business_db(db: DbDep, name: str = Query(...)):
    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="請填寫資料庫名稱。")
    entries = await svc.remove_business_database(db, name)
    return {"business_databases": entries}


@router.get("/activity", response_model=list[ActivityEntry], dependencies=[_AuthDep])
async def get_activity(db: DbDep, limit: int = Query(default=100, ge=1, le=500)):
    records = await svc.list_activity(db, limit=limit)
    return [
        {
            "id": str(r.id),
            "event": r.event,
            "detail": r.detail_json,
            "created_at": r.created_at.isoformat(),
        }
        for r in records
    ]
