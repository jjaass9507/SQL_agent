from functools import lru_cache

from sqlalchemy import create_engine

from web.app_settings import get_database_url


def is_pg_mode() -> bool:
    return bool(get_database_url())


@lru_cache(maxsize=1)
def _engine_for(url: str):
    return create_engine(
        url,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        future=True,
    )


def get_engine():
    """Returns a SQLAlchemy engine for the configured DB, or None if unset.

    Keyed on the URL so changing it on the Settings page transparently rebuilds
    the engine (the previous one is evicted from the cache)."""
    url = get_database_url()
    if not url:
        return None
    return _engine_for(url)
