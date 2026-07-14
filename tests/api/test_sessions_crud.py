"""sessions CRUD 與輸入驗證測試。"""

from app.services import session_service
from tests.api.conftest import sample_table


async def test_create_design_session_defaults(client):
    resp = await client.post("/api/v1/sessions", json={})

    assert resp.status_code == 201
    body = resp.json()
    assert body["title"] == "未命名設計"
    assert body["mode"] == "design"
    assert body["phase"] == "collecting"


async def test_create_session_title_too_long_returns_422(client):
    resp = await client.post("/api/v1/sessions", json={"title": "x" * 201})

    assert resp.status_code == 422


async def test_create_session_invalid_mode_returns_422(client):
    resp = await client.post("/api/v1/sessions", json={"mode": "not-a-mode"})

    assert resp.status_code == 422


async def test_create_review_session_without_db_url_returns_422(client):
    resp = await client.post("/api/v1/sessions", json={"mode": "review"})

    assert resp.status_code == 422


async def test_create_review_session_success(client, monkeypatch):
    tables = [sample_table("users")]

    async def _fake_schema_tree(db_url):
        assert db_url == "postgresql://user:pw@host/db"
        from app.rules.spec_models import tables_from_json

        return tables_from_json(tables), ""

    monkeypatch.setattr(session_service.dbops, "schema_tree", _fake_schema_tree)

    resp = await client.post(
        "/api/v1/sessions",
        json={"mode": "review", "db_url": "postgresql://user:pw@host/db"},
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["mode"] == "review"
    assert "db_url" not in body

    detail_resp = await client.get(f"/api/v1/sessions/{body['id']}")
    detail = detail_resp.json()
    assert detail["phase"] == "reviewing"
    assert detail["context_tables"][0]["table_name"] == "users"
    assert any(j["kind"] == "review" and j["status"] == "queued" for j in detail["jobs"])


async def test_create_review_session_connection_failure_returns_400(client, monkeypatch):
    async def _fake_schema_tree(db_url):
        return [], "連線失敗：boom"

    monkeypatch.setattr(session_service.dbops, "schema_tree", _fake_schema_tree)

    resp = await client.post(
        "/api/v1/sessions",
        json={"mode": "review", "db_url": "postgresql://user:pw@host/db"},
    )

    assert resp.status_code == 400
    assert "boom" in resp.json()["detail"]


async def test_get_session_not_found_returns_404(client):
    resp = await client.get("/api/v1/sessions/00000000-0000-0000-0000-000000000000")

    assert resp.status_code == 404


async def test_list_sessions_returns_newest_first(client):
    first = (await client.post("/api/v1/sessions", json={"title": "first"})).json()
    second = (await client.post("/api/v1/sessions", json={"title": "second"})).json()

    resp = await client.get("/api/v1/sessions")

    assert resp.status_code == 200
    ids = [s["id"] for s in resp.json()]
    assert ids.index(second["id"]) < ids.index(first["id"])
