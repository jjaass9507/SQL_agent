"""GET /sessions/{id}/versions、POST /sessions/{id}/versions/{n}/restore。"""

import respx

from tests.api.conftest import BASE_URL, interview_turn_payload, sample_table
from tests.llm.conftest import chat_completion_response


async def _send_turn_with_table(client, session_id: str, table_name: str) -> None:
    tables = [sample_table(table_name)]
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/chat/completions").mock(
            return_value=chat_completion_response(
                content=interview_turn_payload(f"設計了 {table_name}", tables=tables)
            )
        )
        resp = await client.post(
            f"/api/v1/sessions/{session_id}/messages",
            json={"content": f"我要一張 {table_name} 表"},
        )
    assert resp.status_code == 200


async def test_versions_list_returns_snapshots_newest_first(client):
    session = (await client.post("/api/v1/sessions", json={})).json()
    await _send_turn_with_table(client, session["id"], "users")
    await _send_turn_with_table(client, session["id"], "orders")

    resp = await client.get(f"/api/v1/sessions/{session['id']}/versions")

    assert resp.status_code == 200
    versions = resp.json()
    assert [v["version_num"] for v in versions] == [2, 1]
    assert versions[0]["tables"][0]["table_name"] == "orders"
    assert versions[1]["tables"][0]["table_name"] == "users"


async def test_restore_version_creates_new_version_and_sets_confirming(client):
    session = (await client.post("/api/v1/sessions", json={})).json()
    await _send_turn_with_table(client, session["id"], "users")
    await _send_turn_with_table(client, session["id"], "orders")

    resp = await client.post(f"/api/v1/sessions/{session['id']}/versions/1/restore")

    assert resp.status_code == 200
    body = resp.json()
    assert body["version_num"] == 3
    assert body["tables"][0]["table_name"] == "users"

    detail = (await client.get(f"/api/v1/sessions/{session['id']}")).json()
    assert detail["phase"] == "confirming"
    assert detail["latest_version"] == 3
    assert detail["latest_tables"][0]["table_name"] == "users"


async def test_restore_nonexistent_version_returns_404(client):
    session = (await client.post("/api/v1/sessions", json={})).json()

    resp = await client.post(f"/api/v1/sessions/{session['id']}/versions/99/restore")

    assert resp.status_code == 404


async def test_versions_list_session_not_found_returns_404(client):
    resp = await client.get(
        "/api/v1/sessions/00000000-0000-0000-0000-000000000000/versions"
    )

    assert resp.status_code == 404
