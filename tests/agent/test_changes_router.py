"""app/api/routers/changes.py 的 HTTP 層測試（httpx AsyncClient 打 FastAPI app）。"""

from app.config import get_settings
from tests.agent.conftest import install_fake_psycopg2, seed_business_db

_DDL = "CREATE INDEX idx_users_name ON users(name);"


async def test_create_change_request_success(client, seed_db, monkeypatch):
    install_fake_psycopg2(monkeypatch)
    await seed_business_db(seed_db, "shop", "postgresql://x/y")
    await seed_db.commit()

    resp = await client.post(
        "/api/v1/change-requests", json={"db_name": "shop", "ddl": _DDL, "reason": "加速查詢"}
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "pending"
    assert body["dry_run_ok"] is True


async def test_create_change_request_disallowed_ddl(client):
    resp = await client.post("/api/v1/change-requests", json={"ddl": "DROP TABLE users;"})
    assert resp.status_code == 422


async def test_list_change_requests_filters_by_status(client, seed_db, monkeypatch):
    install_fake_psycopg2(monkeypatch)
    await seed_business_db(seed_db, "shop", "postgresql://x/y")
    await seed_db.commit()

    await client.post("/api/v1/change-requests", json={"db_name": "shop", "ddl": _DDL})

    resp = await client.get("/api/v1/change-requests", params={"status": "pending"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["status"] == "pending"

    resp_empty = await client.get("/api/v1/change-requests", params={"status": "executed"})
    assert resp_empty.json() == []


async def test_approve_requires_admin_token_forbidden_when_unset(client, seed_db, monkeypatch):
    install_fake_psycopg2(monkeypatch)
    await seed_business_db(seed_db, "shop", "postgresql://x/y")
    await seed_db.commit()
    created = (
        await client.post("/api/v1/change-requests", json={"db_name": "shop", "ddl": _DDL})
    ).json()

    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    get_settings.cache_clear()
    resp = await client.post(f"/api/v1/change-requests/{created['proposal_id']}/approve")
    assert resp.status_code == 403
    get_settings.cache_clear()


async def test_approve_wrong_token_unauthorized(client, seed_db, monkeypatch):
    install_fake_psycopg2(monkeypatch)
    await seed_business_db(seed_db, "shop", "postgresql://x/y")
    await seed_db.commit()
    created = (
        await client.post("/api/v1/change-requests", json={"db_name": "shop", "ddl": _DDL})
    ).json()

    monkeypatch.setenv("ADMIN_TOKEN", "secret")
    get_settings.cache_clear()
    resp = await client.post(
        f"/api/v1/change-requests/{created['proposal_id']}/approve",
        headers={"X-Admin-Token": "wrong"},
    )
    assert resp.status_code == 401
    get_settings.cache_clear()


async def test_approve_success_executes(client, seed_db, monkeypatch):
    install_fake_psycopg2(monkeypatch)
    await seed_business_db(seed_db, "shop", "postgresql://x/y")
    await seed_db.commit()
    created = (
        await client.post("/api/v1/change-requests", json={"db_name": "shop", "ddl": _DDL})
    ).json()

    monkeypatch.setenv("ADMIN_TOKEN", "secret")
    get_settings.cache_clear()
    resp = await client.post(
        f"/api/v1/change-requests/{created['proposal_id']}/approve",
        headers={"X-Admin-Token": "secret"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "executed"
    get_settings.cache_clear()


async def test_approve_not_found_404(client, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "secret")
    get_settings.cache_clear()
    resp = await client.post(
        "/api/v1/change-requests/00000000-0000-0000-0000-000000000000/approve",
        headers={"X-Admin-Token": "secret"},
    )
    assert resp.status_code == 404
    get_settings.cache_clear()


async def test_reject_success(client, seed_db, monkeypatch):
    install_fake_psycopg2(monkeypatch)
    await seed_business_db(seed_db, "shop", "postgresql://x/y")
    await seed_db.commit()
    created = (
        await client.post("/api/v1/change-requests", json={"db_name": "shop", "ddl": _DDL})
    ).json()

    monkeypatch.setenv("ADMIN_TOKEN", "secret")
    get_settings.cache_clear()
    resp = await client.post(
        f"/api/v1/change-requests/{created['proposal_id']}/reject",
        headers={"X-Admin-Token": "secret"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"
    get_settings.cache_clear()


async def test_reject_requires_admin_token(client, seed_db, monkeypatch):
    install_fake_psycopg2(monkeypatch)
    await seed_business_db(seed_db, "shop", "postgresql://x/y")
    await seed_db.commit()
    created = (
        await client.post("/api/v1/change-requests", json={"db_name": "shop", "ddl": _DDL})
    ).json()

    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    get_settings.cache_clear()
    resp = await client.post(f"/api/v1/change-requests/{created['proposal_id']}/reject")
    assert resp.status_code == 403
    get_settings.cache_clear()
