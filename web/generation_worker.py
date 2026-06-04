import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from web.session_store import (
    GENERATION_FILES,
    get_session,
    get_tables,
    update_generation_status,
    update_session,
)

logger = logging.getLogger(__name__)

_WRITER_MAP = {
    "01_specification.md": "agents.writers.spec_writer:SpecWriter",
    "02_er_diagram.md":    "agents.writers.diagram_writer:DiagramWriter",
    "03_ddl.sql":          "agents.writers.ddl_writer:DDLWriter",
    "04_security_plan.md": "agents.writers.security_writer:SecurityWriter",
    # On-demand extras (generated individually, not part of the core 4-doc run)
    "05_orm_models.py":    "agents.writers.orm_writer:ORMWriter",
    "06_migration.py":     "agents.writers.migration_writer:MigrationWriter",
    "07_queries.sql":      "agents.writers.query_writer:QueryWriter",
    # NOTE: 08_incremental_migration.sql is NOT here — its writer needs both the
    # designed and existing schema, so it runs via run_incremental(), not _run_one.
    # Pure-template exports (no LLM)
    "09_schema.dbml":      "agents.writers.dbml_writer:DBMLWriter",
    "10_schema.puml":      "agents.writers.plantuml_writer:PlantUMLWriter",
    "11_json_schema.json": "agents.writers.json_schema_writer:JSONSchemaWriter",
    "12_data_dictionary.csv": "agents.writers.data_dict_writer:DataDictWriter",
}

INCREMENTAL_FILE = "08_incremental_migration.sql"

# kind → output filename for on-demand extra generation
EXTRA_FILES = {
    "orm":         "05_orm_models.py",
    "migration":   "06_migration.py",
    "query":       "07_queries.sql",
    "incremental": INCREMENTAL_FILE,
    "dbml":        "09_schema.dbml",
    "plantuml":    "10_schema.puml",
    "jsonschema":  "11_json_schema.json",
    "datadict":    "12_data_dictionary.csv",
}


def _make_writer(filename: str):
    spec = _WRITER_MAP.get(filename)
    if not spec:
        return None
    module_path, cls_name = spec.split(":")
    import importlib
    mod = importlib.import_module(module_path)
    return getattr(mod, cls_name)()


def _run_one(session_id: str, filename: str, tables) -> None:
    writer = _make_writer(filename)
    if not writer:
        return
    update_generation_status(session_id, filename, "loading")
    try:
        content = writer.generate(tables)
        if content and content.strip():
            update_generation_status(session_id, filename, "done", content)
        else:
            update_generation_status(session_id, filename, "failed", error="Writer 回傳空內容")
    except Exception as e:
        logger.error("writer failed: %s", e, extra={"session_id": session_id, "output_file": filename})
        update_generation_status(session_id, filename, "failed", error=str(e))


def run_generation(session_id: str) -> None:
    thread = threading.Thread(target=_generate, args=(session_id,), daemon=True)
    thread.start()


def _generate(session_id: str) -> None:
    tables = get_tables(session_id)
    if not tables:
        return
    # Only the core 4 docs run on confirm; everything else in _WRITER_MAP is
    # an on-demand extra generated individually via run_single_file.
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda fn: _run_one(session_id, fn, tables), GENERATION_FILES))
    update_session(session_id, {"phase": "done"})


def run_single_file(session_id: str, filename: str) -> None:
    """Re-generate a single output file for an already-completed session."""
    tables = get_tables(session_id)
    if not tables:
        return
    thread = threading.Thread(
        target=_run_one,
        args=(session_id, filename, tables),
        daemon=True,
    )
    thread.start()


def run_incremental(session_id: str) -> None:
    """Generate an incremental ALTER migration (existing DB → designed schema)."""
    thread = threading.Thread(target=_incremental, args=(session_id,), daemon=True)
    thread.start()


def _incremental(session_id: str) -> None:
    from web.session_store import tables_from_json
    from agents.writers.incremental_migration_writer import IncrementalMigrationWriter

    session = get_session(session_id)
    if not session:
        return
    designed = get_tables(session_id)
    existing_data = session.get("context_tables") or []
    if not designed or not existing_data:
        update_generation_status(session_id, INCREMENTAL_FILE, "failed",
                                 error="需要設計結構與已匯入的現有 DB 結構")
        return

    existing = tables_from_json(existing_data)
    update_generation_status(session_id, INCREMENTAL_FILE, "loading")
    try:
        content = IncrementalMigrationWriter().generate(designed, existing)
        if content and content.strip():
            update_generation_status(session_id, INCREMENTAL_FILE, "done", content)
        else:
            update_generation_status(session_id, INCREMENTAL_FILE, "failed", error="Writer 回傳空內容")
    except Exception as e:
        logger.error("incremental migration failed: %s", e, extra={"session_id": session_id})
        update_generation_status(session_id, INCREMENTAL_FILE, "failed", error=str(e))


def run_memory_sync(session_id: str) -> None:
    """Upload the session's existing-DB structure to the shared LLM knowledge (background).

    Eager: runs whenever the existing schema is imported/refreshed, so knowledge
    is updated without waiting for a chat that happens to touch an existing table."""
    thread = threading.Thread(target=_memory_sync, args=(session_id,), daemon=True)
    thread.start()


def _memory_sync(session_id: str) -> None:
    from utils.client import get_api
    session = get_session(session_id)
    if not session:
        return
    context_text = (session.get("context_text") or "").strip()
    if not context_text:
        return
    try:
        if get_api().update_memory(context_text):
            update_session(session_id, {"memory_synced": True})
    except Exception as e:
        logger.error("memory sync failed: %s", e, extra={"session_id": session_id})


def run_business_db_memory_sync() -> None:
    """Upload the business database structure to LLM memory (background)."""
    thread = threading.Thread(target=_business_db_memory_sync, daemon=True)
    thread.start()


def _business_db_memory_sync() -> None:
    from web.app_settings import get_business_database_url, get_business_schema
    from web.db_introspect import extract_schema, format_context
    from utils.client import get_api
    biz_url = get_business_database_url()
    if not biz_url:
        return
    biz_schema = get_business_schema()
    tables, err = extract_schema(biz_url, biz_schema)
    if err or not tables:
        logger.warning("business_db_memory_sync: extract failed: %s", err)
        return
    context_text = format_context(tables)
    try:
        get_api().update_memory(context_text, filename="business_schema.txt")
    except Exception as e:
        logger.error("business_db_memory_sync failed: %s", e)


def run_review(session_id: str) -> None:
    """Start background schema review for a 'review' mode session."""
    thread = threading.Thread(target=_review, args=(session_id,), daemon=True)
    thread.start()


def _review(session_id: str) -> None:
    from agents.reviewer import Reviewer
    from web.session_store import tables_from_json
    from web.schema_advisor import analyze
    from web.schema_remediation import build_remediation_sql

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

    tables = tables_from_json(context_tables_data)
    try:
        report = Reviewer().review(tables)
    except Exception as e:
        logger.error("review failed: %s", e, extra={"session_id": session_id})
        update_session(session_id, {"phase": "review_failed"})
        return

    # Rule-based red flags + deterministic remediation SQL (no LLM, always available)
    warnings = analyze(tables)
    outputs = {"05_review_report.md": report}
    fix_sql = build_remediation_sql(warnings)
    if fix_sql:
        outputs["06_review_fix.sql"] = fix_sql

    update_session(session_id, {
        "phase": "review_done",
        "outputs": outputs,
        "review_warnings": warnings,
    })
    from web import activity_log
    activity_log.record("review_completed", session_id, {"table_count": len(tables)})
