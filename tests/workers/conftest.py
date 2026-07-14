"""tests/workers 共用 fixtures：SQLite session_factory、respx mock LLM 輔助函式、
打 outputs router 的 httpx AsyncClient（覆蓋 get_db，直接用同一個 session_factory）。
"""

import json
import os
import tempfile
import uuid

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import deps
from app.llm.provider import LLMProvider
from app.main import app
from app.repos.models import Base

BASE_URL = "http://mock-gateway.test/v1"


@pytest.fixture
async def session_factory():
    """建立一個乾淨的 SQLite 資料庫，回傳綁定該 engine 的 async_sessionmaker。

    worker/service 層會平行開好幾個獨立的 AsyncSession（見 generation_service 的
    docstring），所以測試需要 sessionmaker 本身而不是單一個 session。

    注意：不能用 `sqlite+aiosqlite:///:memory:` + `StaticPool`——StaticPool 對所有
    checkout 回傳同一個實體連線物件（見 SQLAlchemy `StaticPool._do_get`，沒有任何
    互斥），多個 AsyncSession 真正併發寫入時會共用同一條底層連線的交易狀態，
    互相搶佔 BEGIN/COMMIT 導致寫入遺失（曾實測出現 outputs 表少寫、progress_json
    覆蓋成舊快照的間歇性失敗）。改用實體暫存檔 SQLite + 預設連線池，每個
    AsyncSession 都是獨立連線，交易正確隔離；並行寫入時 SQLite 檔案鎖只會讓
    連線互相等待，不會遺失資料。
    """
    fd, path = tempfile.mkstemp(prefix=f"sql_agent_test_{uuid.uuid4().hex}_", suffix=".db")
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
    """覆蓋 `app.api.deps.get_db`，回傳直接打 FastAPI app 的 httpx.AsyncClient
    （與 tests/api/conftest.py 的 `client` fixture同構，但綁定 tests/workers 自己的
    `session_factory`，讓 router 端點與測試前置的 repo 呼叫共用同一個 in-memory DB）。
    """

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


def make_provider(**overrides) -> LLMProvider:
    kwargs = dict(base_url=BASE_URL, api_key="test-key", model="test-model", verify=False)
    kwargs.update(overrides)
    return LLMProvider(**kwargs)


def chat_completion_response(content: str | None) -> httpx.Response:
    """組一個標準（非串流）Chat Completions 回應，格式對齊 tests/llm/conftest.py。"""
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
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        },
    )


def dispatch_by_marker(responses: dict[str, str]):
    """依 system prompt 是否含某個 marker 字串分派回應內容，供 respx side_effect 使用。

    四份文件並行產出時會同時打好幾個不同的 writer（DDL/圖說明/安全規劃……），
    需要依請求內容分辨是哪一個呼叫、回傳對應內容。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        system_content = next(
            (m["content"] for m in body["messages"] if m.get("role") == "system"), ""
        )
        for marker, content in responses.items():
            if marker in system_content:
                return chat_completion_response(content)
        return chat_completion_response("")

    return handler
