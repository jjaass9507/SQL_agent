import io
import json as _json
import logging
import re
import threading
import zipfile
from datetime import datetime, timezone

from dotenv import load_dotenv
from flask import Flask, abort, jsonify, redirect, render_template, request, send_file, url_for

load_dotenv()

app = Flask(__name__)

VERSION = "0.5.0"


# ── Structured JSON logging ─────────────────────────────

class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict = {
            "ts": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        # Merge any extra fields attached via extra={...}
        for key, val in vars(record).items():
            if key not in logging.LogRecord.__dict__ and not key.startswith("_"):
                entry[key] = val
        return _json.dumps(entry, ensure_ascii=False)


def _setup_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(_JsonFormatter())
    logging.root.setLevel(logging.INFO)
    logging.root.handlers = [handler]


_setup_logging()
logger = logging.getLogger(__name__)


from agents.interviewer import Interviewer
from web.session_store import (
    add_message,
    create_session,
    delete_session,
    get_session,
    get_tables,
    list_sessions,
    restore_version,
    set_tables,
    tables_from_json,
    update_session,
    update_generation_status,
    try_start_generation,
    GENERATION_FILES,
)
from web.generation_worker import run_generation, run_review, run_single_file, EXTRA_FILES

_interviewer_store: dict[str, Interviewer] = {}
_interviewer_lock = threading.Lock()


def _sanitize_db_error(msg: str) -> str:
    """Strip credentials and host details from DB error messages."""
    msg = re.sub(r'postgresql://[^\s]+', 'postgresql://...', msg)
    msg = re.sub(r'\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b', '...', msg)
    return msg[:300]


@app.errorhandler(404)
def _not_found(e):
    return jsonify({"error": "not found"}), 404


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
            session = get_session(session_id)
            context = session.get("context_text", "") if session else ""
            # If a design already exists (user is iterating on it), bake the
            # current schema into the context so a freshly-built interviewer
            # still knows what's been designed — even after a server restart.
            if session and session.get("tables"):
                summary = _schema_summary(session["tables"])
                if summary:
                    context = (context + "\n\n" if context else "") + \
                        "使用者已完成以下資料表設計，正在繼續調整，請在此基礎上修改：\n" + summary
            _interviewer_store[session_id] = Interviewer(context=context)
        return _interviewer_store[session_id]


# ── System routes ───────────────────────────────────────

@app.get("/health")
def health():
    return jsonify({"status": "ok", "version": VERSION})


# ── Page routes ─────────────────────────────────────────

@app.get("/")
def index():
    return render_template("index.html")


@app.get("/sessions/<session_id>/chat")
def chat_page(session_id):
    session = get_session(session_id)
    if not session:
        return redirect(url_for("index"))
    if session["phase"] in ("confirming", "generating", "done"):
        return redirect(url_for("confirm_page", session_id=session_id))
    return render_template("chat.html", session=session)


@app.get("/sessions/<session_id>/confirm")
def confirm_page(session_id):
    session = get_session(session_id)
    if not session:
        return redirect(url_for("index"))
    if session["phase"] in ("generating", "done"):
        return redirect(url_for("docs_page", session_id=session_id))
    if session["phase"] == "collecting":
        return redirect(url_for("chat_page", session_id=session_id))

    diff = None
    warnings = []
    if session.get("tables"):
        from web.schema_advisor import analyze
        designed = tables_from_json(session["tables"])
        warnings = analyze(designed)
        if session.get("context_tables"):
            from web.schema_diff import compute_diff
            existing = tables_from_json(session["context_tables"])
            diff = compute_diff(designed, existing)

    return render_template("confirm.html", session=session, diff=diff, warnings=warnings)


@app.get("/sessions/<session_id>/docs")
def docs_page(session_id):
    session = get_session(session_id)
    if not session:
        return redirect(url_for("index"))
    if session.get("mode") == "review":
        return redirect(url_for("review_page", session_id=session_id))
    # Guard against landing on the docs page before generation has started,
    # otherwise the user sees 4 cards spinning on "等待產出" forever.
    if session["phase"] == "collecting":
        return redirect(url_for("chat_page", session_id=session_id))
    if session["phase"] == "confirming":
        return redirect(url_for("confirm_page", session_id=session_id))
    return render_template("docs.html", session=session)


@app.get("/sessions/<session_id>/review")
def review_page(session_id):
    session = get_session(session_id)
    if not session:
        return redirect(url_for("index"))
    if session.get("mode") != "review":
        return redirect(url_for("index"))
    return render_template("review.html", session=session)


# ── API routes ──────────────────────────────────────────

@app.post("/api/sessions")
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
        import dataclasses
        tables, db_error = extract_schema(db_url, db_schema)
        if tables:
            context_tables_json = [dataclasses.asdict(t) for t in tables]
            context_text = format_context(tables)

    session = create_session(title, context_tables_json, context_text, mode=mode, db_url=db_url if db_url else "")
    resp = {k: v for k, v in session.items() if k != "db_url"}

