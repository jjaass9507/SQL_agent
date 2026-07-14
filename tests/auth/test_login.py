"""POST /auth/login：成功 / 失敗 / HttpOnly Cookie / audit / rate limit。"""

from sqlalchemy import select

from app.repos.models import ActivityLog


async def _login(client, email="user@example.com", password="pw12345678"):
    return await client.post("/api/v1/auth/login", json={"email": email, "password": password})


async def test_login_success_returns_tokens(client, make_user):
    await make_user()
    resp = await _login(client)
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 15 * 60


async def test_login_sets_httponly_cookies(client, make_user):
    await make_user()
    resp = await _login(client)
    set_cookies = resp.headers.get_list("set-cookie")
    access = next(c for c in set_cookies if c.startswith("access_token="))
    refresh = next(c for c in set_cookies if c.startswith("refresh_token="))
    for cookie in (access, refresh):
        assert "HttpOnly" in cookie
        assert "Secure" in cookie
        assert "SameSite=lax" in cookie.lower() or "samesite=lax" in cookie.lower()


async def test_login_wrong_password_401(client, make_user):
    await make_user()
    resp = await _login(client, password="wrong-password")
    assert resp.status_code == 401


async def test_login_unknown_email_401(client, make_user):
    await make_user()
    resp = await _login(client, email="nobody@example.com")
    assert resp.status_code == 401


async def test_login_audit_events(client, make_user, db_session):
    user = await make_user()
    await _login(client)
    await _login(client, password="wrong-password")

    rows = (await db_session.execute(select(ActivityLog))).scalars().all()
    events = {r.event for r in rows}
    assert "auth.login" in events
    assert "auth.login_failed" in events
    ok = next(r for r in rows if r.event == "auth.login")
    assert ok.detail_json["user_id"] == str(user.id)


async def test_login_rate_limit_429(client, make_user, monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("AUTH_RATE_LIMIT_LOGIN_MAX", "3")
    get_settings.cache_clear()

    await make_user()
    for _ in range(3):
        resp = await _login(client, password="wrong-password")
        assert resp.status_code == 401
    resp = await _login(client)
    assert resp.status_code == 429
