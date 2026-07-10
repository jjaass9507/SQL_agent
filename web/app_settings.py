"""Application-level settings persisted outside the session database.

The database connection string can't live inside the database it points to
(a bootstrap problem), so it lives in a small JSON config file under DATA_DIR.
The Settings page writes here; db_engine reads here to decide which backend
stores the platform's "memory" (sessions / projects).
"""
import json
import os
from pathlib import Path
from threading import Lock

_DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
_SETTINGS_PATH = _DATA_DIR / "app_settings.json"
_lock = Lock()


def _read() -> dict:
    try:
        with _SETTINGS_PATH.open(encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write(data: dict) -> None:
    """Atomically persist the full settings dict."""
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _SETTINGS_PATH.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(_SETTINGS_PATH)


def _set_key(key: str, value) -> None:
    """Atomically persist a single key in app_settings.json."""
    with _lock:
        data = _read()
        data[key] = value
        _write(data)


# ── Platform storage DB ──────────────────────────────────────────────────────

def get_database_url() -> str:
    """Effective DB URL: the value saved via Settings wins, else the env var."""
    with _lock:
        saved = _read().get("database_url", "")
    return saved or os.environ.get("DATABASE_URL", "")


def set_database_url(url: str) -> None:
    _set_key("database_url", url)


def get_platform_schema() -> str:
    """PostgreSQL schema that the platform's own tables live in (default: public)."""
    with _lock:
        return _read().get("platform_schema", "") or "public"


def set_platform_schema(s: str) -> None:
    _set_key("platform_schema", s.strip() or "public")


# ── Business DBs (multi-named) ───────────────────────────────────────────────

def get_business_databases() -> list:
    """Return list of {name, url} dicts. Auto-migrates old single-DB format."""
    with _lock:
        data = _read()
        if "business_databases" in data:
            return list(data["business_databases"])
        # Migrate from old single-key format
        old_url = data.get("business_database_url", "")
        if old_url:
            return [{"name": "預設", "url": old_url}]
        return []


def get_business_database(name: str) -> dict | None:
    """Return {name, url} for the named DB, or None."""
    for db in get_business_databases():
        if db["name"] == name:
            return db
    return None


def add_business_database(name: str, url: str) -> None:
    """Append or replace (by name) a business database entry."""
    with _lock:
        data = _read()
        dbs = data.get("business_databases")
        if dbs is None:
            # Migrate old single-DB if present
            old_url = data.get("business_database_url", "")
            dbs = [{"name": "預設", "url": old_url}] if old_url else []
        dbs = [d for d in dbs if d["name"] != name]
        dbs.append({"name": name, "url": url})
        data["business_databases"] = dbs
        _write(data)


def remove_business_database(name: str) -> None:
    """Remove a named business database entry."""
    with _lock:
        data = _read()
        dbs = data.get("business_databases", [])
        data["business_databases"] = [d for d in dbs if d["name"] != name]
        _write(data)


def set_business_databases(dbs: list) -> None:
    _set_key("business_databases", dbs)


# ── Global DB Agent conversation ─────────────────────────────────────────────

def get_agent_session_id() -> str:
    """Session id of the single global DB Agent conversation (Phase 5 auth
    will split this per-user; for now everyone shares one)."""
    with _lock:
        return _read().get("agent_session_id", "")


def set_agent_session_id(session_id: str) -> None:
    _set_key("agent_session_id", session_id)
