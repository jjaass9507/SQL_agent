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


def _set_key(key: str, value: str) -> None:
    """Atomically persist a single key in app_settings.json."""
    with _lock:
        data = _read()
        data[key] = value
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _SETTINGS_PATH.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp.replace(_SETTINGS_PATH)


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


# ── Business DB ──────────────────────────────────────────────────────────────

def get_business_database_url() -> str:
    """The user's own database that the global DB Agent interacts with."""
    with _lock:
        return _read().get("business_database_url", "")


def set_business_database_url(url: str) -> None:
    _set_key("business_database_url", url)


def get_business_schema() -> str:
    """PostgreSQL schema to use when browsing the business database (default: public)."""
    with _lock:
        return _read().get("business_schema", "") or "public"


def set_business_schema(s: str) -> None:
    _set_key("business_schema", s.strip() or "public")
