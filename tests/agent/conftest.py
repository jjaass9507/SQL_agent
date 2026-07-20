"""tests/agent 共用 fixtures/helpers。

服務層測試（test_tool_registry.py / test_change_service.py / test_agent_service.py）
直接用 `db_session`（in-memory SQLite）操作 repo，LLM 呼叫改用 `make_provider()`
建構的 `LLMProvider` 明確傳入，不吃環境變數。

路由層測試（test_changes_router.py / test_agent_router.py）用 `client`（httpx
AsyncClient 打 FastAPI app），沿用 tests/api/conftest.py 的 `_configure_settings`
模式設定 LLM_BASE_URL 等環境變數，respx 攔截同一個 BASE_URL。
"""

import json

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api import deps
from app.config import get_settings
from app.llm.provider import LLMProvider
from app.main import app
from app.repos import settings as settings_repo
from app.repos.crypto import encrypt_db_url
from app.repos.models import Base

# 與 tests/llm/conftest.py 使用同一個 base_url，respx mock 才能攔截到。
BASE_URL = "http://mock-gateway.test/v1"

BUSINESS_DATABASES_KEY = "business_databases"


@pytest.fixture(autouse=True)
def _configure_settings(monkeypatch):
    """DB_ENCRYPTION_KEY 一律需要（change_service 加解密連線字串）；
    LLM_* 只有路由層測試（走 `LLMProvider.from_settings()`）會用到，
    服務層測試以 `make_provider()` 明確建構 provider，不受影響。"""
    monkeypatch.setenv("LLM_BASE_URL", BASE_URL)
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LLM_VERIFY", "false")
    monkeypatch.setenv("DB_ENCRYPTION_KEY", "00" * 32)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def make_provider(**overrides) -> LLMProvider:
    """建構一個明確的 LLMProvider（不吃環境變數），供服務層測試直接傳入。"""
    kwargs = dict(base_url=BASE_URL, api_key="test-key", model="test-model", verify=False)
    kwargs.update(overrides)
    return LLMProvider(**kwargs)


@pytest.fixture
async def db_session():
    """每個測試獨立的 in-memory SQLite AsyncSession（服務層測試直接操作 repo）。"""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest.fixture
async def db_engine():
    """路由層測試用：httpx client 覆蓋 `get_db` 依賴共用的 engine。"""
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


@pytest.fixture
async def seed_db(session_factory):
    """路由層測試在呼叫 `client` 前，直接用同一個 `db_engine` 寫入初始資料
    （例如 app_settings 的 business_databases）；測試需自行 `await seed_db.commit()`
    才會被之後的 client 請求看到（各自獨立的 AsyncSession）。"""
    async with session_factory() as session:
        yield session


async def seed_business_db(
    db_session, name: str, db_url: str, default_schema: str | None = None
) -> None:
    """在 app_settings 的 "business_databases" 清單中加入一筆（含加密連線字串）。"""
    setting = await settings_repo.get_setting(db_session, BUSINESS_DATABASES_KEY)
    databases = list(setting.value_json) if setting and setting.value_json else []
    entry = {"name": name, "db_url_encrypted": encrypt_db_url(db_url)}
    if default_schema:
        entry["default_schema"] = default_schema
    databases.append(entry)
    await settings_repo.set_setting(db_session, BUSINESS_DATABASES_KEY, databases)


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, *a):
        self.conn.executed.append(sql)
        if self.conn.fail_on and self.conn.fail_on in sql:
            raise Exception("syntax error at or near ...")


class FakeConn:
    def __init__(self, fail_on=None):
        self.fail_on = fail_on
        self.executed: list[str] = []
        self.rolled_back = False
        self.committed = False
        self.closed = False
        self.autocommit = True

    def cursor(self, **kwargs):
        return FakeCursor(self)

    def rollback(self):
        self.rolled_back = True

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


def install_fake_psycopg2(monkeypatch, *, fail_on: str | None = None) -> FakeConn:
    """把 `sys.modules["psycopg2"]` 換成一個假模組（同 tests/rules/test_ddl_validator.py 的作法），
    讓 `ddl_validator.validate_ddl` / `ddl_executor.execute_ddl` 不需要真實 PostgreSQL 就能測試。

    回傳共用的 FakeConn，可用來斷言 rollback/commit/close 是否被呼叫。
    """
    import sys
    import types

    conn = FakeConn(fail_on=fail_on)
    mod = types.ModuleType("psycopg2")
    mod.connect = lambda *a, **k: conn
    monkeypatch.setitem(sys.modules, "psycopg2", mod)
    return conn


def sample_table_dict(name: str = "orders") -> dict:
    """一張最小可用的 TableSpec dict（供工具參數 design_tables 使用）。"""
    return {
        "table_name": name,
        "description": "",
        "columns": [
            {
                "name": "id",
                "data_type": "integer",
                "nullable": False,
                "description": "",
                "is_primary_key": True,
                "is_foreign_key": False,
                "references": None,
                "is_unique": False,
                "is_indexed": False,
                "length": None,
                "default": None,
            }
        ],
        "constraints": [],
        "related_tables": [],
    }


def chat_response(
    *, content: str | None = None, tool_calls: list[dict] | None = None
) -> httpx.Response:
    """組一個標準（非串流）Chat Completions 回應（同 tests/llm/conftest.py）。"""
    message: dict = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 1,
            "model": "test-model",
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": "tool_calls" if tool_calls else "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        },
    )


def tool_call(call_id: str, name: str, arguments: dict) -> dict:
    """組一個 Chat Completions 回應中的 `tool_calls` 陣列元素。"""
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)},
    }
