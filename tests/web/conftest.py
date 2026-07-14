"""tests/web 共用 fixtures：前端契約測試用的 SQLite session_factory + httpx AsyncClient。

前端 JS 沒有測試框架（原生 ES modules），改以「頁面依賴的 API 契約測試」代替：
用真實 FastAPI app（httpx ASGI transport）+ respx mock LLM + SQLite，驗證各頁 JS
所依賴的請求/回應形狀（欄位名、事件序列）不被後端改動悄悄破壞。

fixtures 結構同 tests/workers/conftest.py（session_factory 用實體暫存檔 SQLite，
理由見該檔 docstring），並如 tests/api/conftest.py 設定 LLM 與加密環境變數。
"""

import os
import tempfile
import uuid

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import deps
from app.config import get_settings
from app.main import app
from app.repos.models import Base

BASE_URL = "http://mock-gateway.test/v1"


@pytest.fixture(autouse=True)
def _configure_settings(monkeypatch):
    """LLM 連線與 DB 加密金鑰皆從環境變數讀取；每個測試前後清除 `get_settings` 的 lru_cache。"""
    monkeypatch.setenv("LLM_BASE_URL", BASE_URL)
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LLM_VERIFY", "false")
    monkeypatch.setenv("DB_ENCRYPTION_KEY", "00" * 32)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
async def session_factory():
    """實體暫存檔 SQLite 的 async_sessionmaker（多 AsyncSession 併發寫入需獨立連線，
    不可用 :memory: + StaticPool，原因見 tests/workers/conftest.py docstring）。"""
    fd, path = tempfile.mkstemp(prefix=f"sql_agent_web_test_{uuid.uuid4().hex}_", suffix=".db")
    os.close(fd)
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory

    await engine.dispose()
    os.remove(path)


@pytest.fixture
async def client(session_factory):
    """覆蓋 `app.api.deps.get_db`，回傳直接打 FastAPI app 的 httpx.AsyncClient。"""

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
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
