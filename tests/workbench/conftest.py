"""tests/workbench 共用 fixtures。

`client` 透過 httpx.AsyncClient 呼叫真正的 `app.main.app`（含本次新增的
workbench/settings 路由），並把 `get_db` 依賴 override 成獨立的
in-memory SQLite，不觸碰任何正式資料庫。
"""

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db
from app.config import get_settings
from app.main import app
from app.repos import sessions
from app.repos.crypto import encrypt_db_url
from app.repos.models import Base


@pytest.fixture(autouse=True)
def _settings_env(monkeypatch):
    """crypto.encrypt_db_url/decrypt_db_url 需要 DB_ENCRYPTION_KEY；每個測試獨立且乾淨。"""
    monkeypatch.setenv("DB_ENCRYPTION_KEY", "ab" * 32)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
async def db_engine():
    """每個測試獨立的 in-memory SQLite engine。"""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(db_engine):
    """給測試直接用 repo 函式做前置資料準備（不經過 API）。

    呼叫端在寫入後需自行 `await db_session.commit()`——
    `client` fixture 用的是另一個 AsyncSession（共用同一個 in-memory 連線），
    未 commit 的資料在 SQLite 的獨立交易語意下不會跨 session 可見。
    """
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        yield session


@pytest.fixture
async def client(db_engine):
    """override `get_db`，讓 API 呼叫與 `db_session` fixture 共用同一個 in-memory DB。"""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    async def _override_get_db():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = _override_get_db
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def make_session(db_session):
    """回傳一個 async factory：建立一個 session（可選 db_url），並 commit 後回傳。"""

    async def _make(*, db_url: str | None = None, title: str = "測試 session"):
        encrypted = encrypt_db_url(db_url) if db_url else None
        record = await sessions.create_session(db_session, title=title, db_url_encrypted=encrypted)
        await db_session.commit()
        return record

    return _make
