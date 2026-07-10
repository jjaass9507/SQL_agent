"""Human-in-the-loop DDL change request storage.

Dispatch mode (same convention as session_store.py):
  - If DATABASE_URL env var is set → PostgreSQL via SQLAlchemy Core
  - Otherwise → a single JSON file (data/change_requests.json)

Lifecycle: pending -> approved -> executed | failed, or pending -> rejected.
`propose_ddl` (agents/tool_registry.py) creates requests after a successful
dry-run; web/routes/changes.py moves them through the remaining states.
"""
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
_JSON_PATH = DATA_DIR / "change_requests.json"
_lock = Lock()


# ══════════════════════════════════════════════════════════════════════════
# JSON-file implementation
# ══════════════════════════════════════════════════════════════════════════

def _json_read_all() -> list:
    try:
        return json.loads(_JSON_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _json_write_all(rows: list) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _JSON_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, indent=2, ensure_ascii=False))
    tmp.replace(_JSON_PATH)


def _json_create(db_name, ddl, reason, dry_run_ok):
    with _lock:
        rows = _json_read_all()
        row = {
            "id": str(uuid.uuid4()),
            "db_name": db_name,
            "ddl": ddl,
            "reason": reason or "",
            "status": "pending",
            "dry_run_ok": dry_run_ok,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "decided_at": None,
            "error": None,
        }
        rows.append(row)
        _json_write_all(rows)
    return row


def _json_get(request_id):
    for row in _json_read_all():
        if row["id"] == request_id:
            return row
    return None


def _json_list_requests(status=None):
    rows = _json_read_all()
    if status:
        rows = [r for r in rows if r.get("status") == status]
    return sorted(rows, key=lambda r: r.get("created_at", ""), reverse=True)


def _json_decide(request_id, status, error=None):
    with _lock:
        rows = _json_read_all()
        for row in rows:
            if row["id"] == request_id:
                row["status"] = status
                row["decided_at"] = datetime.now(timezone.utc).isoformat()
                row["error"] = error
                _json_write_all(rows)
                return row
    return None


# ══════════════════════════════════════════════════════════════════════════
# PostgreSQL implementation
# ══════════════════════════════════════════════════════════════════════════

def _pg_row_to_dict(row) -> dict:
    d = dict(row)
    for key in ("created_at", "decided_at"):
        val = d.get(key)
        if val is not None and hasattr(val, "isoformat"):
            d[key] = val.isoformat()
    return d


def _pg_create(db_name, ddl, reason, dry_run_ok):
    from web.db_engine import get_engine
    from web.db_schema import change_requests_table
    request_id = str(uuid.uuid4())
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(change_requests_table.insert().values(
            id=request_id,
            db_name=db_name,
            ddl=ddl,
            reason=reason or "",
            status="pending",
            dry_run_ok=dry_run_ok,
            created_at=datetime.now(timezone.utc),
            decided_at=None,
            error=None,
        ))
    return _pg_get(request_id)


def _pg_get(request_id):
    from web.db_engine import get_engine
    from web.db_schema import change_requests_table
    from sqlalchemy import select
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            select(change_requests_table).where(change_requests_table.c.id == request_id)
        ).mappings().first()
    return _pg_row_to_dict(row) if row is not None else None


def _pg_list_requests(status=None):
    from web.db_engine import get_engine
    from web.db_schema import change_requests_table
    from sqlalchemy import select
    stmt = select(change_requests_table).order_by(change_requests_table.c.created_at.desc())
    if status:
        stmt = stmt.where(change_requests_table.c.status == status)
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [_pg_row_to_dict(r) for r in rows]


def _pg_decide(request_id, status, error=None):
    from web.db_engine import get_engine
    from web.db_schema import change_requests_table
    engine = get_engine()
    with engine.begin() as conn:
        result = conn.execute(
            change_requests_table.update()
            .where(change_requests_table.c.id == request_id)
            .values(status=status, decided_at=datetime.now(timezone.utc), error=error)
        )
        if result.rowcount == 0:
            return None
    return _pg_get(request_id)


# ══════════════════════════════════════════════════════════════════════════
# Public API — dispatches to PG or JSON implementation at call time
# ══════════════════════════════════════════════════════════════════════════

def create(db_name: str | None, ddl: str, reason: str, dry_run_ok: bool) -> dict:
    from web.db_engine import is_pg_mode
    if is_pg_mode():
        return _pg_create(db_name, ddl, reason, dry_run_ok)
    return _json_create(db_name, ddl, reason, dry_run_ok)


def get(request_id: str) -> dict | None:
    from web.db_engine import is_pg_mode
    if is_pg_mode():
        return _pg_get(request_id)
    return _json_get(request_id)


def list_requests(status: str | None = None) -> list:
    from web.db_engine import is_pg_mode
    if is_pg_mode():
        return _pg_list_requests(status)
    return _json_list_requests(status)


def decide(request_id: str, status: str, error: str | None = None) -> dict | None:
    """Move a request to a new status (approved/rejected/executed/failed),
    stamping decided_at and recording an error message if given."""
    from web.db_engine import is_pg_mode
    if is_pg_mode():
        return _pg_decide(request_id, status, error)
    return _json_decide(request_id, status, error)
