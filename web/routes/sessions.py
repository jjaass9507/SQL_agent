"""Session CRUD, chat messages, confirm/continue, versions and outputs
(Phase 5), moved out of app.py.

run_generation/run_incremental/run_review/run_single_file are called via the
`app` module (not imported by name) so `unittest.mock.patch("app.run_x")` in
tests/test_api.py keeps working unmodified — importing `app` here (rather
than `from app import run_x`) also sidesteps the app.py <-> web.routes.sessions
circular import, since attribute access happens lazily inside request handlers,
long after app.py has finished loading.
"""
import dataclasses
import io
import logging
import threading
import zipfile
from datetime import datetime, timezone

from flask import Blueprint, abort, jsonify, request, send_file

import app as app_module
from agents.interviewer import Interviewer
from web import activity_log
from web.generation_worker import EXTRA_FILES
from web.response_utils import sanitize_db_error
from web.session_store import (
    GENERATION_FILES,
    add_message,
    create_session,
    delete_session,
    get_session,
    list_sessions,
    restore_version,
    set_tables,
    tables_from_json,
    try_start_generation,
    update_session,
)

logger = logging.getLogger(__name__)

bp = Blueprint("sessions", __name__)

_interviewer_store: dict[str, Interviewer] = {}
_interviewer_lock = threading.Lock()


def _schema_summary(tables: list) -> str:
    """Compact text description of the current designed schema, fed to the
    interviewer so it can keep iterating on an existing design."""
    lines = []
    for t in tables or []:
        cols = ", ".join(
            f"{c.get('name')} {c.get('data_type', '')}"
            f"{'(PK)' if c.get('is_primary_key') else ''}"
            f"{'(FK)' if c.get('is_foreign_key') else ''}"
            for c in t.get("columns", [])
        )
        lines.append(f"- {t.get('table_name')}：{cols}")
    return "\n".join(lines)


def _get_interviewer(session_id: str) -> Interviewer:
    with _interviewer_lock:
        if session_id not in _interviewer_store:
            session = get_session(session_id) or {}
            # Existing DB structure is supplied as LLM memory (injected only when
            # the conversation touches an existing table) — not unconditionally.
            memory_text = session.get("context_text", "")
            existing_tables = [t.get("table_name", "") for t in session.get("context_tables", [])]
            existing_table_specs = tables_from_json(session.get("context_tables"))
            # If a design already exists (user is iterating on it), bake the
            # current schema into the context so a freshly-built interviewer
            # still knows what's been designed — even after a server restart.
            context = ""
            if session.get("tables"):
                summary = _schema_summary(session["tables"])
                if summary:
                    context = "使用者已完成以下資料表設計，正在繼續調整，請在此基礎上修改：\n" + summary
            _interviewer_store[session_id] = Interviewer(
                context=context,
                existing_tables=existing_tables,
                memory_text=memory_text,
                existing_table_specs=existing_table_specs,
            )
        return _interviewer_store[session_id]


@bp.post("/api/sessions")
def api_create_session():
    from web.db_introspect import extract_schema, format_context
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "未命名設計").strip()
    db_url = (data.get("db_url") or "").strip()
    db_schema = (data.get("db_schema") or "public").strip()
    mode = (data.get("mode") or "design").strip()
    if mode not in ("design", "review"):
        return jsonify({"error": "mode must be 'design' or 'review'"}), 400

    context_tables_json = []
    context_text = ""
    db_error = ""

    if db_url:
        tables, db_error = extract_schema(db_url, db_schema)
        if tables:
            context_tables_json = [dataclasses.asdict(t) for t in tables]
            context_text = format_context(tables)

    session = create_session(title, context_tables_json, context_text, mode=mode, db_url=db_url if db_url else "")
    resp = {k: v for k, v in session.items() if k != "db_url"}

    if db_error:
        resp["db_error"] = sanitize_db_error(db_error)
        logger.warning("session created with db_error", extra={"session_id": session["id"], "mode": mode})
    elif db_url and context_tables_json:
        resp["db_imported"] = len(context_tables_json)

    if mode == "review" and context_tables_json and not db_error:
        app_module.run_review(session["id"])

    logger.info("session created", extra={"session_id": session["id"], "mode": mode, "phase": session["phase"]})
    activity_log.record("session_created", session["id"],
                        {"mode": mode, "title": title, "db_imported": len(context_tables_json)})
    return jsonify(resp), 201


