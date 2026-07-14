"""GET /sessions/{id}/schema-tree：db 模式（實際連線內省）與 design 模式（版本快照）。"""

import uuid

from app.repos import versions
from app.rules.spec_models import ColumnSpec, TableSpec, asdict


async def test_schema_tree_session_not_found(client):
    resp = await client.get(f"/api/v1/sessions/{uuid.uuid4()}/schema-tree")
    assert resp.status_code == 404


async def test_schema_tree_design_mode_no_version(client, make_session):
    """沒有 db_url 也沒有任何版本快照 → design 模式、空 tables。"""
    record = await make_session()
    resp = await client.get(f"/api/v1/sessions/{record.id}/schema-tree")
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "design"
    assert body["tables"] == []


async def test_schema_tree_design_mode_uses_latest_version(client, make_session, db_session):
    record = await make_session()
    table = TableSpec(
        table_name="users",
        description="使用者",
        columns=[
            ColumnSpec("id", "uuid", False, "PK", is_primary_key=True),
            ColumnSpec("email", "varchar", False, "電子郵件", length=255),
        ],
    )
    await versions.create_version(db_session, record.id, tables_json=[asdict(table)])
    await db_session.commit()

    resp = await client.get(f"/api/v1/sessions/{record.id}/schema-tree")
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "design"
    assert len(body["tables"]) == 1
    assert body["tables"][0]["name"] == "users"
    col_names = {c["name"] for c in body["tables"][0]["columns"]}
    assert col_names == {"id", "email"}
    pk_col = next(c for c in body["tables"][0]["columns"] if c["name"] == "id")
    assert pk_col["is_pk"] is True


async def test_schema_tree_db_mode_uses_live_introspection(client, make_session, monkeypatch):
    record = await make_session(db_url="postgresql://user:secret@host/db")
    live_table = TableSpec(
        table_name="orders",
        description="訂單",
        columns=[ColumnSpec("id", "uuid", False, "PK", is_primary_key=True)],
    )

    def _fake_extract_schema(db_url, schema="public"):
        return [live_table], ""

    monkeypatch.setattr("app.services.dbops.extract_schema", _fake_extract_schema)

    resp = await client.get(f"/api/v1/sessions/{record.id}/schema-tree")
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "db"
    assert body["tables"][0]["name"] == "orders"


async def test_schema_tree_db_mode_falls_back_to_design_on_introspection_failure(
    client, make_session, db_session, monkeypatch
):
    record = await make_session(db_url="postgresql://user:secret@host/db")
    table = TableSpec(table_name="fallback_table", description="", columns=[])
    await versions.create_version(db_session, record.id, tables_json=[asdict(table)])
    await db_session.commit()

    def _fake_extract_schema(db_url, schema="public"):
        return [], "連線失敗"

    monkeypatch.setattr("app.services.dbops.extract_schema", _fake_extract_schema)

    resp = await client.get(f"/api/v1/sessions/{record.id}/schema-tree")
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "design"
    assert body["tables"][0]["name"] == "fallback_table"
