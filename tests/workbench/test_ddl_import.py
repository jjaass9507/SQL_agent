"""POST /ddl-import：貼 DDL → session 建立 → tables 正確（含與 schema-tree 串接的全流程）。"""

DDL = """
CREATE TABLE users (
    id uuid PRIMARY KEY,
    email varchar(255) NOT NULL UNIQUE
);
CREATE TABLE orders (
    id uuid PRIMARY KEY,
    user_id uuid REFERENCES users(id),
    total numeric(10,2)
);
"""


async def test_ddl_import_empty_ddl_rejected(client):
    resp = await client.post("/api/v1/ddl-import", json={"ddl": "   "})
    assert resp.status_code == 400


async def test_ddl_import_unparseable_ddl_rejected(client):
    resp = await client.post("/api/v1/ddl-import", json={"ddl": "not a create table statement"})
    assert resp.status_code == 400


async def test_ddl_import_creates_session_with_correct_tables(client):
    resp = await client.post("/api/v1/ddl-import", json={"title": "匯入測試", "ddl": DDL})
    assert resp.status_code == 201
    body = resp.json()
    assert body["table_count"] == 2
    session_id = body["id"]

    tree_resp = await client.get(f"/api/v1/sessions/{session_id}/schema-tree")
    assert tree_resp.status_code == 200
    tree = tree_resp.json()
    assert tree["source"] == "design"
    names = {t["name"] for t in tree["tables"]}
    assert names == {"users", "orders"}

    orders = next(t for t in tree["tables"] if t["name"] == "orders")
    user_id_col = next(c for c in orders["columns"] if c["name"] == "user_id")
    assert user_id_col["is_fk"] is True
    assert user_id_col["fk_table"] == "users"
