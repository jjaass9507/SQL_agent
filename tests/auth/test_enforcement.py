"""AUTH_ENABLED 開關行為：false 全部照舊（匿名）、true 強制 JWT。"""

import time

import jwt
from sqlalchemy import select

from app.config import get_settings
from app.repos.models import SessionRecord
from tests.auth.conftest import bearer


async def test_auth_disabled_endpoints_work_anonymously(client):
    """AUTH_ENABLED=false（預設）：不帶任何憑證，端點行為與 v0.5 完全相同。"""
    resp = await client.post("/api/v1/sessions", json={"title": "匿名 session"})
    assert resp.status_code == 201
    session_id = resp.json()["id"]

    resp = await client.get("/api/v1/sessions")
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    resp = await client.get(f"/api/v1/sessions/{session_id}")
    assert resp.status_code == 200


async def test_auth_enabled_missing_token_401(client, enable_auth):
    resp = await client.get("/api/v1/sessions")
    assert resp.status_code == 401

    resp = await client.post("/api/v1/sessions", json={"title": "x"})
    assert resp.status_code == 401


async def test_auth_enabled_invalid_token_401(client, enable_auth):
    resp = await client.get(
        "/api/v1/sessions", headers={"Authorization": "Bearer not-a-real-token"}
    )
    assert resp.status_code == 401


async def test_auth_enabled_expired_access_token_401(client, make_user, enable_auth):
    user = await make_user()
    now = int(time.time())
    expired = jwt.encode(
        {"sub": str(user.id), "role": user.role, "iat": now - 3600, "exp": now - 1800},
        get_settings().secret_key,
        algorithm="HS256",
    )
    resp = await client.get("/api/v1/sessions", headers={"Authorization": f"Bearer {expired}"})
    assert resp.status_code == 401


async def test_auth_enabled_valid_token_ok_and_session_gets_user_id(
    client, make_user, enable_auth, db_session
):
    user = await make_user()
    resp = await client.post(
        "/api/v1/sessions", json={"title": "我的 session"}, headers=bearer(user)
    )
    assert resp.status_code == 201
    session_id = resp.json()["id"]

    record = (
        (await db_session.execute(select(SessionRecord))).scalars().one()
    )
    assert str(record.id) == session_id
    assert record.user_id == user.id


async def test_auth_enabled_cookie_authentication(client, make_user, enable_auth):
    """access token 也可只靠 HttpOnly Cookie 傳遞（瀏覽器情境）。"""
    await make_user()
    resp = await client.post(
        "/api/v1/auth/login", json={"email": "user@example.com", "password": "pw12345678"}
    )
    assert resp.status_code == 200
    # cookie 已存進 client 的 cookie jar，不帶 Authorization header
    resp = await client.get("/api/v1/sessions")
    assert resp.status_code == 200