@bp.post("/api/sessions/<session_id>/import-db")
def api_import_db(session_id):
    from web.db_introspect import extract_schema, format_context
    session = get_session(session_id)
    if not session:
        abort(404)
    data = request.get_json(silent=True) or {}
    db_url = (data.get("db_url") or "").strip()
    db_schema = (data.get("db_schema") or "public").strip()
    if not db_url:
        return jsonify({"error": "db_url required"}), 400

    tables, error = extract_schema(db_url, db_schema)
    imported_at = datetime.now(timezone.utc).isoformat()

    if error:
        update_session(session_id, {
            "last_db_import": {"imported_at": imported_at, "table_count": 0, "error": error},
        })
        logger.error("import-db failed", extra={"session_id": session_id})
        return jsonify({"error": sanitize_db_error(error)}), 400

    context_tables_json = [dataclasses.asdict(t) for t in tables]
    context_text = format_context(tables)
    import_updates: dict = {
        "context_tables": context_tables_json,
        "context_text": context_text,
        "last_db_import": {"imported_at": imported_at, "table_count": len(tables), "error": None},
        "db_url": db_url,
    }
    # If the user re-imports while the schema is confirmed, reset to collecting
    # so they can review the new context before re-confirming
    if session.get("phase") == "confirming":
        import_updates.update({"phase": "collecting", "tables": None, "key_points": []})
    update_session(session_id, import_updates)
    with _interviewer_lock:
        _interviewer_store.pop(session_id, None)
    logger.info("import-db succeeded", extra={"session_id": session_id, "table_count": len(tables)})
    return jsonify({"imported": len(tables), "tables": [t.table_name for t in tables]})


@bp.get("/api/sessions")
def api_list_sessions():
    try:
        limit = min(int(request.args.get("limit", 50)), 200)
        offset = max(int(request.args.get("offset", 0)), 0)
    except (TypeError, ValueError):
        return jsonify({"error": "limit and offset must be integers"}), 400
    # The global DB Agent conversation (mode="agent") isn't a design/review
    # project — keep it out of the project list on the homepage.
    sessions = [s for s in list_sessions(limit=limit, offset=offset) if s.get("mode") != "agent"]
    return jsonify(sessions)


@bp.get("/api/sessions/<session_id>")
def api_get_session(session_id):
    session = get_session(session_id)
    if not session:
        abort(404)
    safe = {k: v for k, v in session.items() if k != "db_url"}
    return jsonify(safe)


@bp.patch("/api/sessions/<session_id>")
def api_update_session(session_id):
    session = get_session(session_id)
    if not session:
        abort(404)
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()[:200]
    if not title:
        return jsonify({"error": "title required"}), 400
    update_session(session_id, {"title": title})
    logger.info("session renamed", extra={"session_id": session_id})
    return jsonify({"title": title})


@bp.delete("/api/sessions/<session_id>")
def api_delete_session(session_id):
    if not delete_session(session_id):
        abort(404)
    with _interviewer_lock:
        _interviewer_store.pop(session_id, None)
    logger.info("session deleted", extra={"session_id": session_id})
    activity_log.record("session_deleted", session_id)
    return "", 204


