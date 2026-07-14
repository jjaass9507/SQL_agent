"""HITL 結構變更提案 API：`/change-requests`（建立 / 列表 / 核准 / 駁回）。"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, require_admin_role
from app.repos import change_requests as change_requests_repo
from app.services import change_service

router = APIRouter(prefix="/change-requests", tags=["change-requests"])

# 模組層級單例：避免 B008（Depends() 直接寫在參數預設值會被 lint 擋下）。
_DbDep = Depends(get_db)
# AUTH_ENABLED=false：比照舊 ADMIN_TOKEN 機制；true：改要求 JWT role=admin
# （ADMIN_TOKEN 僅在認證關閉時作為過渡機制，見 app/api/deps.py::require_admin_role）。
_AdminDep = Depends(require_admin_role)


class CreateChangeRequestBody(BaseModel):
    # 省略 db_name 時取第一個已設定的業務資料庫（同 change_service.resolve_business_db）
    db_name: str | None = None
    ddl: str
    reason: str = ""


def _decision_response(result: dict) -> dict:
    if result.get("not_found"):
        raise HTTPException(status_code=404, detail=result["error"])
    if "change_request" not in result:
        raise HTTPException(status_code=409, detail=result["error"])
    return change_service.serialize_change_request(result["change_request"])


@router.post("", status_code=201)
async def create_change_request(body: CreateChangeRequestBody, db: AsyncSession = _DbDep) -> dict:
    result = await change_service.create_change_request(db, body.db_name, body.ddl, body.reason)
    if "error" in result:
        raise HTTPException(status_code=422, detail=result["error"])
    return result


@router.get("")
async def list_change_requests(status: str | None = None, db: AsyncSession = _DbDep) -> list[dict]:
    records = await change_requests_repo.list_change_requests(db, status=status)
    return [change_service.serialize_change_request(r) for r in records]


@router.post("/{change_request_id}/approve", dependencies=[_AdminDep])
async def approve_change_request(change_request_id: uuid.UUID, db: AsyncSession = _DbDep) -> dict:
    result = await change_service.approve_change_request(db, change_request_id)
    return _decision_response(result)


@router.post("/{change_request_id}/reject", dependencies=[_AdminDep])
async def reject_change_request(change_request_id: uuid.UUID, db: AsyncSession = _DbDep) -> dict:
    result = await change_service.reject_change_request(db, change_request_id)
    return _decision_response(result)
