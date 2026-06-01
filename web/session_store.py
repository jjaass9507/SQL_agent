"""
Session storage layer.

Dispatch mode:
  - If DATABASE_URL env var is set → PostgreSQL via SQLAlchemy Core
  - Otherwise → JSON files under DATA_DIR (original behaviour)
"""
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

logger = logging.getLogger(__name__)

DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))

GENERATION_FILES = [
    "01_specification.md",
    "02_er_diagram.md",
    "03_ddl.sql",
    "04_security_plan.md",
]

# ── Per-session in-process locks (used by both JSON and PG modes) ─────────────
_locks: dict[str, Lock] = {}
_locks_guard = Lock()


def _lock_for(session_id: str) -> Lock:
    with _locks_guard:
        if session_id not in _locks:
            _locks[session_id] = Lock()
        return _locks[session_id]


# ══════════════════════════════════════════════════════════════════════════════
# JSON file helpers (original implementation)
# ══════════════════════════════════════════════════════════════════════════════

def _path(session_id: str) -> Path:
    return DATA_DIR / f"{session_id}.json"


def _read(session_id: str) -> dict | None:
    p = _path(session_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _write(session_id: str, data: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _path(session_id).write_text(json.dumps(data, indent=2, ensure_ascii=False))


# ── Serialisation helpers shared by both modes ────────────────────────────────

def _tables_to_json(tables) -> list:
    """Convert list of TableSpec or dict to JSON-safe list of dicts."""
    import dataclasses
    result = []
    for t in (tables or []):
        if dataclasses.is_dataclass(t):
            result.append(dataclasses.asdict(t))
        else:
            result.append(t)
    return result


def tables_from_json(tables_json: list | None):
    """Convert a list of dicts (from JSON/DB) to TableSpec objects."""
    if not tables_json:
        return []
    from models.schema import TableSpec, ColumnSpec
    result = []
    for t in tables_json:
        cols = [ColumnSpec(**{k: v for k, v in c.items() if k in ColumnSpec.__dataclass_fields__})
                for c in t.get("columns", [])]
        result.append(TableSpec(
            table_name=t.get("table_name", ""),
            description=t.get("description", ""),
            columns=cols,
            constraints=t.get("constraints", []),
            related_tables=t.get("related_tables", []),
        ))
    return result


# ══════════════════════════════════════════════════════════════════════════════
# JSON-file implementations (public interface — original behaviour)
# ══════════════════════════════════════════════════════════════════════════════

def _json_create_session(title, context_tables=None, context_text="", mode="design", db_url=""):
    session_id = str(uuid.uuid4())
    session = {
        "id": session_id,
        "title": title,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "phase": "reviewing" if mode == "review" else "collecting",
        "messages": [],
        "tables": None,
        "key_points": [],
        "outputs": {},
        "generation_status": {f: "waiting" for f in GENERATION_FILES},
        "generation_errors": {},
        "table_versions": [],
        "context_tables": context_tables or [],
        "context_text": context_text or "",
        "memory_synced": False,        # existing-DB structure pushed to LLM memory yet?
        "db_url": db_url or "",
    }
    with _lock_for(session_id):
        _write(session_id, session)
    return session


def _json_get_session(session_id):
    with _lock_for(session_id):
        return _read(session_id)


def _json_list_sessions(limit=50, offset=0):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    sessions = []
    for p in sorted(DATA_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            data = json.loads(p.read_text())
            sessions.append({
                "id": data["id"],
                "title": data.get("title", "未命名"),
                "created_at": data.get("created_at", ""),
                "phase": data.get("phase", "collecting"),
                "mode": data.get("mode", "design"),
                "table_count": len(data.get("tables") or []),
            })
        except Exception as exc:
            logger.warning("Corrupt session file %s: %s", p, exc)
    return sessions[offset: offset + limit]


def _json_update_session(session_id, updates):
    with _lock_for(session_id):
        session = _read(session_id)
        if session is None:
            return None
        session.update(updates)
        _write(session_id, session)
    return session


def _json_add_message(session_id, role, content):
    with _lock_for(session_id):
        session = _read(session_id)
        if session is None:
            return None
        session.setdefault("messages", []).append({"role": role, "content": content})
        _write(session_id, session)
    return session


def _json_set_tables(session_id, tables, key_points):
    tables_json = _tables_to_json(tables)
    with _lock_for(session_id):
        session = _read(session_id)
        if session is None:
            return None
        versions = list(session.get("table_versions") or [])
        versions.append({
            "version": len(versions) + 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "tables": tables_json,
            "key_points": key_points,
        })
        if len(versions) > 10:
            versions = versions[-10:]
        session["tables"] = tables_json
        session["key_points"] = key_points
        session["phase"] = "confirming"
        session["table_versions"] = versions
        _write(session_id, session)
    return session


def _json_restore_version(session_id, version_num):
    with _lock_for(session_id):
        session = _read(session_id)
        if session is None:
            return False
        version = next(
            (v for v in (session.get("table_versions") or []) if v["version"] == version_num),
            None,
        )
        if not version:
            return False
        session["tables"] = version["tables"]
        session["key_points"] = version["key_points"]
        session["phase"] = "confirming"
        session["outputs"] = {}
        session["generation_status"] = {f: "waiting" for f in GENERATION_FILES}
        session["generation_errors"] = {}
        _write(session_id, session)
    return True


def _json_get_tables(session_id):
    session = _read(session_id)
    if session is None or not session.get("tables"):
        return None
    return tables_from_json(session["tables"])


def _json_update_generation_status(session_id, filename, status, content=None, error=None):
    with _lock_for(session_id):
        session = _read(session_id)
        if session is None:
            return
        session.setdefault("generation_status", {})[filename] = status
        if content is not None:
            session.setdefault("outputs", {})[filename] = content
        if error is not None:
            session.setdefault("generation_errors", {})[filename] = error
        elif status == "done":
            session.get("generation_errors", {}).pop(filename, None)
        _write(session_id, session)


def _json_try_start_generation(session_id):
    with _lock_for(session_id):
        session = _read(session_id)
        if session is None or session.get("phase") != "confirming":
            return False
        session["phase"] = "generating"
        _write(session_id, session)
        return True


def _json_delete_session(session_id):
    p = _path(session_id)
    if not p.exists():
        return False
    p.unlink()
    with _locks_guard:
        _locks.pop(session_id, None)
    return True


# ══════════════════════════════════════════════════════════════════════════════
# PostgreSQL implementations
# ══════════════════════════════════════════════════════════════════════════════

def _pg_create_session(title, context_tables=None, context_text="", mode="design", db_url=""):
    from web.db_engine import get_engine
    from web.db_schema import sessions_table
    session_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    gen_status = {f: "waiting" for f in GENERATION_FILES}
    session = {
        "id": session_id,
        "title": title,
        "created_at": now.isoformat(),
        "mode": mode,
        "phase": "reviewing" if mode == "review" else "collecting",
        "messages": [],
        "tables": None,
        "key_points": [],
        "outputs": {},
        "generation_status": gen_status,
        "generation_errors": {},
        "table_versions": [],
        "context_tables": context_tables or [],
        "context_text": context_text or "",
        "db_url": db_url or "",
    }
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(sessions_table.insert().values(
            id=session_id,
            title=title,
            mode=mode,
            phase=session["phase"],
            key_points=[],
            tables=None,
            table_versions=[],
            outputs={},
            generation_status=gen_status,
            generation_errors={},
            context_tables=context_tables or [],
            context_text=context_text or "",
            last_db_import=None,
            db_url=db_url or None,
            created_at=now,
            updated_at=now,
        ))
    return session


def _pg_get_session(session_id):
    from web.db_engine import get_engine
    from web.db_schema import sessions_table, messages_table
    from sqlalchemy import select
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            select(sessions_table).where(sessions_table.c.id == session_id)
        ).mappings().first()
        if row is None:
            return None
        session = dict(row)
        msgs = conn.execute(
            select(messages_table)
            .where(messages_table.c.session_id == session_id)
            .order_by(messages_table.c.id)
        ).mappings().all()
        session["messages"] = [{"role": m["role"], "content": m["content"]} for m in msgs]
        # Normalise datetime → ISO string
        for key in ("created_at", "updated_at"):
            val = session.get(key)
            if val is not None and hasattr(val, "isoformat"):
                session[key] = val.isoformat()
        session.pop("updated_at", None)  # not part of public API
        # Ensure nullable lists/dicts are never None
        for key, default in (("key_points", []), ("table_versions", []),
                              ("outputs", {}), ("generation_status", {}),
                              ("generation_errors", {}), ("context_tables", [])):
            if session.get(key) is None:
                session[key] = default
        if session.get("context_text") is None:
            session["context_text"] = ""
    return session


def _pg_list_sessions(limit=50, offset=0):
    from web.db_engine import get_engine
    from web.db_schema import sessions_table
    from sqlalchemy import select
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            select(sessions_table)
            .order_by(sessions_table.c.created_at.desc())
            .limit(limit).offset(offset)
        ).mappings().all()
        result = []
        for r in rows:
            created = r["created_at"]
            if hasattr(created, "isoformat"):
                created = created.isoformat()
            result.append({
                "id": r["id"],
                "title": r["title"],
                "created_at": created,
                "phase": r["phase"],
                "mode": r["mode"],
                "table_count": len(r["tables"]) if r["tables"] else 0,
            })
    return result


def _pg_update_session(session_id, updates):
    from web.db_engine import get_engine
    from web.db_schema import sessions_table
    ALLOWED = {
        "title", "mode", "phase", "key_points", "tables", "table_versions",
        "outputs", "generation_status", "generation_errors",
        "context_tables", "context_text", "last_db_import", "db_url",
    }
    db_updates = {k: v for k, v in updates.items() if k in ALLOWED}
    if not db_updates:
        return _pg_get_session(session_id)
    db_updates["updated_at"] = datetime.now(timezone.utc)
    engine = get_engine()
    with engine.begin() as conn:
        result = conn.execute(
            sessions_table.update()
            .where(sessions_table.c.id == session_id)
            .values(**db_updates)
        )
        if result.rowcount == 0:
            return None
    return _pg_get_session(session_id)


def _pg_add_message(session_id, role, content):
    from web.db_engine import get_engine
    from web.db_schema import sessions_table, messages_table
    from sqlalchemy import select
    engine = get_engine()
    with engine.begin() as conn:
        exists = conn.execute(
            select(sessions_table.c.id).where(sessions_table.c.id == session_id)
        ).first()
        if exists is None:
            return None
        conn.execute(messages_table.insert().values(
            session_id=session_id,
            role=role,
            content=content,
            created_at=datetime.now(timezone.utc),
        ))
    return _pg_get_session(session_id)


def _pg_set_tables(session_id, tables, key_points):
    from web.db_engine import get_engine
    from web.db_schema import sessions_table
    from sqlalchemy import select
    tables_json = _tables_to_json(tables)
    engine = get_engine()
    with _lock_for(session_id):
        with engine.begin() as conn:
            row = conn.execute(
                select(sessions_table).where(sessions_table.c.id == session_id)
            ).mappings().first()
            if row is None:
                return None
            versions = list(row["table_versions"] or [])
            versions.append({
                "version": len(versions) + 1,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "tables": tables_json,
                "key_points": key_points,
            })
            if len(versions) > 10:
                versions = versions[-10:]
            now = datetime.now(timezone.utc)
            conn.execute(
                sessions_table.update()
                .where(sessions_table.c.id == session_id)
                .values(
                    tables=tables_json,
                    key_points=key_points,
                    phase="confirming",
                    table_versions=versions,
                    updated_at=now,
                )
            )
    return _pg_get_session(session_id)


def _pg_restore_version(session_id, version_num):
    from web.db_engine import get_engine
    from web.db_schema import sessions_table
    from sqlalchemy import select
    engine = get_engine()
    with _lock_for(session_id):
        with engine.begin() as conn:
            row = conn.execute(
                select(sessions_table)
                .where(sessions_table.c.id == session_id)
                .with_for_update()
            ).mappings().first()
            if row is None:
                return False
            version = next(
                (v for v in (row["table_versions"] or []) if v["version"] == version_num),
                None,
            )
            if not version:
                return False
            conn.execute(
                sessions_table.update()
                .where(sessions_table.c.id == session_id)
                .values(
                    tables=version["tables"],
                    key_points=version["key_points"],
                    phase="confirming",
                    outputs={},
                    generation_status={f: "waiting" for f in GENERATION_FILES},
                    generation_errors={},
                    updated_at=datetime.now(timezone.utc),
                )
            )
    return True


def _pg_get_tables(session_id):
    session = _pg_get_session(session_id)
    if session is None or not session.get("tables"):
        return None
    return tables_from_json(session["tables"])


def _pg_update_generation_status(session_id, filename, status, content=None, error=None):
    from web.db_engine import get_engine
    from web.db_schema import sessions_table
    from sqlalchemy import select
    engine = get_engine()
    with _lock_for(session_id):
        with engine.begin() as conn:
            row = conn.execute(
                select(sessions_table).where(sessions_table.c.id == session_id)
            ).mappings().first()
            if row is None:
                return
            gen_status = dict(row["generation_status"] or {})
            gen_status[filename] = status
            outputs = dict(row["outputs"] or {})
            gen_errors = dict(row["generation_errors"] or {})
            if content is not None:
                outputs[filename] = content
            if error is not None:
                gen_errors[filename] = error
            elif status == "done":
                gen_errors.pop(filename, None)
            conn.execute(
                sessions_table.update()
                .where(sessions_table.c.id == session_id)
                .values(
                    generation_status=gen_status,
                    outputs=outputs,
                    generation_errors=gen_errors,
                    updated_at=datetime.now(timezone.utc),
                )
            )


def _pg_try_start_generation(session_id):
    from web.db_engine import get_engine
    from web.db_schema import sessions_table
    from sqlalchemy import select
    engine = get_engine()
    with _lock_for(session_id):
        with engine.begin() as conn:
            row = conn.execute(
                select(sessions_table)
                .where(sessions_table.c.id == session_id)
                .with_for_update()
            ).mappings().first()
            if row is None or row["phase"] != "confirming":
                return False
            conn.execute(
                sessions_table.update()
                .where(sessions_table.c.id == session_id)
                .values(phase="generating", updated_at=datetime.now(timezone.utc))
            )
            return True


def _pg_delete_session(session_id):
    from web.db_engine import get_engine
    from web.db_schema import sessions_table
    engine = get_engine()
    with engine.begin() as conn:
        result = conn.execute(
            sessions_table.delete().where(sessions_table.c.id == session_id)
        )
    with _locks_guard:
        _locks.pop(session_id, None)
    return result.rowcount > 0


# ══════════════════════════════════════════════════════════════════════════════
# Public API — dispatches to PG or JSON implementation at call time
# ══════════════════════════════════════════════════════════════════════════════

def create_session(title, context_tables=None, context_text="", mode="design", db_url=""):
    from web.db_engine import is_pg_mode
    if is_pg_mode():
        return _pg_create_session(title, context_tables, context_text, mode, db_url)
    return _json_create_session(title, context_tables, context_text, mode, db_url)


def get_session(session_id):
    from web.db_engine import is_pg_mode
    if is_pg_mode():
        return _pg_get_session(session_id)
    return _json_get_session(session_id)


def list_sessions(limit=50, offset=0):
    from web.db_engine import is_pg_mode
    if is_pg_mode():
        return _pg_list_sessions(limit, offset)
    return _json_list_sessions(limit, offset)


def update_session(session_id, updates):
    from web.db_engine import is_pg_mode
    if is_pg_mode():
        return _pg_update_session(session_id, updates)
    return _json_update_session(session_id, updates)


def add_message(session_id, role, content):
    from web.db_engine import is_pg_mode
    if is_pg_mode():
        return _pg_add_message(session_id, role, content)
    return _json_add_message(session_id, role, content)


def set_tables(session_id, tables, key_points):
    from web.db_engine import is_pg_mode
    if is_pg_mode():
        return _pg_set_tables(session_id, tables, key_points)
    return _json_set_tables(session_id, tables, key_points)


def restore_version(session_id, version_num):
    from web.db_engine import is_pg_mode
    if is_pg_mode():
        return _pg_restore_version(session_id, version_num)
    return _json_restore_version(session_id, version_num)


def get_tables(session_id):
    from web.db_engine import is_pg_mode
    if is_pg_mode():
        return _pg_get_tables(session_id)
    return _json_get_tables(session_id)


def update_generation_status(session_id, filename, status, content=None, error=None):
    from web.db_engine import is_pg_mode
    if is_pg_mode():
        return _pg_update_generation_status(session_id, filename, status, content, error)
    return _json_update_generation_status(session_id, filename, status, content, error)


def try_start_generation(session_id):
    from web.db_engine import is_pg_mode
    if is_pg_mode():
        return _pg_try_start_generation(session_id)
    return _json_try_start_generation(session_id)


def delete_session(session_id):
    from web.db_engine import is_pg_mode
    if is_pg_mode():
        return _pg_delete_session(session_id)
    return _json_delete_session(session_id)
