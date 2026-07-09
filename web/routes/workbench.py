"""SQL workbench routes (Phase 5), moved out of app.py: manual query/EXPLAIN,
NL2SQL, DDL dry-run validation, the schema browser, and DDL-paste import.
"""
import logging

from flask import Blueprint, abort, jsonify, request

from web import activity_log
from web.response_utils import hide_platform_tables, sanitize_db_error
from web.session_store import create_session, get_session, set_tables

logger = logging.getLogger(__name__)

bp = Blueprint("workbench", __name__)


@bp.post("/api/ddl-import")
def api_ddl_import():
    """Create a new design session from pasted CREATE TABLE DDL.

    Skips the interview phase and lands directly on the confirm page so the
    user can review/refine the parsed schema.
    """
    from web.ddl_parser import parse_ddl
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "DDL 匯入設計").strip()[:120]
    ddl_text = (data.get("ddl") or "").strip()
    if not ddl_text:
        return jsonify({"error": "ddl required"}), 400
    if len(ddl_text) > 100_000:
        return jsonify({"error": "DDL 內容過長"}), 400
    tables = parse_ddl(ddl_text)
    if not tables:
        return jsonify({"error": "未能解析出任何 CREATE TABLE 語句，請確認 DDL 格式"}), 400
    session = create_session(title, mode="design")
    set_tables(session["id"], tables, [f"從 DDL 匯入 {len(tables)} 個資料表，可在此調整後產出文件"])
    logger.info("ddl-import", extra={"session_id": session["id"], "table_count": len(tables)})
    activity_log.record("ddl_imported", session["id"], {"table_count": len(tables)})
    return jsonify({"id": session["id"], "table_count": len(tables)}), 201


@bp.post("/api/sessions/<session_id>/query")
def api_query(session_id):
    """Execute a read-only SQL query against the session's target database."""
    from web.db_manager import execute_query
    session = get_session(session_id)
    if not session:
        abort(404)
    db_url = session.get("db_url") or ""
    if not db_url:
        return jsonify({"error": "no database URL configured for this session"}), 400
    data = request.get_json(silent=True) or {}
    sql = (data.get("sql") or "").strip()
    if not sql:
        return jsonify({"error": "sql required"}), 400
    result = execute_query(db_url, sql)
    if "error" in result:
        result["error"] = sanitize_db_error(result["error"])
        return jsonify(result), 400
    logger.info("SQL query executed", extra={"session_id": session_id, "sql_len": len(sql)})
    activity_log.record("query_executed", session_id, {"rows": len(result.get("rows", []))})
    return jsonify(result)


@bp.get("/api/sessions/<session_id>/schema-tree")
def api_schema_tree(session_id):
    """Schema browser data for the SQL workbench.

    Uses the live target DB when the session has a db_url; otherwise falls back
    to the designed tables so the browser is useful even before any DB is wired.
    """
    session = get_session(session_id)
    if not session:
        abort(404)
    db_url = session.get("db_url") or ""
    if db_url:
        from web.db_manager import schema_tree
        result = schema_tree(db_url)
        if "error" not in result and result.get("tables"):
            result["tables"] = hide_platform_tables(result["tables"], db_url)
            result["source"] = "db"
            return jsonify(result)
        # fall through to designed tables on introspection failure

    designed = []
    for t in (session.get("tables") or []):
        cols = []
        for col in t.get("columns", []):
            ref = col.get("references")
            if isinstance(ref, dict):
                ref = ref.get("table")
            length = col.get("length")
            dtype = col.get("data_type", "")
            cols.append({
                "name": col.get("name", ""),
                "type": f"{dtype}({length})" if length else dtype,
                "nullable": col.get("nullable", True),
                "is_pk": col.get("is_primary_key", False),
                "is_fk": col.get("is_foreign_key", False),
                "fk_table": ref,
            })
        designed.append({"name": t.get("table_name", ""), "columns": cols})
    return jsonify({"source": "design", "tables": designed})


@bp.post("/api/sessions/<session_id>/explain")
def api_explain(session_id):
    """Run EXPLAIN on a SQL query against the session's target database."""
    from web.db_manager import explain_query
    session = get_session(session_id)
    if not session:
        abort(404)
    db_url = session.get("db_url") or ""
    if not db_url:
        return jsonify({"error": "no database URL configured for this session"}), 400
    data = request.get_json(silent=True) or {}
    sql = (data.get("sql") or "").strip()
    if not sql:
        return jsonify({"error": "sql required"}), 400
    result = explain_query(db_url, sql)
    if "error" in result:
        result["error"] = sanitize_db_error(result["error"])
        return jsonify(result), 400
    return jsonify(result)


@bp.post("/api/sessions/<session_id>/nl2sql")
def api_nl2sql(session_id):
    """Generate a read-only SELECT from a natural-language question (workbench)."""
    from web.db_manager import schema_tree
    from web.nl2sql import generate_sql, format_schema
    session = get_session(session_id)
    if not session:
        abort(404)
    db_url = session.get("db_url") or ""
    if not db_url:
        return jsonify({"error": "此 session 未設定資料庫連線"}), 400
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"error": "question required"}), 400
    if len(question) > 2000:
        return jsonify({"error": "問題過長（上限 2000 字）"}), 400
    tree = schema_tree(db_url)
    if "error" in tree:
        return jsonify({"error": sanitize_db_error(tree["error"])}), 400
    tables = hide_platform_tables(tree.get("tables", []), db_url)
    result = generate_sql(question, format_schema(tables))
    if "error" in result:
        return jsonify(result), 400
    logger.info("nl2sql generated", extra={"session_id": session_id})
    activity_log.record("nl2sql_generated", session_id, {"q_len": len(question)})
    return jsonify(result)


@bp.post("/api/sessions/<session_id>/validate-ddl")
def api_validate_ddl(session_id):
    """Dry-run the generated DDL against a real PostgreSQL (rolled back)."""
    from web.ddl_validator import validate_ddl
    from web.app_settings import get_database_url
    session = get_session(session_id)
    if not session:
        abort(404)
    ddl = (session.get("outputs", {}) or {}).get("03_ddl.sql", "")
    if not ddl.strip():
        return jsonify({"ok": False, "error": "尚未產生 DDL，請先完成文件產出"}), 400
    # Prefer the session's own DB; fall back to the platform's configured PostgreSQL
    conn_url = session.get("db_url") or get_database_url()
    if not conn_url:
        return jsonify({"ok": False,
                        "error": "需要一個 PostgreSQL 連線（session 的資料庫或設定頁的平台資料庫）才能驗證 DDL"}), 400
    result = validate_ddl(ddl, conn_url)
    if not result.get("ok"):
        result["error"] = sanitize_db_error(result.get("error", ""))
    logger.info("ddl validated", extra={"session_id": session_id, "ok": result.get("ok")})
    return jsonify(result)
