"""tests/ad_auth 共用 fixtures：SQLite in-memory DB + httpx AsyncClient +
`ldap3` 假物件（不需真實 AD）。

`AD_ENABLED` 預設維持 false；需要 AD 的測試使用 `enable_ad` fixture（連同
`fake_ldap` 配置假的 `ldap3.Server`/`ldap3.Connection`）。
"""

import re

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
    """每個測試前後清除 `get_settings` 的 lru_cache，並重置 login 限流器。"""
    monkeypatch.setenv("DB_ENCRYPTION_KEY", "00" * 32)
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-0123456789abcdef-0123456789abcdef")
    get_settings.cache_clear()
    auth_service.login_rate_limiter._hits.clear()
    yield
    get_settings.cache_clear()
    auth_service.login_rate_limiter._hits.clear()


@pytest.fixture
def enable_ad(monkeypatch):
    """開啟 `AD_ENABLED` 並提供一組測試用的 AD 連線設定。"""
    monkeypatch.setenv("AD_ENABLED", "true")
    monkeypatch.setenv("AD_SERVER", "ldap://dc.test.local")
    monkeypatch.setenv("AD_USE_SSL", "false")
    monkeypatch.setenv("AD_DOMAIN", "CORP")
    monkeypatch.setenv("AD_UPN_SUFFIX", "corp.local")
    monkeypatch.setenv("AD_SEARCH_BASE", "DC=corp,DC=local")
    monkeypatch.setenv("AD_ADMIN_GROUP", "SQLAgentAdmins")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def enable_ad_sso_header(monkeypatch, enable_ad):
    """在 `enable_ad` 基礎上另外開啟 `ad_sso_remote_user_header` 模式。"""
    monkeypatch.setenv("AD_SSO_ENABLED", "true")
    monkeypatch.setenv("AD_SSO_REMOTE_USER_HEADER", "X-Remote-User")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def enable_ad_sso_token(monkeypatch, enable_ad):
    """在 `enable_ad` 基礎上開啟 `AD_SSO_ENABLED`，但不設定 remote-user header
    （落入 `X-IIS-WindowsAuthToken` token 模式）。"""
    monkeypatch.setenv("AD_SSO_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# -- 假 ldap3（不需真實 AD）--------------------------------------------------------


class FakeAttr:
    """對應 ldap3 的 Attribute：`.values` 是一個 list。"""

    def __init__(self, values: list):
        self.values = values


class FakeEntry:
    """對應 ldap3 的 Entry：屬性透過 `getattr(entry, name)` 存取。"""

    def __init__(self, **attrs):
        self._attrs = {}
        for key, value in attrs.items():
            if value is None:
                continue
            values = value if isinstance(value, list) else [value]
            self._attrs[key] = FakeAttr(values)

    def __getattr__(self, name):
        try:
            return self._attrs[name]
        except KeyError:
            raise AttributeError(name) from None


class FakeConnection:
    """對應 ldap3 的 Connection：`bind()`/`search()`/`unbind()`。"""

    def __init__(self, server: "FakeServer", user: str | None = None, password: str | None = None):
        self.server = server
        self.user = user
        self.password = password
        self.entries: list = []

    def bind(self) -> bool:
        if self.server.connection_error is not None:
            raise self.server.connection_error
        if self.user is None:
            return self.server.allow_anonymous
        return self.server.check_credentials(self.user, self.password)

    def search(self, search_base, search_filter, attributes) -> bool:
        entry = self.server.find_entry(search_filter)
        self.entries = [entry] if entry else []
        return bool(entry)

    def unbind(self) -> None:
        pass


class FakeServer:
    """對應 ldap3 的 Server：記錄測試設定的帳密／查詢條目，供 `FakeConnection` 使用。"""

    def __init__(self):
        self.allow_anonymous = False
        self.connection_error: Exception | None = None
        self._creds: dict[str, str] = {}
        self._entries_by_sam: dict[str, FakeEntry] = {}

    def add_user(
        self,
        *,
        sam: str,
        password: str,
        entry: FakeEntry | None = None,
        upn: str | None = None,
        domain: str | None = None,
    ) -> None:
        self._creds[sam.lower()] = password
        if upn:
            self._creds[upn.lower()] = password
        if domain:
            self._creds[f"{domain}\\{sam}".lower()] = password
        if entry is not None:
            self._entries_by_sam[sam.lower()] = entry

    def check_credentials(self, bind_identifier: str, password: str | None) -> bool:
        expected = self._creds.get(bind_identifier.lower())
        return expected is not None and expected == password

    def find_entry(self, search_filter: str) -> FakeEntry | None:
        match = re.search(r"sAMAccountName=([^)]+)", search_filter)
        if not match:
            return None
        return self._entries_by_sam.get(match.group(1).lower())


@pytest.fixture
def fake_ldap(monkeypatch):
    """monkeypatch `ldap3.Server`/`ldap3.Connection` 為上面的假物件；
    回傳共用的 `FakeServer` 供測試配置帳密／使用者條目。"""
    import ldap3

    server = FakeServer()

    def _server_factory(address, use_ssl=True, get_info=None):
        return server

    def _connection_factory(srv, user=None, password=None, **kwargs):
        return FakeConnection(srv, user=user, password=password)

    monkeypatch.setattr(ldap3, "Server", _server_factory)
    monkeypatch.setattr(ldap3, "Connection", _connection_factory)
    return server


# -- DB / HTTP client（比照 tests/auth/conftest.py）--------------------------------


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
def session_factory(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest.fixture
async def db_session(session_factory):
    async with session_factory() as session:
        yield session


@pytest.fixture
async def client(session_factory):
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
    token, _ = auth_service.create_access_token(user)
    return {"Authorization": f"Bearer {token}"}
