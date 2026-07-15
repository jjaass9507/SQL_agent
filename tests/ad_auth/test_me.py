"""GET /auth/me：未登入 401、AUTH_ENABLED=false 匿名 200、已登入回傳使用者資訊。"""

from tests.ad_auth.conftest import bearer


async def test_me_auth_disabled_returns_anonymous(client):
    """AUTH_ENABLED=false（預設）：不論是否帶 token，一律回 200 {anonymous: true}。"""
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 200
    assert resp.json() == {
        "anonymous": True,
        "email": None,
        "display_name": None,
        "role": None,
        "auth_source": None,
    }


async def test_me_auth_enabled_without_token_401(client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("AUTH_ENABLED", "true")
    get_settings.cache_clear()
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401


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


async def test_me_ad_user_reports_auth_source_ad(
    client, enable_ad, fake_ldap, monkeypatch
):
    from app.config import get_settings

    fake_ldap.add_user(sam="jdoe", password="secret123", upn="jdoe@corp.local")
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
