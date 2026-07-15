"""POST /auth/login 的 AD + 本地整合流程（mock ldap3，不需真實 AD）。"""

from sqlalchemy import select

from app.repos.models import ActivityLog, User
from tests.ad_auth.conftest import FakeEntry


async def _login(client, username, password):
    return await client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )


async def test_ad_login_success_jit_provisions_user_and_jwt_works(
    client, enable_ad, fake_ldap, db_session
):
    """AD bind 成功 → JIT 建檔（auth_source='ad'）→ 簽發的 JWT 可用於受保護端點。"""
    fake_ldap.add_user(
        sam="jdoe",
        password="secret123",
        entry=FakeEntry(
            sAMAccountName="jdoe",
            displayName="John Doe",
            mail="jdoe@corp.local",
            memberOf=[],
        ),
    )
    resp = await _login(client, "jdoe", "secret123")
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"]
    assert fake_ldap.bind_attempts == ["TESTDOM\\jdoe"]  # NETBIOS\sam 格式

    user = (
        (await db_session.execute(select(User).where(User.email == "jdoe@corp.local")))
        .scalars()
        .one()
    )
    assert user.auth_source == "ad"
    assert user.display_name == "John Doe"
    assert user.password_hash is None
    assert user.role == "user"

    # JWT 可正常用於受保護端點（帶著 access_token cookie，login 呼叫已存進 client jar）。
    resp2 = await client.get("/api/v1/sessions")
    assert resp2.status_code == 200


async def test_ad_login_no_mail_attribute_email_falls_back_to_ad_domain(
    client, enable_ad, fake_ldap, db_session
):
    """AD 條目沒有 mail 屬性：email 組 `sam@AD_DOMAIN`。"""
    fake_ldap.add_user(
        sam="nomail", password="secret123", entry=FakeEntry(sAMAccountName="nomail")
    )
    resp = await _login(client, "nomail", "secret123")
    assert resp.status_code == 200
    user = (
        (await db_session.execute(select(User).where(User.email == "nomail@corp.local")))
        .scalars()
        .one()
    )
    assert user.auth_source == "ad"


async def test_ad_login_admin_group_maps_admin_role(client, enable_ad, fake_ldap, db_session):
    fake_ldap.add_user(
        sam="admin1",
        password="secret123",
        entry=FakeEntry(
            sAMAccountName="admin1",
            mail="admin1@corp.local",
            memberOf=["CN=SQLAgentAdmins,OU=Groups,DC=corp,DC=local"],
        ),
    )
    resp = await _login(client, "admin1", "secret123")
    assert resp.status_code == 200
    user = (
        (await db_session.execute(select(User).where(User.email == "admin1@corp.local")))
        .scalars()
        .one()
    )
    assert user.role == "admin"


async def test_ad_login_admin_group_via_full_dn(
    client, enable_ad, fake_ldap, monkeypatch, db_session
):
    from app.config import get_settings

    monkeypatch.setenv("AD_ADMIN_GROUP", "CN=SQLAgentAdmins,OU=Groups,DC=corp,DC=local")
    get_settings.cache_clear()
    fake_ldap.add_user(
        sam="admin2",
        password="secret123",
        entry=FakeEntry(
            sAMAccountName="admin2",
            mail="admin2@corp.local",
            memberOf=["CN=SQLAgentAdmins,OU=Groups,DC=corp,DC=local"],
        ),
    )
    resp = await _login(client, "admin2", "secret123")
    assert resp.status_code == 200
    user = (
        (await db_session.execute(select(User).where(User.email == "admin2@corp.local")))
        .scalars()
        .one()
    )
    assert user.role == "admin"


