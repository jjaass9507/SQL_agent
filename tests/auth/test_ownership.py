"""AUTH_ENABLED=true 時的 session 所有權驗證與 admin 角色行為。"""

from sqlalchemy import select

from app.repos import sessions as sessions_repo
from app.repos.models import ActivityLog
from tests.auth.conftest import bearer


async def _make_session_for(db_session, user, title="測試"):
    record = await sessions_repo.create_session(db_session, title=title, user_id=user.id)
    await db_session.commit()
    return record


async def test_user_cannot_read_others_session_403(client, make_user, enable_auth, db_session):
    user_a = await make_user(email="a@example.com")
    user_b = await make_user(email="b@example.com")
    session_b = await _make_session_for(db_session, user_b)

    resp = await client.get(f"/api/v1/sessions/{session_b.id}", headers=bearer(user_a))
    assert resp.status_code == 403

    # 其餘 session 子端點同樣受所有權保護
    resp = await client.get(f"/api/v1/sessions/{session_b.id}/versions", headers=bearer(user_a))
    assert resp.status_code == 403
    resp = await client.get(f"/api/v1/sessions/{session_b.id}/outputs", headers=bearer(user_a))
    assert resp.status_code == 403
    resp = await client.get(
        f"/api/v1/sessions/{session_b.id}/schema-tree", headers=bearer(user_a)
    )
    assert resp.status_code == 403


async def test_owner_can_read_own_session(client, make_user, enable_auth, db_session):
    user = await make_user()
    session = await _make_session_for(db_session, user)
    resp = await client.get(f"/api/v1/sessions/{session.id}", headers=bearer(user))
    assert resp.status_code == 200


async def test_list_sessions_filters_by_owner(client, make_user, enable_auth, db_session):
    user_a = await make_user(email="a@example.com")
    user_b = await make_user(email="b@example.com")
    session_a = await _make_session_for(db_session, user_a, title="A 的")
    await _make_session_for(db_session, user_b, title="B 的")

    resp = await client.get("/api/v1/sessions", headers=bearer(user_a))
    assert resp.status_code == 200
    ids = [s["id"] for s in resp.json()]
    assert ids == [str(session_a.id)]


async def test_admin_can_read_others_session_with_audit(
    client, make_user, enable_auth, db_session
):
    admin = await make_user(email="admin@example.com", role="admin")
    user = await make_user(email="u@example.com")
    session = await _make_session_for(db_session, user)

    resp = await client.get(f"/api/v1/sessions/{session.id}", headers=bearer(admin))
    assert resp.status_code == 200

    rows = (await db_session.execute(select(ActivityLog))).scalars().all()
    access = next(r for r in rows if r.event == "admin.session_access")
    assert access.detail_json["session_id"] == str(session.id)
    assert access.detail_json["admin_id"] == str(admin.id)


async def test_admin_sees_all_sessions_in_list(client, make_user, enable_auth, db_session):
    admin = await make_user(email="admin@example.com", role="admin")
    user = await make_user(email="u@example.com")
    await _make_session_for(db_session, user)
    await _make_session_for(db_session, admin)

    resp = await client.get("/api/v1/sessions", headers=bearer(admin))
    assert resp.status_code == 200
    assert len(resp.json()) == 2


async def test_change_request_approve_requires_admin_role(client, make_user, enable_auth):
    """AUTH_ENABLED=true 時 approve/reject 改看 JWT role：user 403、admin 通過角色檢查
    （不存在的 change request → 404，證明已通過權限關卡）。"""
    user = await make_user(email="u@example.com")
    admin = await make_user(email="admin@example.com", role="admin")
    fake_id = "00000000-0000-0000-0000-000000000000"

    resp = await client.post(f"/api/v1/change-requests/{fake_id}/approve", headers=bearer(user))
    assert resp.status_code == 403

    resp = await client.post(f"/api/v1/change-requests/{fake_id}/approve", headers=bearer(admin))
    assert resp.status_code == 404

    resp = await client.post(f"/api/v1/change-requests/{fake_id}/reject", headers=bearer(user))
    assert resp.status_code == 403
