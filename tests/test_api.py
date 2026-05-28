"""API integration tests using Flask test client. No real LLM or DB calls."""
import pytest
from unittest.mock import MagicMock, patch

from models.schema import ColumnSpec, TableSpec


# ── Fixtures ────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _isolate_data(tmp_path, monkeypatch):
    """Redirect session store to a per-test temp directory."""
    import web.session_store as ss
    monkeypatch.setattr(ss, "DATA_DIR", tmp_path)


@pytest.fixture(autouse=True)
def _clear_interviewer_store():
    """Reset in-process interviewer cache between tests."""
    import app as application
    application._interviewer_store.clear()
    yield
    application._interviewer_store.clear()


@pytest.fixture
def client():
    import app as application
    application.app.config["TESTING"] = True
    with application.app.test_client() as c:
        yield c


def _post_session(client, title="Test", mode="design"):
    return client.post("/api/sessions", json={"title": title, "mode": mode})


def _make_table():
    col = ColumnSpec(name="id", data_type="UUID", nullable=False, description="PK",
                     is_primary_key=True)
    return TableSpec(table_name="users", description="User table", columns=[col])


# ── TC-API-01: Create session — design mode ─────────────

def test_create_session_design(client):
    resp = _post_session(client, "訂單系統", "design")
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["phase"] == "collecting"
    assert data["mode"] == "design"
    assert "id" in data


# ── TC-API-02: Default mode when not provided ───────────

def test_create_session_default_mode(client):
    resp = client.post("/api/sessions", json={"title": "無 mode"})
    assert resp.status_code == 201
    assert resp.get_json()["mode"] == "design"


# ── TC-API-03: Get non-existent session ─────────────────

def test_get_nonexistent_session(client):
    resp = client.get("/api/sessions/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


# ── TC-API-04: Send empty message ───────────────────────

def test_send_empty_message(client):
    session_id = _post_session(client).get_json()["id"]
    resp = client.post(f"/api/sessions/{session_id}/messages", json={"content": ""})
    assert resp.status_code == 400
    assert "content required" in resp.get_json()["error"]


# ── TC-API-05: Send message to wrong-phase session ──────

def test_send_message_wrong_phase(client):
    from web.session_store import update_session
    session_id = _post_session(client).get_json()["id"]
    update_session(session_id, {"phase": "generating"})
    resp = client.post(f"/api/sessions/{session_id}/messages", json={"content": "hello"})
    assert resp.status_code == 400


# ── TC-API-06: Confirm with no tables ───────────────────

def test_confirm_no_tables(client):
    session_id = _post_session(client).get_json()["id"]
    resp = client.post(f"/api/sessions/{session_id}/confirm")
    assert resp.status_code == 400
    assert "no tables" in resp.get_json()["error"]


# ── TC-API-07: Double confirm prevention ────────────────

def test_confirm_double_submit(client):
    from web.session_store import set_tables
    session_id = _post_session(client).get_json()["id"]
    set_tables(session_id, [_make_table()], ["point"])

    with patch("app.run_generation"):
        r1 = client.post(f"/api/sessions/{session_id}/confirm")
        r2 = client.post(f"/api/sessions/{session_id}/confirm")

    assert r1.status_code == 200
    assert r1.get_json()["status"] == "generating"
    assert r2.status_code == 400


# ── TC-API-08: List schema versions ─────────────────────

def test_list_versions(client):
    from web.session_store import set_tables
    session_id = _post_session(client).get_json()["id"]
    table = _make_table()
    set_tables(session_id, [table], [])
    set_tables(session_id, [table], [])

    resp = client.get(f"/api/sessions/{session_id}/versions")
    assert resp.status_code == 200
    versions = resp.get_json()
    assert len(versions) == 2
    assert "version" in versions[0]
    assert "created_at" in versions[0]
    assert "table_count" in versions[0]


# ── TC-API-09: Restore non-existent version ─────────────

def test_restore_nonexistent_version(client):
    session_id = _post_session(client).get_json()["id"]
    resp = client.post(f"/api/sessions/{session_id}/versions/999/restore")
    assert resp.status_code == 404


# ── TC-API-10: Download ZIP with no outputs ─────────────

def test_download_zip_no_outputs(client):
    session_id = _post_session(client).get_json()["id"]
    resp = client.get(f"/api/sessions/{session_id}/outputs/zip")
    assert resp.status_code == 400


# ── TC-API-11: Health endpoint ──────────────────────────

def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ok"
    assert "version" in data


# ── TC-API-12: Pagination ────────────────────────────────

def test_list_sessions_pagination(client):
    for i in range(5):
        _post_session(client, title=f"Session {i}")

    resp = client.get("/api/sessions?limit=2&offset=0")
    assert resp.status_code == 200
    assert len(resp.get_json()) == 2

    resp = client.get("/api/sessions?limit=10&offset=3")
    assert resp.status_code == 200
    assert len(resp.get_json()) == 2  # 5 total, skip 3 → 2 remaining


# ── TC-API-13: outputs endpoint includes generation_errors

def test_outputs_includes_errors(client):
    from web.session_store import update_generation_status
    session_id = _post_session(client).get_json()["id"]
    update_generation_status(session_id, "01_specification.md", "failed", error="timeout")

    resp = client.get(f"/api/sessions/{session_id}/outputs")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "generation_errors" in data
    assert data["generation_errors"].get("01_specification.md") == "timeout"


# ── TC-API-14: Restore version reverts to previous tables

def test_restore_version_reverts_tables(client):
    from web.session_store import set_tables, get_session
    session_id = _post_session(client).get_json()["id"]

    col_v1 = ColumnSpec(name="email", data_type="VARCHAR", nullable=False, description="")
    table_v1 = TableSpec(table_name="users", description="v1", columns=[col_v1])
    set_tables(session_id, [table_v1], ["v1 point"])

    col_v2 = ColumnSpec(name="phone", data_type="VARCHAR", nullable=True, description="")
    table_v2 = TableSpec(table_name="users", description="v2", columns=[col_v2])
    set_tables(session_id, [table_v2], ["v2 point"])

    resp = client.post(f"/api/sessions/{session_id}/versions/1/restore")
    assert resp.status_code == 200

    session = get_session(session_id)
    assert session["phase"] == "confirming"
    assert session["key_points"] == ["v1 point"]
