"""POST /sessions/{id}/import-db。"""

import uuid

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.repos import versions
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


async def test_session_detail_diff_flags_type_change(client, db_engine, monkeypatch):
    """型態縮小（varchar 20→10，會截斷既有資料）必須被標成「變更」而非「不變」。

    差異比對一律走後端 app/rules/schema_diff.py；前端曾自行用欄位名稱做集合差集，
    這種只改型態、欄位名不變的情境會被誤判成「不變」。
    """
    def _users(length: int) -> dict:
        table = sample_table("users")
        table["columns"] = [
            {**table["columns"][0], "name": "email", "data_type": "varchar", "length": length}
        ]
        return table

    async def _fake_schema_tree(db_url):
        return tables_from_json([_users(20)]), ""

    monkeypatch.setattr(session_service.dbops, "schema_tree", _fake_schema_tree)

    session = (await client.post("/api/v1/sessions", json={})).json()
    await client.post(
        f"/api/v1/sessions/{session['id']}/import-db",
        json={"db_url": "postgresql://u:p@h/db"},
    )

    # 設計版本：同一張表、同一個欄位名，只把長度縮小
    async with async_sessionmaker(db_engine, expire_on_commit=False)() as db:
        await versions.create_version(db, uuid.UUID(session["id"]), tables_json=[_users(10)])
        await db.commit()

    detail = (await client.get(f"/api/v1/sessions/{session['id']}")).json()
    diff = detail["schema_diff"]
    assert diff is not None
    assert "users" not in diff["unchanged_tables"]
    assert "users" in diff["modified_tables"]
    changed = diff["modified_tables"]["users"]["changed_columns"]
    assert any(c["name"] == "email" for c in changed)
