"""Platform settings + activity log routes (Phase 5), moved out of app.py.

/api/settings, /api/settings/business-db and /api/activity — configuring the
memory-backend database and named business databases used by the DB Agent.
"""
import logging

from flask import Blueprint, jsonify, request

from web import activity_log
from web.response_utils import mask_db_url, sanitize_db_error

logger = logging.getLogger(__name__)

bp = Blueprint("settings", __name__)


@bp.get("/api/settings")
def api_get_settings():
    from web.app_settings import (
        get_database_url, get_platform_schema,
        get_business_databases,
    )
    url = get_database_url()
    dbs = get_business_databases()
    return jsonify({
        "configured": bool(url),
        "backend": "postgresql" if url else "json",
        "masked_url": mask_db_url(url),
        "platform_schema": get_platform_schema(),
        "business_databases": [
            {"name": d["name"], "masked_url": mask_db_url(d["url"])}
            for d in dbs
        ],
    })


@bp.get("/api/llm/health")
def api_llm_health():
    """LLM 連線診斷：實際打一次 gateway，回傳成功或完整失敗原因。"""
    from utils.client import get_api
    try:
        api = get_api()
    except RuntimeError as e:
        return jsonify({"ok": False, "error": str(e)}), 503
    result = api.ping()
    if not result.get("ok"):
        logger.error("LLM health check failed", extra={"detail": result.get("error")})
        return jsonify(result), 503
    probe = api.probe_system_prompt()
    result["system_mode"] = api.system_mode
    result["system_prompt_honored"] = probe.get("honored")
    if probe.get("honored") is False:
        if api.system_mode == "system":
            result["hint"] = "此 gateway 疑似忽略 system 訊息，請在 .env 設 LLM_SYSTEM_MODE=inline 後重啟"
        else:
            result["hint"] = "inline 模式下模型仍未遵循指令，請確認 LLM_MODEL 對應的模型能力"
    return jsonify(result)


@bp.get("/api/activity")
def api_activity():
    """Recent platform usage records from the configured database (empty in JSON mode)."""
    try:
        limit = min(int(request.args.get("limit", 100)), 500)
    except (TypeError, ValueError):
        limit = 100
    return jsonify(activity_log.recent(limit=limit))


@bp.post("/api/settings")
def api_set_settings():
    """Set (or clear) the database used as the platform's memory."""
    from sqlalchemy import text
    from web.app_settings import set_database_url, set_platform_schema
    from web.db_engine import get_engine
    from web.db_schema import ensure_schema

    data = request.get_json(silent=True) or {}
    url = (data.get("database_url") or "").strip()
    platform_schema = (data.get("platform_schema") or "public").strip() or "public"

    if not url:
        set_database_url("")
        set_platform_schema(platform_schema)
        logger.info("settings: database cleared, reverting to JSON memory")
        return jsonify({"configured": False, "backend": "json", "masked_url": "",
                        "platform_schema": platform_schema})

    if not url.startswith(("postgresql://", "postgres://")):
        return jsonify({"error": "僅支援 PostgreSQL 連線字串（postgresql://...）"}), 400

    try:
        set_database_url(url)
        set_platform_schema(platform_schema)
        engine = get_engine()
        ensure_schema(engine, platform_schema)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as e:
        set_database_url("")
        logger.warning("settings: db connection failed", extra={"err": str(e)[:200]})
        return jsonify({"error": f"連線失敗：{sanitize_db_error(str(e))}"}), 400

    logger.info("settings: database configured as memory backend")
    activity_log.record("settings_db_configured", None, {"masked_url": mask_db_url(url)})
    return jsonify({"configured": True, "backend": "postgresql", "masked_url": mask_db_url(url),
                    "platform_schema": platform_schema})


@bp.post("/api/settings/business-db")
def api_add_business_db():
    """Add or replace a named business database for the global DB Agent."""
    from web.app_settings import add_business_database, get_business_databases
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    url = (data.get("url") or "").strip()

    if not name:
        return jsonify({"error": "請填入資料庫名稱"}), 400
    if not url:
        return jsonify({"error": "請填入連線字串"}), 400
    if not url.startswith(("postgresql://", "postgres://")):
        return jsonify({"error": "僅支援 PostgreSQL 連線字串（postgresql://...）"}), 400

    try:
        import psycopg2
        conn = psycopg2.connect(url, connect_timeout=10)
        conn.close()
    except Exception as e:
        return jsonify({"error": f"連線失敗：{sanitize_db_error(str(e))}"}), 400

    add_business_database(name, url)

    logger.info("settings: business DB added", extra={"name": name, "masked": mask_db_url(url)})
    activity_log.record("business_db_configured", None, {"name": name, "masked_url": mask_db_url(url)})
    try:
        dbs = get_business_databases()
        return jsonify({
            "business_databases": [
                {"name": d.get("name", ""), "masked_url": mask_db_url(d.get("url", ""))}
                for d in dbs
            ]
        })
    except Exception as e:
        logger.error("api_add_business_db: response build failed: %s", e)
        return jsonify({"error": "內部錯誤，請重新整理頁面"}), 500


@bp.delete("/api/settings/business-db")
def api_remove_business_db():
    """Remove a named business database."""
    from web.app_settings import remove_business_database, get_business_databases
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400

    remove_business_database(name)

    logger.info("settings: business DB removed", extra={"name": name})
    try:
        dbs = get_business_databases()
        return jsonify({
            "business_databases": [
                {"name": d.get("name", ""), "masked_url": mask_db_url(d.get("url", ""))}
                for d in dbs
            ]
        })
    except Exception as e:
        logger.error("api_remove_business_db: response build failed: %s", e)
        return jsonify({"error": "內部錯誤，請重新整理頁面"}), 500
