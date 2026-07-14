"""POST /sessions/{id}/import-db。"""

from app.rules.spec_models import tables_from_json
from app.services import session_service
from tests.api.conftest import sample_table


async def test_import_db_success_stores_context_tables(client, monkeypatch):
    tables_json = [sample_table("products")]

    async def _fake_schema_tree(db_url):
        return tables_from_json(tables_json), ""

    monkeypatch.setattr(session_service.dbops, "schema_tree", _fake_schema_tree)

    session = (await client.post("/api/v1/sessions", json={})).json()

    resp = await client.post(
        f"/api/v1/sessions/{session['id']}/import-db",
        json={"db_url": "postgresql://u:p@h/db"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["table_count"] == 1
    assert body["context_tables"][0]["table_name"] == "products"
    assert "db_url" not in body

    detail = (await client.get(f"/api/v1/sessions/{session['id']}")).json()
    assert detail["context_tables"][0]["table_name"] == "products"


async def test_import_db_connection_failure_returns_400(client, monkeypatch):
    async def _fake_schema_tree(db_url):
        return [], "連線失敗：timeout"

    monkeypatch.setattr(session_service.dbops, "schema_tree", _fake_schema_tree)

    session = (await client.post("/api/v1/sessions", json={})).json()

    resp = await client.post(
        f"/api/v1/sessions/{session['id']}/import-db",
        json={"db_url": "postgresql://u:p@h/db"},
    )

    assert resp.status_code == 400


async def test_import_db_session_not_found_returns_404(client):
    resp = await client.post(
        "/api/v1/sessions/00000000-0000-0000-0000-000000000000/import-db",
        json={"db_url": "postgresql://u:p@h/db"},
    )

    assert resp.status_code == 404


async def test_import_db_empty_url_returns_422(client):
    session = (await client.post("/api/v1/sessions", json={})).json()

    resp = await client.post(
        f"/api/v1/sessions/{session['id']}/import-db", json={"db_url": ""}
    )

    assert resp.status_code == 422
