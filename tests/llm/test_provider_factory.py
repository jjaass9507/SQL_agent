"""所有 LLM 呼叫點都必須帶入探測到的 gateway 能力檔。

`POST /llm/diagnose` 會把探測結果存進 app_settings，但
`LLMProvider.from_settings()` 不會自己去讀，預設是「全部支援」。原本只有
DB Agent 記得帶入，訪談／文件產出／審查／NL2SQL 都沒有——面對不支援
system role 的 gateway，DB Agent 正常，其餘路徑一律 500。
"""

import re
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.llm.capabilities import CapabilityProfile
from app.repos import settings as settings_repo
from app.repos.models import Base
from app.services import provider_factory

APP_DIR = Path(__file__).resolve().parents[2] / "app"


@pytest.fixture
async def db_session():
    """本檔自足的 in-memory DB（tests/llm 沒有共用的 db_session fixture）。"""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
    await engine.dispose()


async def test_load_profile_returns_none_before_any_probe(db_session):
    assert await provider_factory.load_profile(db_session) is None


async def test_build_provider_uses_stored_profile(db_session):
    await settings_repo.set_setting(
        db_session,
        provider_factory.CAPABILITY_SETTING_KEY,
        CapabilityProfile(system_role=False, native_tools=False).model_dump(),
    )
    provider = await provider_factory.build_provider(db_session)
    assert provider.profile.system_role is False
    assert provider.profile.native_tools is False


async def test_build_provider_uses_selected_pensieve_backend(db_session, monkeypatch):
    from app.config import get_settings
    from app.llm.pensieve import PensieveProvider

    monkeypatch.setenv("PENSIEVE_URL", "http://pensieve.test/api")
    monkeypatch.setenv("PENSIEVE_TOKEN", "token")
    monkeypatch.setenv("PENSIEVE_EMPNO", "E123")
    get_settings.cache_clear()
    await settings_repo.set_setting(db_session, provider_factory.BACKEND_SETTING_KEY, "pensieve")

    provider = await provider_factory.build_provider(db_session)

    assert isinstance(provider, PensieveProvider)
    assert provider.model == "Pensieve"


@pytest.mark.parametrize(
    "path",
    [
        "api/routers/workbench.py",
        "api/routers/sessions.py",
        "services/review_service.py",
        "workers/handlers.py",
    ],
)
def test_call_sites_do_not_bypass_the_capability_profile(path):
    """直接呼叫 from_settings() 會拿到「全部支援」的預設值，繞過探測結果。

    `llm.py` 是唯一的例外——探針本身必須量測未經覆蓋的真實能力。
    """
    source = (APP_DIR / path).read_text(encoding="utf-8")
    assert not re.search(r"LLMProvider\.from_settings\(", source), (
        f"{path} 直接建立 provider，會忽略 /llm/diagnose 探測到的能力檔；"
        "請改用 provider_factory.build_provider(db)"
    )


async def test_llm_failure_is_not_a_bare_500():
    """LLM gateway 掛掉時使用者不該看到「Internal Server Error」。

    平台的使用者包含完全不懂技術的人，裸的 500 會被理解成整個平台壞了。
    """
    import httpx

    from app.llm.errors import LLMError
    from app.main import create_app

    application = create_app()

    @application.get("/__boom")
    async def _boom():
        raise LLMError("gateway unreachable: connection refused")

    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/__boom")

    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert "Internal Server Error" not in detail
    # 技術細節（連線字串、堆疊）不可外洩給使用者
    assert "connection refused" not in detail
    assert re.search(r"[一-鿿]", detail), f"錯誤訊息應為中文：{detail!r}"