async def test_ad_login_role_refreshed_after_group_removed(
    client, enable_ad, fake_ldap, db_session
):
    """role 每次登入依 AD 群組現況刷新：admin 群組移除後應降級為 user。"""
    entry = FakeEntry(
        sAMAccountName="admin3",
        mail="admin3@corp.local",
        memberOf=["CN=SQLAgentAdmins,OU=Groups,DC=corp,DC=local"],
    )
    fake_ldap.add_user(sam="admin3", password="secret123", entry=entry)
    resp = await _login(client, "admin3", "secret123")
    assert resp.status_code == 200
    user = (
        (await db_session.execute(select(User).where(User.email == "admin3@corp.local")))
        .scalars()
        .one()
    )
    assert user.role == "admin"

    # 移除群組成員資格後再次登入
    entry._attrs["memberOf"].values = []
    resp = await _login(client, "admin3", "secret123")
    assert resp.status_code == 200
    await db_session.refresh(user)
    assert user.role == "user"


async def test_ad_bind_fail_falls_back_to_local_success(client, enable_ad, fake_ldap, make_user):
    """AD bind 失敗（帳密錯誤，非連線問題）→ fallback 本地帳密，本地帳密正確則成功。"""
    fake_ldap.add_user(sam="jdoe", password="ad-password")
    await make_user(email="local@example.com", password="local-pw-12345")

    resp = await _login(client, "local@example.com", "local-pw-12345")
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"]


async def test_ad_bind_fail_and_local_fail_401(client, enable_ad, fake_ldap, make_user):
    """AD 與本地帳密皆失敗 → 401。"""
    await make_user(email="local@example.com", password="local-pw-12345")
    resp = await _login(client, "local@example.com", "wrong-password")
    assert resp.status_code == 401


async def test_ad_login_audit_source_is_ad(client, enable_ad, fake_ldap, db_session):
    fake_ldap.add_user(sam="jdoe", password="secret123")
    await _login(client, "jdoe", "secret123")

    rows = (await db_session.execute(select(ActivityLog))).scalars().all()
    ok = next(r for r in rows if r.event == "auth.login")
    assert ok.detail_json["source"] == "ad"


async def test_local_login_audit_source_is_local(
    client, enable_ad, fake_ldap, make_user, db_session
):
    await make_user(email="local@example.com", password="local-pw-12345")
    resp = await _login(client, "local@example.com", "local-pw-12345")
    assert resp.status_code == 200

    rows = (await db_session.execute(select(ActivityLog))).scalars().all()
    ok = next(r for r in rows if r.event == "auth.login")
    assert ok.detail_json["source"] == "local"


async def test_ad_connection_error_returns_503_no_local_fallback(
    client, enable_ad, fake_ldap, make_user
):
    """AD 連線層級錯誤（非帳密錯誤）不 fallback 本地帳密，直接回 503。"""
    from ldap3.core.exceptions import LDAPSocketOpenError

    fake_ldap.connection_error = LDAPSocketOpenError("DNS 解析失敗")
    await make_user(email="local@example.com", password="local-pw-12345")

    resp = await _login(client, "local@example.com", "local-pw-12345")
    assert resp.status_code == 503


async def test_ad_mock_login_end_to_end(client, enable_ad, monkeypatch, db_session):
    """AD_MOCK=true（僅供開發）：不連 AD，任何非空密碼登入成功並 JIT 建檔。"""
    from app.config import get_settings

    monkeypatch.setenv("AD_MOCK", "true")
    get_settings.cache_clear()

    resp = await _login(client, "tester", "any-password")
    assert resp.status_code == 200
    user = (
        (await db_session.execute(select(User).where(User.email == "tester@corp.local")))
        .scalars()
        .one()
    )
    assert user.auth_source == "ad"
    assert user.role == "user"


async def test_ad_disabled_login_still_works_with_email_field(client, make_user):
    """AD_ENABLED=false（預設）：/auth/login 用舊有 `email` 欄位行為完全不變。"""
    await make_user(email="user@example.com", password="pw12345678")
    resp = await client.post(
        "/api/v1/auth/login", json={"email": "user@example.com", "password": "pw12345678"}
    )
    assert resp.status_code == 200