    if db_error:
        resp["db_error"] = _sanitize_db_error(db_error)
        logger.warning("session created with db_error", extra={"session_id": session["id"], "mode": mode})
    elif db_url and context_tables_json:
        resp["db_imported"] = len(context_tables_json)

    if mode == "review" and context_tables_json and not db_error:
        run_review(session["id"])

    logger.info("session created", extra={"session_id": session["id"], "mode": mode, "phase": session["phase"]})
    return jsonify(resp), 201


@app.post("/api/sessions/<session_id>/import-db")
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

    import dataclasses
    tables, error = extract_schema(db_url, db_schema)
    imported_at = datetime.now(timezone.utc).isoformat()

    if error:
        update_session(session_id, {
            "last_db_import": {"imported_at": imported_at, "table_count": 0, "error": error},
        })
        logger.error("import-db failed", extra={"session_id": session_id})
        return jsonify({"error": _sanitize_db_error(error)}), 400

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


@app.get("/api/sessions")
def api_list_sessions():
    try:
        limit = min(int(request.args.get("limit", 50)), 200)
        offset = max(int(request.args.get("offset", 0)), 0)
    except (TypeError, ValueError):
        return jsonify({"error": "limit and offset must be integers"}), 400
    return jsonify(list_sessions(limit=limit, offset=offset))


@app.get("/api/sessions/<session_id>")
def api_get_session(session_id):
    session = get_session(session_id)
    if not session:
        abort(404)
    safe = {k: v for k, v in session.items() if k != "db_url"}
    return jsonify(safe)


@app.patch("/api/sessions/<session_id>")
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


@app.delete("/api/sessions/<session_id>")
def api_delete_session(session_id):
    if not delete_session(session_id):
        abort(404)
    with _interviewer_lock:
        _interviewer_store.pop(session_id, None)
    logger.info("session deleted", extra={"session_id": session_id})
    return "", 204


@app.post("/api/sessions/<session_id>/messages")
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


@app.put("/api/sessions/<session_id>/tables")
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


@app.post("/api/sessions/<session_id>/confirm")
def api_confirm(session_id):
    session = get_session(session_id)
    if not session:
        abort(404)
    if not session.get("tables"):
        return jsonify({"error": "no tables to generate"}), 400

    if not try_start_generation(session_id):
        return jsonify({"error": "session not in confirming phase"}), 400

    logger.info("generation started", extra={"session_id": session_id})
    run_generation(session_id)
    return jsonify({"status": "generating"})


@app.post("/api/sessions/<session_id>/continue")
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

@app.post("/api/sessions/<session_id>/review/restart")
def api_review_restart(session_id):
    session = get_session(session_id)
    if not session:
        abort(404)
    if session.get("mode") != "review":
        return jsonify({"error": "not a review session"}), 400
    update_session(session_id, {"phase": "reviewing", "outputs": {}})
    run_review(session_id)
    logger.info("review restarted", extra={"session_id": session_id})
    return jsonify({"status": "reviewing"})


# ── Per-file regeneration ───────────────────────────────

@app.post("/api/sessions/<session_id>/outputs/<filename>/regenerate")
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
    run_single_file(session_id, filename)
    logger.info("file regeneration started", extra={"session_id": session_id, "output_file": filename})
    return jsonify({"status": "regenerating"})


# ── On-demand extra outputs (ORM / migration / queries) ──

@app.post("/api/sessions/<session_id>/extras/<kind>/generate")
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
    run_single_file(session_id, filename)
    logger.info("extra generation started", extra={"session_id": session_id, "output_file": filename})
    return jsonify({"status": "generating", "filename": filename})


# ── Version management ──────────────────────────────────

@app.get("/api/sessions/<session_id>/versions")
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


@app.post("/api/sessions/<session_id>/versions/<int:version_num>/restore")
def api_restore_version(session_id, version_num):
    if not restore_version(session_id, version_num):
        return jsonify({"error": "version not found"}), 404
    return jsonify({"status": "restored", "version": version_num})


# ── Outputs ─────────────────────────────────────────────

@app.get("/api/sessions/<session_id>/outputs")
def api_get_outputs(session_id):
    session = get_session(session_id)
    if not session:
        abort(404)
    return jsonify({
        "outputs": session.get("outputs", {}),
        "generation_status": session.get("generation_status", {}),
        "generation_errors": session.get("generation_errors", {}),
    })


@app.get("/api/sessions/<session_id>/outputs/zip")
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


@app.post("/api/sessions/<session_id>/query")
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
        result["error"] = _sanitize_db_error(result["error"])
        return jsonify(result), 400
    logger.info("SQL query executed", extra={"session_id": session_id, "sql_len": len(sql)})
    return jsonify(result)


@app.get("/api/sessions/<session_id>/schema-tree")
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


@app.post("/api/sessions/<session_id>/explain")
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
        result["error"] = _sanitize_db_error(result["error"])
        return jsonify(result), 400
    return jsonify(result)


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
