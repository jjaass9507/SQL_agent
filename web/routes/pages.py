"""HTML page routes (Phase 5): the six Jinja-rendered pages, moved out of
app.py verbatim. No url_prefix — paths match the pre-split app.py exactly.
"""
from flask import Blueprint, redirect, render_template, url_for

from web.app_settings import get_business_databases
from web.convention_checker import check_conventions, infer_conventions
from web.schema_advisor import analyze
from web.schema_diff import compute_diff
from web.session_store import get_session, tables_from_json
from web.table_relation import find_related

bp = Blueprint("pages", __name__)


@bp.get("/")
def index():
    return render_template("index.html")


@bp.get("/sessions/<session_id>/chat")
def chat_page(session_id):
    session = get_session(session_id)
    if not session:
        return redirect(url_for("pages.index"))
    if session["phase"] in ("confirming", "generating", "done"):
        return redirect(url_for("pages.confirm_page", session_id=session_id))
    return render_template("chat.html", session=session)


@bp.get("/sessions/<session_id>/confirm")
def confirm_page(session_id):
    session = get_session(session_id)
    if not session:
        return redirect(url_for("pages.index"))
    if session["phase"] in ("generating", "done"):
        return redirect(url_for("pages.docs_page", session_id=session_id))
    if session["phase"] == "collecting":
        return redirect(url_for("pages.chat_page", session_id=session_id))

    diff = None
    warnings = []
    relation_report = None
    if session.get("tables"):
        designed = tables_from_json(session["tables"])
        warnings = analyze(designed)
        if session.get("context_tables"):
            existing = tables_from_json(session["context_tables"])
            diff = compute_diff(designed, existing)

            warnings += check_conventions(designed, infer_conventions(existing))

            requirement_text = "\n".join(session.get("key_points") or [])
            relation_report = find_related(requirement_text, designed, existing)

    return render_template("confirm.html", session=session, diff=diff, warnings=warnings,
                           relation_report=relation_report)


@bp.get("/sessions/<session_id>/docs")
def docs_page(session_id):
    session = get_session(session_id)
    if not session:
        return redirect(url_for("pages.index"))
    if session.get("mode") == "review":
        return redirect(url_for("pages.review_page", session_id=session_id))
    # Guard against landing on the docs page before generation has started,
    # otherwise the user sees 4 cards spinning on "等待產出" forever.
    if session["phase"] == "collecting":
        return redirect(url_for("pages.chat_page", session_id=session_id))
    if session["phase"] == "confirming":
        return redirect(url_for("pages.confirm_page", session_id=session_id))
    return render_template("docs.html", session=session)


@bp.get("/sessions/<session_id>/review")
def review_page(session_id):
    session = get_session(session_id)
    if not session:
        return redirect(url_for("pages.index"))
    if session.get("mode") != "review":
        return redirect(url_for("pages.index"))
    return render_template("review.html", session=session)


@bp.get("/settings")
def settings_page():
    return render_template("settings.html")


@bp.get("/db-agent")
def db_agent_page():
    has_biz_db = bool(get_business_databases())
    return render_template("db_agent.html", has_biz_db=has_biz_db)
