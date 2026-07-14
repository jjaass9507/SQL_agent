"""tests/auth 共用 fixtures：SQLite in-memory DB + httpx AsyncClient（https base_url，
讓 Secure cookie 在測試中也能往返）+ 使用者/權杖建立輔助函式。

`AUTH_ENABLED` 預設維持 false（迴歸測試用）；需要開啟認證的測試使用
`enable_auth` fixture。
"""

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api import deps
from app.config import get_settings
from app.main import app
from app.repos import users as users_repo
from app.repos.models import Base
from app.services import auth_service


@pytest.fixture(autouse=True)
def _configure_settings(monkeypatch):
    """每個測試前後清除 `get_settings` 的 lru_cache，並重置 login 限流器
    （模組層級單例，不清會讓限流測試互相污染）。"""
    monkeypatch.setenv("DB_ENCRYPTION_KEY", "00" * 32)
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-0123456789abcdef-0123456789abcdef")
    get_settings.cache_clear()
    auth_service.login_rate_limiter._hits.clear()
    yield
    get_settings.cache_clear()
    auth_service.login_rate_limiter._hits.clear()


@pytest.fixture
def enable_auth(monkeypatch):
    """開啟 AUTH_ENABLED 的測試專用 fixture。"""
    monkeypatch.setenv("AUTH_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
async def db_engine():
    """每個測試獨立的 in-memory SQLite（StaticPool 讓多個 AsyncSession 共用同一連線）。"""
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
def session_factory(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest.fixture
async def db_session(session_factory):
    """給測試直接用 repo 函式做前置資料準備；寫入後需自行 `await db_session.commit()`。"""
    async with session_factory() as session:
        yield session


@pytest.fixture
async def client(session_factory):
    """覆蓋 `app.api.deps.get_db`；base_url 用 https 讓 Secure cookie 能被 httpx 保存。"""

    async def _override_get_db():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[deps.get_db] = _override_get_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
def make_user(db_session):
    """async factory：建立使用者並 commit，回傳 User。"""

    async def _make(email: str = "user@example.com", password: str = "pw12345678", role="user"):
        user = await users_repo.create_user(
            db_session,
            email=email,
            password_hash=auth_service.hash_password(password),
            role=role,
        )
        await db_session.commit()
        return user

    return _make


def bearer(user) -> dict:
    """組出某使用者的 `Authorization: Bearer` header（直接用服務層簽發 access token）。"""
    token, _ = auth_service.create_access_token(user)
    return {"Authorization": f"Bearer {token}"}
