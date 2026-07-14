"""SQLAlchemy 2.0 async engine 與 session factory（唯一連線入口）。

讀取 `app.config.get_settings().database_url`；預設 SQLite（開發/測試），
正式環境設定為 PostgreSQL 連線字串即可，程式碼不需變動。
"""

from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings
from app.repos.models import Base

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _ensure_sqlite_dir(database_url: str) -> None:
    """SQLite 檔案模式時自動補建父目錄（data/ 為 git ignored，clone 後不存在）。"""
    url = make_url(database_url)
    if url.get_backend_name() == "sqlite" and url.database and url.database != ":memory:":
        Path(url.database).expanduser().parent.mkdir(parents=True, exist_ok=True)


def get_engine() -> AsyncEngine:
    """回傳全域唯一的 async engine（延遲建立、依 settings.database_url）。"""
    global _engine
    if _engine is None:
        database_url = get_settings().database_url
        _ensure_sqlite_dir(database_url)
        _engine = create_async_engine(database_url, future=True)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """回傳全域唯一的 async session factory。"""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _session_factory


async def get_session() -> AsyncIterator[AsyncSession]:
    """依賴注入風格的 session 產生器：yield 一個 AsyncSession，離開時自動關閉。"""
    async with get_session_factory()() as session:
        yield session


async def init_db(engine: AsyncEngine | None = None) -> None:
    """建立所有資料表（供測試或本機開發快速起手；正式環境一律用 Alembic migration）。"""
    target = engine or get_engine()
    async with target.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
