"""HITL 結構變更提案（change request）服務層。

流程（見 docs/v2_rebuild_plan.md §4-4、docs/v05/architecture.md「人工審批」節）：
    propose_ddl / POST /change-requests
        → check_ddl_allowlist → 解析業務資料庫連線 → ddl_validator.validate_ddl（dry-run）
        → 通過才建立 status="pending" 的 change_requests 列
    POST /change-requests/{id}/approve（須 require_admin）
        → 重驗 allowlist + dry-run（防資料庫已 drift）→ 任一失敗直接 decide "failed"
        → 皆通過 → ddl_executor.execute_ddl()（單一交易）→ decide "executed"/"failed"
        → 兩種結果都寫 activity_log
    POST /change-requests/{id}/reject（須 require_admin）→ decide "rejected"，永不執行
"""

import asyncio
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.repos import activity as activity_repo
from app.repos import change_requests as change_requests_repo
from app.repos import crypto
from app.repos import settings as settings_repo
from app.repos.models import ChangeRequest
from app.rules import ddl_executor, ddl_validator, sql_safety

BUSINESS_DATABASES_KEY = "business_databases"


async def resolve_business_db(
    db: AsyncSession, name: str | None
) -> tuple[str | None, str | None, str | None]:
    """依名稱解析業務資料庫的（解密後）連線字串。

    未指定名稱時取第一個已設定的資料庫（單一資料庫情境可省略 name）。
    回傳 (resolved_name, db_url, error)；失敗時 db_url 為 None、error 為錯誤訊息。
    """
    setting = await settings_repo.get_setting(db, BUSINESS_DATABASES_KEY)
    databases: list[dict] = setting.value_json if setting and setting.value_json else []
    if not databases:
        return None, None, "尚未設定任何業務資料庫，請先在設定頁新增。"

    if name:
        match = next((d for d in databases if d.get("name") == name), None)
        if match is None:
            return None, None, f"找不到資料庫：{name}"
    else:
        match = databases[0]

    resolved_name = match.get("name")
    encrypted = match.get("db_url_encrypted")
    if not encrypted:
        return resolved_name, None, f"資料庫「{resolved_name}」未設定連線字串。"
    try:
        url = crypto.decrypt_db_url(encrypted)
    except crypto.CryptoConfigError as exc:
        return resolved_name, None, str(exc)
    return resolved_name, url, None


async def create_change_request(
    db: AsyncSession, db_name: str | None, ddl: str, reason: str = ""
) -> dict:
    """Allowlist 檢查 + dry-run 驗證皆通過才建立 pending 提案；失敗回傳 {"error": ...}。"""
    err = sql_safety.check_ddl_allowlist(ddl)
    if err:
        return {"error": err}

    resolved_name, url, err = await resolve_business_db(db, db_name)
    if err:
        return {"error": err}

    dry_run = await asyncio.to_thread(ddl_validator.validate_ddl, ddl, url)
    if not dry_run.get("ok"):
        return {"error": f"dry-run 驗證失敗：{dry_run.get('error', '未知錯誤')}"}

    record = await change_requests_repo.create_change_request(
        db, resolved_name, ddl, reason, dry_run_ok=True
    )
    return {
        "proposal_id": str(record.id),
        "dry_run_ok": True,
        "status": "pending",
        "db_name": resolved_name,
    }


async def _fail(
    db: AsyncSession, change_request_id: uuid.UUID, error: str
) -> dict:
    updated = await change_requests_repo.decide_change_request(
        db, change_request_id, status="failed", error=error
    )
    await activity_repo.log_activity(
        db, "change_request.failed", {"id": str(change_request_id), "error": error}
    )
    return {"change_request": updated, "ok": False}


async def approve_change_request(db: AsyncSession, change_request_id: uuid.UUID) -> dict:
    """核准並執行：重驗 allowlist/dry-run → 單一交易執行 DDL → 記錄結果。"""
    record = await change_requests_repo.get_change_request(db, change_request_id)
    if record is None:
        return {"error": "找不到變更提案", "not_found": True}
    if record.status != "pending":
        return {"error": f"提案狀態為「{record.status}」，僅能核准 pending 狀態的提案"}

    err = sql_safety.check_ddl_allowlist(record.ddl)
    if err:
        return await _fail(db, change_request_id, err)

    _, url, err = await resolve_business_db(db, record.db_name)
    if err:
        return await _fail(db, change_request_id, err)

    dry_run = await asyncio.to_thread(ddl_validator.validate_ddl, record.ddl, url)
    if not dry_run.get("ok"):
        error_msg = f"dry-run 驗證失敗：{dry_run.get('error', '未知錯誤')}"
        return await _fail(db, change_request_id, error_msg)

    exec_result = await asyncio.to_thread(ddl_executor.execute_ddl, url, record.ddl)
    if not exec_result.get("ok"):
        return await _fail(db, change_request_id, exec_result.get("error", "未知錯誤"))

    updated = await change_requests_repo.decide_change_request(
        db, change_request_id, status="executed"
    )
    await activity_repo.log_activity(
        db, "change_request.executed", {"id": str(change_request_id), "db_name": record.db_name}
    )
    return {"change_request": updated, "ok": True}


async def reject_change_request(db: AsyncSession, change_request_id: uuid.UUID) -> dict:
    """駁回，永不執行。"""
    record = await change_requests_repo.get_change_request(db, change_request_id)
    if record is None:
        return {"error": "找不到變更提案", "not_found": True}
    if record.status != "pending":
        return {"error": f"提案狀態為「{record.status}」，僅能駁回 pending 狀態的提案"}

    updated = await change_requests_repo.decide_change_request(
        db, change_request_id, status="rejected"
    )
    await activity_repo.log_activity(db, "change_request.rejected", {"id": str(change_request_id)})
    return {"change_request": updated, "ok": True}


def serialize_change_request(record: ChangeRequest) -> dict:
    """序列化為 API 回應用的 dict。"""
    return {
        "id": str(record.id),
        "db_name": record.db_name,
        "ddl": record.ddl,
        "reason": record.reason,
        "status": record.status,
        "dry_run_ok": record.dry_run_ok,
        "created_at": record.created_at.isoformat(),
        "decided_at": record.decided_at.isoformat() if record.decided_at else None,
        "error": record.error,
    }
