from functools import lru_cache

from sqlalchemy import create_engine

from web.app_settings import get_database_url, get_platform_schema


def is_pg_mode() -> bool:
    return bool(get_database_url())


@lru_cache(maxsize=8)
def _engine_for(url: str, platform_schema: str):
    kwargs = dict(pool_size=5, max_overflow=10, pool_pre_ping=True, future=True)
    if platform_schema and platform_schema != "public":
        kwargs["connect_args"] = {"options": f"-c search_path={platform_schema},public"}
    return create_engine(url, **kwargs)


_ensured: set[tuple] = set()


def get_engine():
    """Returns a SQLAlchemy engine for the configured DB, or None if unset.

    Keyed on URL + platform_schema so changing either on the Settings page
    transparently rebuilds the engine. On first use, ensures the schema is
    up to date (creates missing tables / columns) so existing deployments
    self-heal without a manual Alembic run."""
    url = get_database_url()
    if not url:
        return None
    schema = get_platform_schema()
    engine = _engine_for(url, schema)
    key = (url, schema)
    if key not in _ensured:
        from web.db_schema import ensure_schema
        ensure_schema(engine, schema)
        _ensured.add(key)
    return engine
