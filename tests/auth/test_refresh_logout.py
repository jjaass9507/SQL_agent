"""POST /auth/refresh 與 /auth/logout：換發 / 撤銷後失效 / audit。"""

from sqlalchemy import select

from app.repos.models import ActivityLog


async def _login(client):
    return await client.post(
        "/api/v1/auth/login", json={"email": "user@example.com", "password": "pw12345678"}
    )


async def test_refresh_returns_new_access_token(client, make_user):
    await make_user()
    login_resp = await _login(client)
    refresh_token = login_resp.json()["refresh_token"]

    resp = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"]
    assert body["expires_in"] == 15 * 60


async def test_refresh_via_cookie(client, make_user):
    """refresh_token 也可只靠 HttpOnly Cookie 傳遞（瀏覽器情境）。"""
    await make_user()
    await _login(client)  # cookie 已存進 client 的 cookie jar
    resp = await client.post("/api/v1/auth/refresh")
    assert resp.status_code == 200


async def test_refresh_invalid_token_401(client, make_user):
    await make_user()
    resp = await client.post("/api/v1/auth/refresh", json={"refresh_token": "bogus"})
    assert resp.status_code == 401


async def test_logout_revokes_refresh_token(client, make_user):
    await make_user()
    login_resp = await _login(client)
    refresh_token = login_resp.json()["refresh_token"]

    resp = await client.post("/api/v1/auth/logout", json={"refresh_token": refresh_token})
    assert resp.status_code == 204

    resp = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert resp.status_code == 401


async def test_logout_clears_cookies_and_audits(client, make_user, db_session):
    await make_user()
    await _login(client)
    resp = await client.post("/api/v1/auth/logout")
    assert resp.status_code == 204
    set_cookies = " ".join(resp.headers.get_list("set-cookie"))
    assert 'access_token="' in set_cookies  # 清空值
    assert 'refresh_token="' in set_cookies

    rows = (await db_session.execute(select(ActivityLog))).scalars().all()
    assert "auth.logout" in {r.event for r in rows}
