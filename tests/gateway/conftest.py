"""gateway 契約測試的共用 fixtures：假 gateway + 指向它的 app client。"""

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db
from app.config import get_settings
from app.main import app
from app.repos.models import Base
from tests.gateway.fake_gateway import FakeGateway


@pytest.fixture
def gateway(request):
    """啟動一個假 gateway。用 `@pytest.mark.parametrize` 間接帶入 profile 名稱。"""
    profile = getattr(request, "param", "full")
    server = FakeGateway(profile).start()
    yield server
    server.stop()


@pytest.fixture
def llm_env(gateway, monkeypatch):
    """把 app 的 LLM 設定指向假 gateway（每個測試獨立、不碰任何真實服務）。"""
    monkeypatch.setenv("LLM_BASE_URL", gateway.base_url)
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "fake-model")
    monkeypatch.setenv("DB_ENCRYPTION_KEY", "ab" * 32)
    get_settings.cache_clear()
    yield gateway
    get_settings.cache_clear()


@pytest.fixture
async def db_engine():
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
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        yield session


@pytest.fixture
async def client(db_engine, llm_env):
    """走真實 FastAPI app，但 DB 是 in-memory、LLM 是假 gateway。"""
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