@bp.post("/api/sessions/<session_id>/messages")
def api_send_message(session_id):
    session = get_session(session_id)
    if not session:
        abort(404)
    if session["phase"] not in ("collecting", "confirming"):
        return jsonify({"error": "session not in collecting phase"}), 400

    data = request.get_json(silent=True) or {}
    content = (data.get("content") or "").strip()
    if not content:
        return jsonify({"error": "content required"}), 400
    if len(content) > 10_000:
        return jsonify({"error": "content too long (max 10,000 characters)"}), 400

    if session["phase"] == "confirming":
        update_session(session_id, {"phase": "collecting"})

    add_message(session_id, "user", content)

    interviewer = _get_interviewer(session_id)
    reply_text, tables, summary = interviewer.chat(content)

    add_message(session_id, "ai", reply_text)

    tables_ready = tables is not None
    key_points = []

    if tables_ready:
        key_points = summary
        set_tables(session_id, tables, key_points)
        activity_log.record("requirements_completed", session_id, {"table_count": len(tables)})

        tables_json = [
            {
                "table_name": t.table_name,
                "description": t.description,
                "columns": [
                    {
                        "name": c.name,
                        "data_type": c.data_type,
                        "length": c.length,
                        "nullable": c.nullable,
                        "default": c.default,
                        "description": c.description,
                        "is_primary_key": c.is_primary_key,
                        "is_foreign_key": c.is_foreign_key,
                        "references": c.references,
                        "is_unique": c.is_unique,
                        "is_indexed": c.is_indexed,
                    }
                    for c in t.columns
                ],
                "constraints": t.constraints,
                "related_tables": t.related_tables,
            }
            for t in tables
        ]
    else:
        tables_json = None

    return jsonify({
        "reply": reply_text,
        "phase": "confirming" if tables_ready else "collecting",
        "tables_ready": tables_ready,
        "tables": tables_json,
        "key_points": key_points,
    })


@bp.put("/api/sessions/<session_id>/tables")
def api_update_tables(session_id):
    """Save a user-edited schema. Creates a new version, keeps phase=confirming."""
    from models.schema import ColumnSpec, TableSpec
    session = get_session(session_id)
    if not session:
        abort(404)
    if session["phase"] not in ("confirming", "generating", "done"):
        return jsonify({"error": "schema can only be edited after requirements are collected"}), 400

    data = request.get_json(silent=True) or {}
    raw_tables = data.get("tables")
    if not isinstance(raw_tables, list) or not raw_tables:
        return jsonify({"error": "tables must be a non-empty list"}), 400

    try:
        tables = []
        for t in raw_tables:
            name = (t.get("table_name") or "").strip()
            if not name:
                return jsonify({"error": "every table needs a table_name"}), 400
            columns = []
            for c in t.get("columns", []):
                col_name = (c.get("name") or "").strip()
                if not col_name:
                    return jsonify({"error": f"table '{name}' has a column without a name"}), 400
                columns.append(ColumnSpec(
                    name=col_name,
                    data_type=(c.get("data_type") or "text").strip(),
                    nullable=bool(c.get("nullable", True)),
                    description=(c.get("description") or "").strip(),
                    is_primary_key=bool(c.get("is_primary_key", False)),
                    is_foreign_key=bool(c.get("is_foreign_key", False)),
                    references=(c.get("references") or None),
                    is_unique=bool(c.get("is_unique", False)),
                    is_indexed=bool(c.get("is_indexed", False)),
                    length=c.get("length") or None,
                    default=(c.get("default") or None),
                ))
            if not columns:
                return jsonify({"error": f"table '{name}' has no columns"}), 400
            tables.append(TableSpec(
                table_name=name,
                description=(t.get("description") or "").strip(),
                columns=columns,
                constraints=t.get("constraints", []),
                related_tables=t.get("related_tables", []),
            ))
    except (AttributeError, TypeError) as e:
        return jsonify({"error": f"invalid table structure: {e}"}), 400

    set_tables(session_id, tables, session.get("key_points", []))
    logger.info("schema edited", extra={"session_id": session_id, "table_count": len(tables)})
    return jsonify({"status": "saved", "table_count": len(tables)})


@bp.post("/api/sessions/<session_id>/confirm")
def api_confirm(session_id):
    session = get_session(session_id)
    if not session:
        abort(404)
    if not session.get("tables"):
        return jsonify({"error": "no tables to generate"}), 400

    if not try_start_generation(session_id):
        return jsonify({"error": "session not in confirming phase"}), 400

    logger.info("generation started", extra={"session_id": session_id})
    activity_log.record("design_confirmed", session_id, {"table_count": len(session.get("tables") or [])})
    app_module.run_generation(session_id)
    return jsonify({"status": "generating"})


@bp.post("/api/sessions/<session_id>/continue")
def api_continue(session_id):
    """Re-open a confirmed/generated design for further iteration via chat."""
    session = get_session(session_id)
    if not session:
        abort(404)
    if session.get("mode") == "review":
        return jsonify({"error": "review sessions cannot be iterated this way"}), 400
    if not session.get("tables"):
        return jsonify({"error": "no design to continue"}), 400
    # Back to collecting; existing messages + schema-aware interviewer let the
    # user say e.g. "加一張 order_items 表" and refine the design incrementally.
    update_session(session_id, {"phase": "collecting"})
    with _interviewer_lock:
        _interviewer_store.pop(session_id, None)  # rebuild with current-schema context
    logger.info("design reopened for iteration", extra={"session_id": session_id})
    return jsonify({"status": "collecting"})


