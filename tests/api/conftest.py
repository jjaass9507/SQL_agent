"""tests/api 共用 fixtures：SQLite in-memory DB（覆蓋 `get_db` 依賴）+ httpx AsyncClient。

LLM 一律 respx mock（見 tests/llm/conftest.py 的共用 helper），不需要真實 gateway。
"""

import json

import httpx
import pytest
import respx
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api import deps
from app.config import get_settings
from app.main import app
from app.repos.models import Base
from tests.llm.conftest import chat_completion_response

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
async def client(db_engine):
    """覆蓋 `app.api.deps.get_db`，回傳直接打 FastAPI app 的 httpx.AsyncClient。"""
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)

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


def interview_turn_payload(
    reply: str,
    tables: list[dict] | None = None,
    summary: list[str] | None = None,
    user_confirmed: bool = False,
) -> str:
    """組一個符合 `interview_service.InterviewTurn` json_schema 的 LLM 回應內容（字串）。"""
    return json.dumps(
        {
            "reply": reply,
            "tables": tables,
            "summary": summary,
            "user_confirmed": user_confirmed,
        },
        ensure_ascii=False,
    )


async def drive_to_confirming(
    client, session_id: str, tables: list[dict], summary: list[str] | None = None
):
    """跑「提案 → 使用者同意」兩輪，讓 session 進入 confirming。

    interview_service 的閘門要求先提案、使用者同意後才落版本（見
    `_may_finalize`），所以測試無法用單一輪把 session 推到 confirming。
    """
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/chat/completions").side_effect = [
            chat_completion_response(content=interview_turn_payload("我的規劃如下", tables=tables)),
            chat_completion_response(
                content=interview_turn_payload(
                    "設計完成", tables=tables, summary=summary, user_confirmed=True
                )
            ),
        ]
        await client.post(
            f"/api/v1/sessions/{session_id}/messages", json={"content": "我要一張使用者表"}
        )
        return await client.post(
            f"/api/v1/sessions/{session_id}/messages", json={"content": "可以"}
        )


def sample_table(name: str = "users") -> dict:
    """一張最小可用的 TableSpec dict（供測試組 LLM 結構化輸出 / 版本快照使用）。"""
    return {
        "table_name": name,
        "description": "使用者資料表",
        "columns": [
            {
                "name": "id",
                "data_type": "uuid",
                "nullable": False,
                "description": "主鍵",
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
