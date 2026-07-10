"""Global DB Agent blueprint: multi-step ReAct chat + manual query/DDL routes
for the business database(s). Moved out of app.py so the agent loop's routes
live next to the code they call (agents/agent_loop.py, agents/tool_registry.py).
"""
import logging

from flask import Blueprint, jsonify, request

from web import activity_log
from web.response_utils import hide_platform_tables, mask_db_url, sanitize_db_error

logger = logging.getLogger(__name__)

bp = Blueprint("db_agent", __name__, url_prefix="/api/db-agent")


def _resolve_biz_url(name: str | None) -> str | None:
    """Return the URL for a named DB, or the first available DB if name is None."""
    from web.app_settings import get_business_database, get_business_databases
    if name and name != "__all__":
        db = get_business_database(name)
        return db["url"] if db else None
    dbs = get_business_databases()
    return dbs[0]["url"] if dbs else None


def _get_or_create_agent_session() -> str:
    """The DB Agent has a single global conversation for now (Phase 5 auth
    will split this per-user). Its session id is persisted in app_settings so
    it survives restarts and is shared across workers."""
    from web.app_settings import get_agent_session_id, set_agent_session_id
    from web.session_store import create_session, get_session
    sid = get_agent_session_id()
    if sid and get_session(sid):
        return sid
    session = create_session("DB Agent 對話", mode="agent")
    set_agent_session_id(session["id"])
    return session["id"]


def _create_design_session(design_request: str) -> dict:
    """Kick off a new design session from a <DESIGN_REQUEST> the agent raised.
    Mirrors the interview-collecting flow used by /api/sessions/<id>/messages."""
    from agents.interviewer import Interviewer
    from web.session_store import add_message, create_session, set_tables

    session = create_session(design_request[:80], mode="design")
    sid = session["id"]
    add_message(sid, "user", design_request)

    interviewer = Interviewer()
    reply, tables, summary = interviewer.chat(design_request)
    add_message(sid, "ai", reply)

    tables_json = None
    if tables:
        set_tables(sid, tables, summary or [])
        tables_json = [
            {"table_name": t.table_name, "description": t.description,
             "columns": [{"name": c.name, "data_type": c.data_type} for c in t.columns]}
            for t in tables
        ]

    return {
        "id": sid,
        "title": design_request[:80],
        "reply": reply,
        "tables_ready": tables is not None,
        "tables": tables_json,
    }


@bp.get("/databases")
def api_db_agent_databases():
    """List configured business databases for the DB selector."""
    from web.app_settings import get_business_databases
    dbs = get_business_databases()
    return jsonify([{"name": d["name"], "masked_url": mask_db_url(d["url"])} for d in dbs])


@bp.get("/schema-tree")
def api_db_agent_schema_tree():
    """Schema browser data for the global DB Agent page.

    ?db=<name> → single DB flat list; omit or __all__ → grouped by DB.
    """
    from web.app_settings import get_business_database, get_business_databases
    from web.db_manager import schema_tree
    db_name = (request.args.get("db") or "").strip() or None

    if db_name and db_name != "__all__":
        db = get_business_database(db_name)
        if not db:
            return jsonify({"error": f"找不到資料庫：{db_name}"}), 400
        result = schema_tree(db["url"], None)
        if "error" in result:
            return jsonify({"error": sanitize_db_error(result["error"])}), 400
        result["tables"] = hide_platform_tables(result["tables"], db["url"])
        return jsonify(result)

    # All DBs grouped
    dbs = get_business_databases()
    if not dbs:
        return jsonify({"error": "尚未設定業務資料庫，請先至設定頁填入連線字串。"}), 400
    databases = []
    for db in dbs:
        r = schema_tree(db["url"], None)
        tables = r.get("tables", [])
        tables = hide_platform_tables(tables, db["url"])
        databases.append({"name": db["name"], "tables": tables})
    return jsonify({"databases": databases})


@bp.post("/chat")
def api_db_agent_chat():
    """Unified conversational endpoint: Q&A, multi-step data query, DDL suggestions.

    Runs the ReAct tool-calling loop (agents/agent_loop.run_agent_turn) and
    shapes the response to stay compatible with the old single-shot db_agent:
    reply / ddl_suggestion / query_result / query_error / design_session are
    all still present when applicable; "steps" is new (tool-call trail).
    """
    from web.app_settings import get_business_databases

    if not get_business_databases():
        return jsonify({"error": "尚未設定業務資料庫，請先至設定頁填入連線字串。"}), 400

    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    db_name = (data.get("db_name") or "").strip() or None
    if not message:
        return jsonify({"error": "message required"}), 400
    if len(message) > 2000:
        return jsonify({"error": "訊息過長（上限 2000 字）"}), 400

    from agents.agent_loop import run_agent_turn
    conversation_id = _get_or_create_agent_session()
    outcome = run_agent_turn(conversation_id, message, db_name)

    result = {
        "reply": outcome["reply"],
        "steps": outcome.get("steps", []),
        "ddl_suggestion": None,
        "query_result": None,
    }

    # Backward-compatible query_result/query_error: surface the last run_query
    # tool call this turn made (the frontend still renders a results panel).
    for step in reversed(outcome.get("steps", [])):
        if step.get("tool") != "run_query":
            continue
        step_result = step.get("result") or {}
        if "error" in step_result:
            result["query_error"] = step_result["error"]
        else:
            result["query_result"] = step_result
            result["query_sql"] = (step.get("args") or {}).get("sql", "")
            step_db = (step.get("args") or {}).get("db")
            if step_db:
                result["query_db"] = step_db
        break

    ddl = outcome.get("ddl_suggestion")
    if ddl:
        result["ddl_suggestion"] = ddl["sql"]
        result["ddl_db"] = ddl["db"] or (db_name if db_name and db_name != "__all__" else None)

    design_request = outcome.get("design_request")
    if design_request:
        result["design_session"] = _create_design_session(design_request)

    proposal = outcome.get("proposal")
    if proposal:
        result["proposal"] = proposal

    logger.info("db_agent chat", extra={"has_ddl": bool(ddl), "has_design": bool(design_request),
                                        "has_proposal": bool(proposal),
                                        "steps": len(outcome.get("steps", []))})
    activity_log.record("db_agent_chat", None, {"has_ddl": bool(ddl), "has_proposal": bool(proposal),
                                                "steps": len(outcome.get("steps", []))})
    return jsonify(result)


@bp.delete("/chat")
def api_db_agent_clear_chat():
    """Clear the global DB Agent conversation by deleting its session and
    letting the next /chat call create a fresh one."""
    from web.app_settings import get_agent_session_id, set_agent_session_id
    from web.session_store import delete_session
    sid = get_agent_session_id()
    if sid:
        delete_session(sid)
    set_agent_session_id("")
    return jsonify({"ok": True})


@bp.post("/query")
def api_db_agent_query():
    """Execute a manual read-only SQL query against a named business DB."""
    from web.db_manager import execute_query
    data = request.get_json(silent=True) or {}
    db_name = (data.get("db_name") or "").strip() or None
    sql = (data.get("sql") or "").strip()
    if not sql:
        return jsonify({"error": "sql required"}), 400
    biz_url = _resolve_biz_url(db_name)
    if not biz_url:
        return jsonify({"error": "尚未設定業務資料庫，或找不到指定的資料庫"}), 400
    result = execute_query(biz_url, sql)
    if "error" in result:
        result["error"] = sanitize_db_error(result["error"])
        return jsonify(result), 400
    return jsonify(result)
