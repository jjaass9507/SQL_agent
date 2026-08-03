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


# ── B0：先前完全沒有認證依賴的端點 ────────────────────────────────────────
#
# 這幾個端點在 AUTH_ENABLED=false 的預設部署下曾經是誰都能打的：
# Agent 能透過工具讀取業務資料庫的真實資料、activity 是稽核紀錄、
# settings 會列出已設定的業務資料庫。加上 get_current_user 後，匿名模式
# 行為完全不變，但 AUTH_ENABLED=true 時會確實擋下未登入的請求。

_PREVIOUSLY_UNPROTECTED = [
    ("GET", "/api/v1/settings"),
    ("GET", "/api/v1/activity"),
    ("GET", "/api/v1/change-requests"),
]


async def test_previously_unprotected_endpoints_stay_open_when_auth_disabled(client):
    for method, path in _PREVIOUSLY_UNPROTECTED:
        resp = await client.request(method, path)
        assert resp.status_code == 200, f"{method} {path} 在匿名模式下不該被擋"


async def test_previously_unprotected_endpoints_require_login_when_auth_enabled(
    client, enable_auth
):
    for method, path in _PREVIOUSLY_UNPROTECTED:
        resp = await client.request(method, path)
        assert resp.status_code == 401, f"{method} {path} 在 AUTH_ENABLED=true 時應要求登入"


async def test_agent_chat_requires_login_when_auth_enabled(client, enable_auth):
    """Agent 可透過工具對業務資料庫下查詢，不該是未登入即可呼叫的端點。"""
    resp = await client.post("/api/v1/agent/chat", json={"message": "列出所有資料庫"})
    assert resp.status_code == 401
