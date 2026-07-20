"""平台設定（記憶後端狀態 + 業務資料庫連線）與稽核紀錄 API。

本次工作範圍只允許新增 `app/api/schemas/workbench.py`（見任務檔案範圍），
沒有 `app/api/schemas/settings.py`，因此本檔的 request/response Pydantic
models 就地定義，不額外開新 schema 檔。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.services import settings_service as svc

router = APIRouter(tags=["settings"])

DbDep = Annotated[AsyncSession, Depends(get_db)]


class BusinessDatabaseOut(BaseModel):
    name: str
    masked_url: str
    default_schema: str | None = None


class SettingsOut(BaseModel):
    configured: bool
    backend: str
    masked_url: str
    business_databases: list[BusinessDatabaseOut]


class BusinessDatabaseIn(BaseModel):
    name: str
    url: str
    default_schema: str | None = None


class BusinessDatabasesOut(BaseModel):
    business_databases: list[BusinessDatabaseOut]


class ActivityEntry(BaseModel):
    id: str
    event: str
    detail: dict | None = None
    created_at: str


@router.get("/settings", response_model=SettingsOut)
async def get_settings_route(db: DbDep):
    return await svc.get_settings_overview(db)


@router.post("/settings/business-db", response_model=BusinessDatabasesOut)
async def add_business_db(body: BusinessDatabaseIn, db: DbDep):
    name = body.name.strip()
    url = body.url.strip()
    if not name:
        raise HTTPException(status_code=400, detail="請填入資料庫名稱")
    if not url:
        raise HTTPException(status_code=400, detail="請填入連線字串")
    try:
        entries = await svc.upsert_business_database(
            db, name, url, default_schema=(body.default_schema or "").strip() or None
        )
    except svc.ConnectionTestFailed as exc:
        raise HTTPException(status_code=400, detail=f"連線失敗：{exc}") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {"business_databases": entries}


@router.delete("/settings/business-db", response_model=BusinessDatabasesOut)
async def remove_business_db(db: DbDep, name: str = Query(...)):
    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    entries = await svc.remove_business_database(db, name)
    return {"business_databases": entries}


@router.get("/activity", response_model=list[ActivityEntry])
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
