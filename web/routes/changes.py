"""Human-in-the-loop DDL change-request blueprint (Phase 4).

Replaces the direct-execution /api/db-agent/execute-ddl path: any structural
change (agent-proposed via propose_ddl, or manually submitted from a DDL
suggestion card) lands as a pending row in web/change_requests.py and only
takes effect once an admin approves it here.
"""
import logging
import os
from functools import wraps

from flask import Blueprint, jsonify, request

from web import activity_log, change_requests
from web.response_utils import sanitize_db_error

logger = logging.getLogger(__name__)

bp = Blueprint("changes", __name__, url_prefix="/api/change-requests")


def _resolve_biz_db(name: str | None):
    """Return {name, url} for the named business DB, or the first configured
    one if name is None/"__all__". Mirrors web/routes/agent.py's _resolve_biz_url."""
    from web.app_settings import get_business_database, get_business_databases
    if name and name != "__all__":
        return get_business_database(name)
    dbs = get_business_databases()
    return dbs[0] if dbs else None


def require_admin(fn):
    """Gate approve/reject behind a shared secret. No ADMIN_TOKEN configured
    -> 403 (feature disabled, tell the operator to set it up); wrong token
    presented -> 401."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        admin_token = os.environ.get("ADMIN_TOKEN")
        if not admin_token:
            return jsonify({"error": "伺服器尚未設定 ADMIN_TOKEN 環境變數，無法核准/駁回變更"}), 403
        given = request.headers.get("X-Admin-Token", "")
        if given != admin_token:
            return jsonify({"error": "管理員權杖無效"}), 401
        return fn(*args, **kwargs)
    return wrapper


@bp.get("")
def list_change_requests():
    status = (request.args.get("status") or "").strip() or None
    return jsonify(change_requests.list_requests(status))


@bp.post("")
def create_change_request():
    """Manual submission path for the DB Agent page's DDL suggestion card
    ("送審" button): runs the same allowlist + dry-run gate as propose_ddl
    before creating a pending request."""
    from web.ddl_validator import validate_ddl
    from web.sql_safety import check_ddl_allowlist

    data = request.get_json(silent=True) or {}
    db_name = (data.get("db_name") or "").strip() or None
    ddl = (data.get("ddl") or "").strip()
    reason = (data.get("reason") or "").strip()
    if not ddl:
        return jsonify({"error": "ddl required"}), 400

    db = _resolve_biz_db(db_name)
    if not db:
        return jsonify({"error": "尚未設定業務資料庫，或找不到指定的資料庫"}), 400

    safety_err = check_ddl_allowlist(ddl)
    if safety_err:
        return jsonify({"error": safety_err}), 400

    dry_run = validate_ddl(ddl, db["url"])
    if not dry_run.get("ok"):
        return jsonify({"error": dry_run.get("error", "dry-run 驗證失敗")}), 400

    req = change_requests.create(db["name"], ddl, reason, dry_run_ok=True)
    return jsonify(req), 201


@bp.post("/<request_id>/approve")
@require_admin
def approve_change_request(request_id):
    """Re-validate (allowlist + dry-run) before executing — the DB may have
    changed since the request was created — then run it inside ddl_executor's
    single transaction and record the outcome."""
    from web.ddl_executor import execute_ddl
    from web.ddl_validator import validate_ddl
    from web.sql_safety import check_ddl_allowlist

    req = change_requests.get(request_id)
    if req is None:
        return jsonify({"error": "找不到變更請求"}), 404
    if req["status"] != "pending":
        return jsonify({"error": f"此請求狀態為 {req['status']}，無法核准"}), 400

    db = _resolve_biz_db(req.get("db_name"))
    if not db:
        req = change_requests.decide(request_id, "failed", error="找不到目標資料庫，可能已被移除")
        return jsonify(req), 400

    safety_err = check_ddl_allowlist(req["ddl"])
    if safety_err:
        req = change_requests.decide(request_id, "failed", error=safety_err)
        return jsonify(req), 400

    dry_run = validate_ddl(req["ddl"], db["url"])
    if not dry_run.get("ok"):
        req = change_requests.decide(request_id, "failed", error=dry_run.get("error", "dry-run 驗證失敗"))
        return jsonify(req), 400

    result = execute_ddl(db["url"], req["ddl"])
    if not result.get("ok"):
        error = sanitize_db_error(result.get("error", "執行失敗"))
        req = change_requests.decide(request_id, "failed", error=error)
        activity_log.record("change_request_failed", None, {"request_id": request_id, "error": error})
        return jsonify(req), 400

    req = change_requests.decide(request_id, "executed")
    logger.info("change request executed", extra={"request_id": request_id,
                                                    "stmts": result.get("statements_run")})
    activity_log.record("change_request_executed", None,
                        {"request_id": request_id, "statements_run": result.get("statements_run")})
    return jsonify(req)


@bp.post("/<request_id>/reject")
@require_admin
def reject_change_request(request_id):
    req = change_requests.get(request_id)
    if req is None:
        return jsonify({"error": "找不到變更請求"}), 404
    if req["status"] != "pending":
        return jsonify({"error": f"此請求狀態為 {req['status']}，無法駁回"}), 400
    req = change_requests.decide(request_id, "rejected")
    activity_log.record("change_request_rejected", None, {"request_id": request_id})
    return jsonify(req)
