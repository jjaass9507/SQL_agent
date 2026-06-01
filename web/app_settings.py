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


def get_database_url() -> str:
    """Effective DB URL: the value saved via Settings wins, else the env var."""
    with _lock:
        saved = _read().get("database_url", "")
    return saved or os.environ.get("DATABASE_URL", "")


def set_database_url(url: str) -> None:
    """Persist (or clear, when empty) the database URL atomically."""
    with _lock:
        data = _read()
        data["database_url"] = url
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _SETTINGS_PATH.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp.replace(_SETTINGS_PATH)
