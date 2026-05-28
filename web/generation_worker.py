import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from web.session_store import get_tables, update_generation_status, update_session

logger = logging.getLogger(__name__)


def run_generation(session_id: str) -> None:
    thread = threading.Thread(
        target=_generate,
        args=(session_id,),
        daemon=True,
    )
    thread.start()


def _generate(session_id: str) -> None:
    from agents.writers.spec_writer import SpecWriter
    from agents.writers.diagram_writer import DiagramWriter
    from agents.writers.ddl_writer import DDLWriter
    from agents.writers.security_writer import SecurityWriter

    tables = get_tables(session_id)
    if not tables:
        return

    writers = [
        ("01_specification.md", SpecWriter()),
        ("02_er_diagram.md", DiagramWriter()),
        ("03_ddl.sql", DDLWriter()),
        ("04_security_plan.md", SecurityWriter()),
    ]

    def run_one(filename: str, writer) -> None:
        update_generation_status(session_id, filename, "loading")
        try:
            content = writer.generate(tables)
            if content and content.strip():
                update_generation_status(session_id, filename, "done", content)
            else:
                update_generation_status(session_id, filename, "failed",
                                         error="Writer 回傳空內容")
        except Exception as e:
            logger.error("writer failed: %s", e, extra={"session_id": session_id, "filename": filename})
            update_generation_status(session_id, filename, "failed", error=str(e))

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda args: run_one(*args), writers))

    update_session(session_id, {"phase": "done"})


def run_review(session_id: str) -> None:
    """Start background schema review for a 'review' mode session."""
    thread = threading.Thread(target=_review, args=(session_id,), daemon=True)
    thread.start()


def _review(session_id: str) -> None:
    from agents.reviewer import Reviewer
    from web.session_store import get_session

    session = get_session(session_id)
    if not session:
        return
    context_tables_data = session.get("context_tables", [])
    if not context_tables_data:
        update_session(session_id, {
            "phase": "review_done",
            "outputs": {"05_review_report.md": "（未匯入任何資料表，無法進行審查）"},
        })
        return

    from web.session_store import tables_from_json
    tables = tables_from_json(context_tables_data)

    try:
        report = Reviewer().review(tables)
    except Exception as e:
        logger.error("review failed: %s", e, extra={"session_id": session_id})
        report = f"審查過程發生錯誤：{e}"

    update_session(session_id, {
        "phase": "review_done",
        "outputs": {"05_review_report.md": report},
    })
