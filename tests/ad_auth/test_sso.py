"""GET /auth/sso：IIS Windows SSO 自動登入端點（三層身分解析）。"""

from sqlalchemy import select

from app.repos.models import ActivityLog, User
from tests.ad_auth.conftest import FakeEntry, make_ntlm_type3_header


async def test_sso_disabled_returns_404(client):
    """AD_SSO_ENABLED=false（預設）：端點回 404。"""
    resp = await client.get("/api/v1/auth/sso")
    assert resp.status_code == 404


async def test_sso_disabled_even_when_ad_enabled_returns_404(client, enable_ad):
    """AD_ENABLED=true 但 AD_SSO_ENABLED 仍為 false：/auth/sso 依然 404。"""
    resp = await client.get("/api/v1/auth/sso")
    assert resp.status_code == 404


async def test_sso_no_identity_redirects_without_cookies(client, enable_ad_sso_header):
    """解析不到身分不回 401（避免 IIS 觸發瀏覽器憑證彈窗）：302 到 `/`、不帶 cookie。"""
    resp = await client.get("/api/v1/auth/sso", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/"
    set_cookies = resp.headers.get_list("set-cookie")
    assert not any(c.startswith("access_token=") for c in set_cookies)


async def test_sso_remote_user_header_with_bind_account_maps_groups(
    client, enable_ad_bind_account, enable_ad_sso_header, fake_ldap, db_session
):
    """信任 `X-Remote-User` header → 以服務帳號查群組/顯示名 → JIT → 302 到 `/`。"""
    fake_ldap.add_bind_credential("TESTDOM\\svc-sqlagent", "svc-password")
    fake_ldap.add_user(
        sam="jdoe",
        password="irrelevant",
        entry=FakeEntry(
            sAMAccountName="jdoe",
            displayName="John Doe",
            mail="jdoe@corp.local",
            memberOf=["CN=SQLAgentAdmins,OU=Groups,DC=corp,DC=local"],
        ),
    )
    resp = await client.get(
        "/api/v1/auth/sso",
        headers={"X-Remote-User": "TESTDOM\\jdoe"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/"
    set_cookies = resp.headers.get_list("set-cookie")
    assert any(c.startswith("access_token=") for c in set_cookies)
    assert any(c.startswith("refresh_token=") for c in set_cookies)
    # 群組查詢用的是服務帳號 bind
    assert fake_ldap.bind_attempts == ["TESTDOM\\svc-sqlagent"]

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


async def test_sso_without_bind_account_grants_basic_user_role(
    client, enable_ad_sso_header, fake_ldap, db_session
):
    """沒有服務帳號：不查群組（skill 明載，不做匿名查詢），只發基本 user 角色。"""
    resp = await client.get(
        "/api/v1/auth/sso",
        headers={"X-Remote-User": "someone@corp.local"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert fake_ldap.bind_attempts == []  # 完全沒有嘗試查 AD
    user = (
        (await db_session.execute(select(User).where(User.email == "someone@corp.local")))
        .scalars()
        .one()
    )
    assert user.role == "user"
    assert user.auth_source == "ad"


async def test_sso_ntlm_authorization_header_decoded(
    client, enable_ad_sso, fake_ldap, db_session
):
    """第二層：`Authorization: NTLM <Type-3>` 純 Python 解碼出帳號 → JIT → 302。"""
    resp = await client.get(
        "/api/v1/auth/sso",
        headers={"Authorization": make_ntlm_type3_header("K11879")},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    set_cookies = resp.headers.get_list("set-cookie")
    assert any(c.startswith("access_token=") for c in set_cookies)
    user = (
        (await db_session.execute(select(User).where(User.email == "K11879@corp.local")))
        .scalars()
        .one()
    )
    assert user.auth_source == "ad"
    assert user.role == "user"  # 無服務帳號 → 基本角色


async def test_sso_windows_token_mode_returns_501_on_linux(client, enable_ad_sso):
    """第三層：X-IIS-WindowsAuthToken 的 ctypes 解碼僅支援 Windows；
    非 Windows 環境（本測試環境為 Linux）一律回 501。"""
    resp = await client.get(
        "/api/v1/auth/sso",
        headers={"X-IIS-WindowsAuthToken": "1a2b"},
        follow_redirects=False,
    )
    assert resp.status_code == 501


async def test_sso_remote_user_header_takes_priority_over_ntlm(
    client, enable_ad_sso_header, fake_ldap, db_session
):
    """第一層（REMOTE_USER 式 header）優先於第二層（NTLM Authorization）。"""
    resp = await client.get(
        "/api/v1/auth/sso",
        headers={
            "X-Remote-User": "TESTDOM\\priority",
            "Authorization": make_ntlm_type3_header("ignored"),
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    user = (
        (await db_session.execute(select(User).where(User.email == "priority@corp.local")))
        .scalars()
        .one()
    )
    assert user.auth_source == "ad"