# ── Review restart ─────────────────────────────────────

@bp.post("/api/sessions/<session_id>/review/restart")
def api_review_restart(session_id):
    session = get_session(session_id)
    if not session:
        abort(404)
    if session.get("mode") != "review":
        return jsonify({"error": "not a review session"}), 400
    update_session(session_id, {"phase": "reviewing", "outputs": {}})
    app_module.run_review(session_id)
    logger.info("review restarted", extra={"session_id": session_id})
    return jsonify({"status": "reviewing"})


# ── Per-file regeneration ───────────────────────────────

@bp.post("/api/sessions/<session_id>/outputs/<filename>/regenerate")
def api_regenerate_output(session_id, filename):
    session = get_session(session_id)
    if not session:
        abort(404)
    if filename not in GENERATION_FILES:
        return jsonify({"error": "invalid filename"}), 400
    if not session.get("tables"):
        return jsonify({"error": "no tables"}), 400
    if session.get("generation_status", {}).get(filename) == "loading":
        return jsonify({"error": "already regenerating"}), 409
    app_module.run_single_file(session_id, filename)
    logger.info("file regeneration started", extra={"session_id": session_id, "output_file": filename})
    return jsonify({"status": "regenerating"})


# ── On-demand extra outputs (ORM / migration / queries) ──

@bp.post("/api/sessions/<session_id>/extras/<kind>/generate")
def api_generate_extra(session_id, kind):
    session = get_session(session_id)
    if not session:
        abort(404)
    filename = EXTRA_FILES.get(kind)
    if not filename:
        return jsonify({"error": "invalid extra kind"}), 400
    if not session.get("tables"):
        return jsonify({"error": "no tables"}), 400
    if session.get("generation_status", {}).get(filename) == "loading":
        return jsonify({"error": "already generating"}), 409
    if kind == "incremental":
        # Needs an existing DB to diff the design against
        if not session.get("context_tables"):
            return jsonify({"error": "需要匯入現有 DB 才能產生增量 migration"}), 400
        app_module.run_incremental(session_id)
    else:
        app_module.run_single_file(session_id, filename)
    logger.info("extra generation started", extra={"session_id": session_id, "output_file": filename})
    return jsonify({"status": "generating", "filename": filename})


# ── Version management ──────────────────────────────────

@bp.get("/api/sessions/<session_id>/versions")
def api_list_versions(session_id):
    session = get_session(session_id)
    if not session:
        abort(404)
    versions = session.get("table_versions", [])
    return jsonify([
        {
            "version": v["version"],
            "created_at": v["created_at"],
            "table_count": len(v["tables"]) if v.get("tables") else 0,
        }
        for v in versions
    ])


@bp.post("/api/sessions/<session_id>/versions/<int:version_num>/restore")
def api_restore_version(session_id, version_num):
    if not restore_version(session_id, version_num):
        return jsonify({"error": "version not found"}), 404
    return jsonify({"status": "restored", "version": version_num})


# ── Outputs ─────────────────────────────────────────────

@bp.get("/api/sessions/<session_id>/outputs")
def api_get_outputs(session_id):
    session = get_session(session_id)
    if not session:
        abort(404)
    return jsonify({
        "outputs": session.get("outputs", {}),
        "generation_status": session.get("generation_status", {}),
        "generation_errors": session.get("generation_errors", {}),
    })


@bp.get("/api/sessions/<session_id>/outputs/zip")
def api_download_zip(session_id):
    session = get_session(session_id)
    if not session:
        abort(404)
    outputs = session.get("outputs", {})
    if not outputs:
        return jsonify({"error": "no outputs yet"}), 400

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for filename, content in outputs.items():
            zf.writestr(filename, content)
    buf.seek(0)

    title = session.get("title", "output").replace(" ", "_")
    return send_file(
        buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"{title}.zip",
    )
