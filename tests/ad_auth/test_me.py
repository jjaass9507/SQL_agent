"""GET /auth/me：永遠回 200（IIS Windows Auth 下 401 會觸發瀏覽器原生憑證彈窗，
skill 明載「永遠不回 401」）；未登入回 {anonymous: true, auth_type: null}。"""

from tests.ad_auth.conftest import bearer

ANONYMOUS = {
    "anonymous": True,
    "email": None,
    "display_name": None,
    "role": None,
    "auth_source": None,
    "auth_type": None,
}


async def test_me_auth_disabled_returns_anonymous(client):
    """AUTH_ENABLED=false（預設）：一律回 200 {anonymous: true, auth_type: null}。"""
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 200
    assert resp.json() == ANONYMOUS


async def test_me_auth_enabled_without_token_returns_200_anonymous(client, monkeypatch):
    """未登入也回 200 匿名（不回 401——避免 IIS 觸發瀏覽器憑證彈窗）。"""
    from app.config import get_settings

    monkeypatch.setenv("AUTH_ENABLED", "true")
    get_settings.cache_clear()
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 200
    assert resp.json() == ANONYMOUS


async def test_me_auth_enabled_invalid_token_returns_200_anonymous(client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("AUTH_ENABLED", "true")
    get_settings.cache_clear()
    resp = await client.get(
        "/api/v1/auth/me", headers={"Authorization": "Bearer not-a-real-token"}
    )
    assert resp.status_code == 200
    assert resp.json() == ANONYMOUS


async def test_me_auth_enabled_with_token_returns_user_info(client, make_user, monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("AUTH_ENABLED", "true")
    get_settings.cache_clear()

    user = await make_user(email="user@example.com", password="pw12345678", role="admin")
    resp = await client.get("/api/v1/auth/me", headers=bearer(user))
    assert resp.status_code == 200
    body = resp.json()
    assert body["anonymous"] is False
    assert body["email"] == "user@example.com"
    assert body["role"] == "admin"
    assert body["auth_source"] == "local"
    # bearer() 直接簽 token（無 auth_type claim）→ 依 auth_source 推導為 local
    assert body["auth_type"] == "local"


async def test_me_local_login_reports_auth_type_local(client, make_user, monkeypatch):
    from app.config import get_settings

    await make_user(email="user@example.com", password="pw12345678")
    login_resp = await client.post(
        "/api/v1/auth/login", json={"email": "user@example.com", "password": "pw12345678"}
    )
    assert login_resp.status_code == 200

    monkeypatch.setenv("AUTH_ENABLED", "true")
    get_settings.cache_clear()

    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 200
    assert resp.json()["auth_type"] == "local"


async def test_me_ad_manual_login_reports_manual(client, enable_ad, fake_ldap, monkeypatch):
    from app.config import get_settings

    fake_ldap.add_user(sam="jdoe", password="secret123")
    login_resp = await client.post(
        "/api/v1/auth/login", json={"username": "jdoe", "password": "secret123"}
    )
    assert login_resp.status_code == 200

    monkeypatch.setenv("AUTH_ENABLED", "true")
    get_settings.cache_clear()

    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "jdoe@corp.local"
    assert body["auth_source"] == "ad"
    assert body["auth_type"] == "manual"


async def test_me_sso_login_reports_sso(client, enable_ad_sso_header, monkeypatch):
    from app.config import get_settings

    sso_resp = await client.get(
        "/api/v1/auth/sso",
        headers={"X-Remote-User": "TESTDOM\\jdoe"},
        follow_redirects=False,
    )
    assert sso_resp.status_code == 302

    monkeypatch.setenv("AUTH_ENABLED", "true")
    get_settings.cache_clear()

    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "jdoe@corp.local"
    assert body["auth_source"] == "ad"
    assert body["auth_type"] == "sso"
