import os
from functools import lru_cache
from sqlalchemy import create_engine


def is_pg_mode() -> bool:
    return bool(os.environ.get("DATABASE_URL", ""))


@lru_cache(maxsize=1)
def get_engine():
    """Returns a SQLAlchemy engine. Cached after first call. Returns None if DATABASE_URL not set."""
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        return None
    return create_engine(
        url,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        future=True,
    )
