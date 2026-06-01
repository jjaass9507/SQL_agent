"""Platform usage trail recorded to the configured PostgreSQL.

Records meaningful user actions (session created, message sent, design
confirmed, review run, query executed, settings changed, …) into the
`activity_log` table of the database set on the Settings page.

Best-effort by design: a no-op when there is no database (JSON mode), and it
never raises — recording usage must not break the action being recorded.
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def record(event: str, session_id: str | None = None, detail: dict | None = None) -> None:
    from web.db_engine import is_pg_mode, get_engine

    if not is_pg_mode():
        return
    try:
        from web.db_schema import activity_log_table
        engine = get_engine()
        with engine.begin() as conn:
            conn.execute(activity_log_table.insert().values(
                session_id=session_id,
                event=event,
                detail=detail,
                created_at=datetime.now(timezone.utc),
            ))
    except Exception as e:  # never let auditing break the request
        logger.warning("activity_log record failed", extra={"event": event, "err": str(e)[:200]})


def recent(limit: int = 100) -> list[dict]:
    """Return the most recent usage records (newest first). Empty in JSON mode."""
    from web.db_engine import is_pg_mode, get_engine
    from sqlalchemy import select

    if not is_pg_mode():
        return []
    from web.db_schema import activity_log_table
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            select(activity_log_table)
            .order_by(activity_log_table.c.id.desc())
            .limit(limit)
        ).mappings().all()
    return [{
        "id": r["id"],
        "session_id": r["session_id"],
        "event": r["event"],
        "detail": r["detail"],
        "created_at": r["created_at"].isoformat() if r["created_at"] else "",
    } for r in rows]
