import threading
from concurrent.futures import ThreadPoolExecutor

from web.session_store import get_tables, update_generation_status, update_session


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
            print(f"[generation_worker] {filename} failed: {e}")
            update_generation_status(session_id, filename, "failed", error=str(e))

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda args: run_one(*args), writers))

    update_session(session_id, {"phase": "done"})
