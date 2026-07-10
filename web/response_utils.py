"""Small response-shaping helpers shared between app.py and web/routes/*.

Extracted out of app.py so blueprint modules (which app.py imports) can use
them without an app.py <-> web.routes.* import cycle.
"""
import re


def hide_platform_tables(tables: list, db_url: str) -> list:
    """Drop the platform's own bookkeeping tables from workbench/agent schema
    views, but only when the target DB is the same as the platform storage DB
    (otherwise a user's legitimately-named tables would be hidden)."""
    from web.app_settings import get_database_url
    from web.db_schema import platform_table_names
    if db_url and db_url.strip() == (get_database_url() or "").strip():
        hidden = platform_table_names()
        return [t for t in tables if t.get("name") not in hidden]
    return tables


def sanitize_db_error(msg: str) -> str:
    """Strip credentials and host details from DB error messages."""
    msg = re.sub(r'postgresql://[^\s]+', 'postgresql://...', msg)
    msg = re.sub(r'\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b', '...', msg)
    return msg[:300]


def mask_db_url(url: str) -> str:
    """Hide the password in a connection string before sending it to the frontend."""
    if not url:
        return ""
    return re.sub(r'://([^:/@]+):([^@]+)@', r'://\1:****@', url)
