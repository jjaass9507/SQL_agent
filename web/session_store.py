import dataclasses
import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from models.schema import ColumnSpec, TableSpec

logger = logging.getLogger(__name__)

_data_dir_env = os.environ.get("DATA_DIR")
DATA_DIR = Path(_data_dir_env) if _data_dir_env else Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()

GENERATION_FILES = [
    "01_specification.md",
    "02_er_diagram.md",
    "03_ddl.sql",
    "04_security_plan.md",
]


def _lock_for(session_id: str) -> threading.Lock:
    with _locks_guard:
        if session_id not in _locks:
            _locks[session_id] = threading.Lock()
        return _locks[session_id]


def _path(session_id: str) -> Path:
    return DATA_DIR / f"{session_id}.json"


def _tables_to_json(tables: list[TableSpec]) -> list[dict]:
    return [dataclasses.asdict(t) for t in tables]


def _tables_from_json(data: list[dict]) -> list[TableSpec]:
    result = []
    for t in data:
        columns = [ColumnSpec(**c) for c in t["columns"]]
        result.append(TableSpec(
            table_name=t["table_name"],
            description=t["description"],
            columns=columns,
            constraints=t.get("constraints", []),
            related_tables=t.get("related_tables", []),
        ))
    return result


def create_session(title: str, context_tables: list[dict] | None = None,
                   context_text: str = "", mode: str = "design") -> dict:
    session_id = str(uuid.uuid4())
    session = {
        "id": session_id,
        "title": title,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,                                         # "design" | "review"
        "phase": "reviewing" if mode == "review" else "collecting",
        "messages": [],
        "tables": None,
        "key_points": [],
        "outputs": {},
        "generation_status": {f: "waiting" for f in GENERATION_FILES},
        "generation_errors": {},
        "table_versions": [],                                 # list of design snapshots
        "context_tables": context_tables or [],
        "context_text": context_text,
    }
    _write(session_id, session)
    return session


def get_session(session_id: str) -> dict | None:
    p = _path(session_id)
    if not p.exists():
        return None
    with _lock_for(session_id):
        return json.loads(p.read_text(encoding="utf-8"))


def list_sessions(limit: int = 50, offset: int = 0) -> list[dict]:
    sessions = []
    for p in sorted(DATA_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            sessions.append({
                "id": data["id"],
                "title": data["title"],
                "created_at": data["created_at"],
                "phase": data["phase"],
                "mode": data.get("mode", "design"),
                "table_count": len(data["tables"]) if data["tables"] else 0,
            })
        except Exception as e:
            logger.warning("skipping corrupt session file %s: %s", p.name, e)
    return sessions[offset: offset + limit]


def update_session(session_id: str, updates: dict[str, Any]) -> dict | None:
    with _lock_for(session_id):
        p = _path(session_id)
        if not p.exists():
            return None
        session = json.loads(p.read_text(encoding="utf-8"))
        session.update(updates)
        p.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")
        return session


def add_message(session_id: str, role: str, content: str) -> dict | None:
    with _lock_for(session_id):
        p = _path(session_id)
        if not p.exists():
            return None
        session = json.loads(p.read_text(encoding="utf-8"))
        session["messages"].append({"role": role, "content": content})
        p.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")
        return session


def set_tables(session_id: str, tables: list[TableSpec], key_points: list[str]) -> dict | None:
    with _lock_for(session_id):
        p = _path(session_id)
        if not p.exists():
            return None
        session = json.loads(p.read_text(encoding="utf-8"))
        tables_json = _tables_to_json(tables)
        session["tables"] = tables_json
        session["key_points"] = key_points
        session["phase"] = "confirming"
        # Append to version history (keep last 10 versions)
        versions = session.setdefault("table_versions", [])
        versions.append({
            "version": len(versions) + 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "tables": tables_json,
            "key_points": key_points,
        })
        if len(versions) > 10:
            versions[:] = versions[-10:]
        p.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")
        return session


def restore_version(session_id: str, version_num: int) -> bool:
    """Restore a previous design version as the current tables. Returns True on success."""
    with _lock_for(session_id):
        p = _path(session_id)
        if not p.exists():
            return False
        session = json.loads(p.read_text(encoding="utf-8"))
        version = next((v for v in session.get("table_versions", [])
                        if v["version"] == version_num), None)
        if not version:
            return False
        session["tables"] = version["tables"]
        session["key_points"] = version["key_points"]
        session["phase"] = "confirming"
        session["outputs"] = {}
        session["generation_status"] = {f: "waiting" for f in GENERATION_FILES}
        session["generation_errors"] = {}
        p.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")
        return True


def tables_from_json(data: list[dict]) -> list[TableSpec]:
    """Public alias for deserialising a list of table dicts into TableSpec objects."""
    return _tables_from_json(data)


def get_tables(session_id: str) -> list[TableSpec] | None:
    session = get_session(session_id)
    if not session or not session.get("tables"):
        return None
    return _tables_from_json(session["tables"])


def update_generation_status(session_id: str, filename: str, status: str,
                             content: str | None = None, error: str | None = None) -> None:
    with _lock_for(session_id):
        p = _path(session_id)
        if not p.exists():
            return
        session = json.loads(p.read_text(encoding="utf-8"))
        session["generation_status"][filename] = status
        if content is not None:
            session["outputs"][filename] = content
        if error is not None:
            session.setdefault("generation_errors", {})[filename] = error
        elif status == "done":
            session.get("generation_errors", {}).pop(filename, None)
        p.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")


def try_start_generation(session_id: str) -> bool:
    """Atomically transitions phase from 'confirming' to 'generating'.
    Returns True on success, False if already past confirming phase."""
    with _lock_for(session_id):
        p = _path(session_id)
        if not p.exists():
            return False
        session = json.loads(p.read_text(encoding="utf-8"))
        if session.get("phase") != "confirming":
            return False
        session["phase"] = "generating"
        p.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")
        return True


def delete_session(session_id: str) -> bool:
    """Delete a session's JSON file. Returns True if deleted, False if not found."""
    with _lock_for(session_id):
        p = _path(session_id)
        if not p.exists():
            return False
        p.unlink()
    with _locks_guard:
        _locks.pop(session_id, None)
    return True


def _write(session_id: str, session: dict) -> None:
    _path(session_id).write_text(
        json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8"
    )
