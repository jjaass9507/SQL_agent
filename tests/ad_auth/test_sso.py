"""GET /auth/sso：IIS Windows SSO 自動登入端點。"""

from sqlalchemy import select

from app.repos.models import ActivityLog, User
from tests.ad_auth.conftest import FakeEntry


async def test_sso_disabled_returns_404(client):
    """AD_SSO_ENABLED=false（預設）：端點回 404。"""
    resp = await client.get("/api/v1/auth/sso")
    assert resp.status_code == 404


async def test_sso_disabled_even_when_ad_enabled_returns_404(client, enable_ad):
    """AD_ENABLED=true 但 AD_SSO_ENABLED 仍為 false：/auth/sso 依然 404。"""
    resp = await client.get("/api/v1/auth/sso")
    assert resp.status_code == 404


async def test_sso_remote_user_header_missing_returns_401(client, enable_ad_sso_header):
    resp = await client.get("/api/v1/auth/sso", follow_redirects=False)
    assert resp.status_code == 401


async def test_sso_remote_user_header_trusts_header_jit_and_redirects(
    client, enable_ad_sso_header, fake_ldap, db_session
):
    """信任 `X-Remote-User` header → 查 AD 補群組/顯示名 → JIT 供裝 → 302 到 `/`。"""
    fake_ldap.allow_anonymous = True
    fake_ldap.add_user(
        sam="jdoe",
        password="irrelevant",
        upn="jdoe@corp.local",
        entry=FakeEntry(
            sAMAccountName="jdoe",
            userPrincipalName="jdoe@corp.local",
            displayName="John Doe",
            mail="jdoe@corp.local",
            memberOf=["CN=SQLAgentAdmins,OU=Groups,DC=corp,DC=local"],
        ),
    )
    resp = await client.get(
        "/api/v1/auth/sso",
        headers={"X-Remote-User": "CORP\\jdoe"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/"
    set_cookies = resp.headers.get_list("set-cookie")
    assert any(c.startswith("access_token=") for c in set_cookies)
    assert any(c.startswith("refresh_token=") for c in set_cookies)

    user = (
        (await db_session.execute(select(User).where(User.email == "jdoe@corp.local")))
        .scalars()
        .one()
    )
    assert user.auth_source == "ad"
    assert user.role == "admin"
    assert user.display_name == "John Doe"

    rows = (await db_session.execute(select(ActivityLog))).scalars().all()
    ok = next(r for r in rows if r.event == "auth.login")
    assert ok.detail_json["source"] == "ad_sso"


async def test_sso_remote_user_header_ad_lookup_fails_grants_basic_user_role(
    client, enable_ad_sso_header, fake_ldap, db_session
):
    """匿名查詢失敗（伺服器不允許匿名 bind）時仍信任 header 身分，只發基本 user 角色。"""
    fake_ldap.allow_anonymous = False  # 匿名 bind 被拒
    resp = await client.get(
        "/api/v1/auth/sso",
        headers={"X-Remote-User": "someone@corp.local"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    user = (
        (await db_session.execute(select(User).where(User.email == "someone@corp.local")))
        .scalars()
        .one()
    )
    assert user.role == "user"
    assert user.auth_source == "ad"


async def test_sso_token_mode_returns_501_on_linux(client, enable_ad_sso_token):
    """未設定 `ad_sso_remote_user_header` → 落入 X-IIS-WindowsAuthToken token 模式；
    非 Windows 環境一律回 501（本測試環境為 Linux）。"""
    resp = await client.get(
        "/api/v1/auth/sso",
        headers={"X-IIS-WindowsAuthToken": "dummy-token"},
        follow_redirects=False,
    )
    assert resp.status_code == 501
