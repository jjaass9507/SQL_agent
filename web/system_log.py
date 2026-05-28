import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOG_DIR = Path(os.getenv("SQL_AGENT_LOG_DIR", Path(__file__).parent.parent / "logs"))
SYSTEM_LOG_FILE = LOG_DIR / "system.log.jsonl"

_lock = threading.Lock()

SENSITIVE_KEYS = {
    "authorization",
    "cookie",
    "db_url",
    "password",
    "secret",
    "token",
}


def _is_sensitive_key(key: str) -> bool:
    key_lower = key.lower()
    return any(marker in key_lower for marker in SENSITIVE_KEYS)


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "***REDACTED***" if _is_sensitive_key(str(key)) else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def log_event(event_type: str, **context: Any) -> None:
    """Append one structured event to the system log.

    Logging must never break user-facing flows, so write failures are swallowed
    after printing a compact diagnostic message.
    """
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        "context": _redact(context),
    }

    try:
        LOG_DIR.mkdir(exist_ok=True)
        line = json.dumps(record, ensure_ascii=False)
        with _lock:
            with SYSTEM_LOG_FILE.open("a", encoding="utf-8") as file:
                file.write(line + "\n")
    except Exception as exc:
        print(f"[system_log] failed to write log event: {exc}")
