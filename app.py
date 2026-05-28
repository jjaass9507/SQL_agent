import io
import json as _json
import logging
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
from web.generation_worker import run_generation, run_review

_interviewer_store: dict[str, Interviewer] = {}
_interviewer_lock = threading.Lock()


def _get_interviewer(session_id: str) -> Interviewer:
    with _interviewer_lock:
        if session_id not in _interviewer_store:
            session = get_session(session_id)
            context = session.get("context_text", "") if session else ""
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
    if session.get("tables") and session.get("context_tables"):
        from web.schema_diff import compute_diff
        designed = tables_from_json(session["tables"])
        existing = tables_from_json(session["context_tables"])
        diff = compute_diff(designed, existing)

    return render_template("confirm.html", session=session, diff=diff)


@app.get("/sessions/<session_id>/docs")
def docs_page(session_id):
    session = get_session(session_id)
    if not session:
        return redirect(url_for("index"))
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

    context_tables_json = []
    context_text = ""
    db_error = ""

    if db_url:
        import dataclasses
        tables, db_error = extract_schema(db_url, db_schema)
        if tables:
            context_tables_json = [dataclasses.asdict(t) for t in tables]
            context_text = format_context(tables)

    session = create_session(title, context_tables_json, context_text, mode=mode)
    resp = dict(session)

    if db_error:
        resp["db_error"] = db_error
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
        return jsonify({"error": error}), 400

    context_tables_json = [dataclasses.asdict(t) for t in tables]
    context_text = format_context(tables)
    update_session(session_id, {
        "context_tables": context_tables_json,
        "context_text": context_text,
        "last_db_import": {"imported_at": imported_at, "table_count": len(tables), "error": None},
    })
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
    return jsonify(session)


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


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
