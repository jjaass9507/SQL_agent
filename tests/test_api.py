"""API integration tests using Flask test client. No real LLM or DB calls."""
import pytest
from unittest.mock import MagicMock, patch

from models.schema import ColumnSpec, TableSpec


# ── Fixtures ────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _isolate_data(tmp_path, monkeypatch):
    """Redirect session store to a per-test temp directory and force JSON mode,
    so a developer's configured DB (data/app_settings.json) can't leak in."""
    import web.session_store as ss
    import web.app_settings as settings
    monkeypatch.setattr(ss, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "_SETTINGS_PATH", tmp_path / "app_settings.json")
    monkeypatch.delenv("DATABASE_URL", raising=False)


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


# ── TC-API-11: Incremental migration needs an existing DB ──

def test_incremental_requires_existing_db(client):
    from web.session_store import set_tables
    session_id = _post_session(client).get_json()["id"]
    set_tables(session_id, [_make_table()], [])
    resp = client.post(f"/api/sessions/{session_id}/extras/incremental/generate")
    assert resp.status_code == 400
    assert "現有 DB" in resp.get_json()["error"]


def test_incremental_starts_with_existing_db(client):
    import dataclasses
    from web.session_store import set_tables, update_session
    session_id = _post_session(client).get_json()["id"]
    set_tables(session_id, [_make_table()], [])
    update_session(session_id, {"context_tables": [dataclasses.asdict(_make_table())]})

    with patch("app.run_incremental") as mock_run:
        resp = client.post(f"/api/sessions/{session_id}/extras/incremental/generate")
    assert resp.status_code == 200
    assert resp.get_json()["filename"] == "08_incremental_migration.sql"
    mock_run.assert_called_once_with(session_id)


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


# ── TC-API-15: Delete session ────────────────────────────

def test_delete_session(client):
    session_id = _post_session(client, "To Delete").get_json()["id"]

    resp = client.delete(f"/api/sessions/{session_id}")
    assert resp.status_code == 204

    # Session is gone
    assert client.get(f"/api/sessions/{session_id}").status_code == 404

    # No longer in list
    sessions = client.get("/api/sessions").get_json()
    assert not any(s["id"] == session_id for s in sessions)


# ── TC-API-16: Delete non-existent session ───────────────

def test_delete_nonexistent_session(client):
    resp = client.delete("/api/sessions/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


# ── TC-API-17: Rename session via PATCH ──────────────────

def test_rename_session(client):
    session_id = _post_session(client, "Original Title").get_json()["id"]

    resp = client.patch(f"/api/sessions/{session_id}", json={"title": "New Title"})
    assert resp.status_code == 200
    assert resp.get_json()["title"] == "New Title"

    # Verify the rename is persisted
    from web.session_store import get_session
    assert get_session(session_id)["title"] == "New Title"


def test_rename_session_empty_title(client):
    session_id = _post_session(client, "My Session").get_json()["id"]

    resp = client.patch(f"/api/sessions/{session_id}", json={"title": "   "})
    assert resp.status_code == 400
    assert "title required" in resp.get_json()["error"]


def test_rename_nonexistent_session(client):
    resp = client.patch("/api/sessions/00000000-0000-0000-0000-000000000000", json={"title": "x"})
    assert resp.status_code == 404


# ── TC-API-18: Review restart ────────────────────────────

def test_review_restart(client):
    from web.session_store import update_session
    session_id = _post_session(client, mode="review").get_json()["id"]
    update_session(session_id, {"phase": "review_done", "outputs": {"05_review_report.md": "old"}})

    with patch("app.run_review"):
        resp = client.post(f"/api/sessions/{session_id}/review/restart")

    assert resp.status_code == 200
    assert resp.get_json()["status"] == "reviewing"

    from web.session_store import get_session
    s = get_session(session_id)
    assert s["phase"] == "reviewing"
    assert s["outputs"] == {}


def test_review_restart_wrong_mode(client):
    session_id = _post_session(client, mode="design").get_json()["id"]
    resp = client.post(f"/api/sessions/{session_id}/review/restart")
    assert resp.status_code == 400


# ── TC-API-19: Per-file regenerate ──────────────────────

def test_regenerate_file(client):
    from web.session_store import set_tables
    session_id = _post_session(client).get_json()["id"]
    set_tables(session_id, [_make_table()], [])

    with patch("app.run_single_file"):
        resp = client.post(f"/api/sessions/{session_id}/outputs/01_specification.md/regenerate")

    assert resp.status_code == 200
    assert resp.get_json()["status"] == "regenerating"


def test_regenerate_invalid_file(client):
    from web.session_store import set_tables
    session_id = _post_session(client).get_json()["id"]
    set_tables(session_id, [_make_table()], [])

    resp = client.post(f"/api/sessions/{session_id}/outputs/malicious.sh/regenerate")
    assert resp.status_code == 400


def test_regenerate_no_tables(client):
    session_id = _post_session(client).get_json()["id"]
    resp = client.post(f"/api/sessions/{session_id}/outputs/01_specification.md/regenerate")
    assert resp.status_code == 400


# ── TC-API-26: Edit schema via PUT /tables ──────────────

def test_update_tables_saves_and_versions(client):
    from web.session_store import set_tables, get_session
    session_id = _post_session(client).get_json()["id"]
    set_tables(session_id, [_make_table()], ["p"])

    edited = {"tables": [{
        "table_name": "users",
        "description": "edited",
        "columns": [
            {"name": "id", "data_type": "uuid", "nullable": False,
             "is_primary_key": True, "description": "pk"},
            {"name": "email", "data_type": "varchar", "length": 255,
             "nullable": False, "is_unique": True, "description": "mail"},
        ],
    }]}
    resp = client.put(f"/api/sessions/{session_id}/tables", json=edited)
    assert resp.status_code == 200
    assert resp.get_json()["table_count"] == 1

    s = get_session(session_id)
    assert len(s["tables"][0]["columns"]) == 2
    assert s["tables"][0]["columns"][1]["is_unique"] is True
    assert len(s["table_versions"]) >= 2  # edit created a new version


def test_update_tables_rejects_empty(client):
    from web.session_store import set_tables
    session_id = _post_session(client).get_json()["id"]
    set_tables(session_id, [_make_table()], [])
    assert client.put(f"/api/sessions/{session_id}/tables",
                      json={"tables": []}).status_code == 400
    assert client.put(f"/api/sessions/{session_id}/tables",
                      json={"tables": [{"table_name": "", "columns": [{"name": "x"}]}]}).status_code == 400
    assert client.put(f"/api/sessions/{session_id}/tables",
                      json={"tables": [{"table_name": "t", "columns": []}]}).status_code == 400


def test_update_tables_wrong_phase(client):
    session_id = _post_session(client).get_json()["id"]  # collecting, no tables
    resp = client.put(f"/api/sessions/{session_id}/tables",
                      json={"tables": [{"table_name": "t", "columns": [{"name": "x"}]}]})
    assert resp.status_code == 400


# ── TC-API-27: Design advisor warnings on confirm page ──

def test_confirm_page_shows_advisor_warnings(client):
    from web.session_store import set_tables
    from models.schema import ColumnSpec, TableSpec
    session_id = _post_session(client).get_json()["id"]
    # email without unique + password (secret) → should trigger warnings
    cols = [
        ColumnSpec(name="id", data_type="serial", nullable=False, description="pk", is_primary_key=True),
        ColumnSpec(name="email", data_type="varchar", nullable=False, description="mail"),
        ColumnSpec(name="password", data_type="varchar", nullable=False, description="pw"),
    ]
    set_tables(session_id, [TableSpec(table_name="accounts", description="acc", columns=cols)], ["p"])
    html = client.get(f"/sessions/{session_id}/confirm").data.decode()
    assert "設計建議" in html
    assert "INITIAL_TABLES" in html


# ── TC-API-29: Continue iterating after generation ──────

def test_continue_reopens_design(client):
    from web.session_store import set_tables, update_session, get_session
    session_id = _post_session(client).get_json()["id"]
    set_tables(session_id, [_make_table()], ["p"])
    update_session(session_id, {"phase": "done"})

    resp = client.post(f"/api/sessions/{session_id}/continue")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "collecting"
    assert get_session(session_id)["phase"] == "collecting"


def test_continue_no_design(client):
    session_id = _post_session(client).get_json()["id"]  # no tables
    resp = client.post(f"/api/sessions/{session_id}/continue")
    assert resp.status_code == 400


def test_continue_rejects_review(client):
    from web.session_store import set_tables
    session_id = _post_session(client, mode="review").get_json()["id"]
    set_tables(session_id, [_make_table()], [])
    resp = client.post(f"/api/sessions/{session_id}/continue")
    assert resp.status_code == 400


# ── TC-API-28: On-demand extra outputs ──────────────────

def test_generate_extra_orm(client):
    from web.session_store import set_tables
    session_id = _post_session(client).get_json()["id"]
    set_tables(session_id, [_make_table()], [])

    with patch("app.run_single_file"):
        resp = client.post(f"/api/sessions/{session_id}/extras/orm/generate")
    assert resp.status_code == 200
    assert resp.get_json()["filename"] == "05_orm_models.py"


def test_generate_extra_invalid_kind(client):
    from web.session_store import set_tables
    session_id = _post_session(client).get_json()["id"]
    set_tables(session_id, [_make_table()], [])
    resp = client.post(f"/api/sessions/{session_id}/extras/bogus/generate")
    assert resp.status_code == 400


def test_generate_extra_no_tables(client):
    session_id = _post_session(client).get_json()["id"]
    resp = client.post(f"/api/sessions/{session_id}/extras/orm/generate")
    assert resp.status_code == 400


def test_generate_extra_concurrent_blocked(client):
    from web.session_store import set_tables, update_generation_status
    session_id = _post_session(client).get_json()["id"]
    set_tables(session_id, [_make_table()], [])
    update_generation_status(session_id, "05_orm_models.py", "loading")
    resp = client.post(f"/api/sessions/{session_id}/extras/orm/generate")
    assert resp.status_code == 409


# ── TC-API-20: mode validation ───────────────────────────

def test_create_session_invalid_mode(client):
    resp = client.post("/api/sessions", json={"title": "x", "mode": "hack"})
    assert resp.status_code == 400
    assert "mode" in resp.get_json()["error"]


# ── TC-API-21: message length limit ─────────────────────

def test_send_message_too_long(client):
    session_id = _post_session(client).get_json()["id"]
    resp = client.post(f"/api/sessions/{session_id}/messages",
                       json={"content": "x" * 10_001})
    assert resp.status_code == 400
    assert "too long" in resp.get_json()["error"]


# ── TC-API-22: 404 returns JSON ──────────────────────────

def test_404_returns_json(client):
    resp = client.get("/api/sessions/nonexistent-uuid")
    assert resp.status_code == 404
    data = resp.get_json()
    assert data is not None
    assert "error" in data


# ── TC-API-23: restore clears outputs ───────────────────

def test_restore_clears_outputs(client):
    from web.session_store import set_tables, update_generation_status, get_session
    session_id = _post_session(client).get_json()["id"]
    table = _make_table()
    set_tables(session_id, [table], ["v1"])
    update_generation_status(session_id, "01_specification.md", "done", content="spec content")
    set_tables(session_id, [table], ["v2"])

    resp = client.post(f"/api/sessions/{session_id}/versions/1/restore")
    assert resp.status_code == 200

    s = get_session(session_id)
    assert s["outputs"] == {}
    assert s["generation_status"]["01_specification.md"] == "waiting"


# ── TC-API-24: clear error on regen success ──────────────

def test_clear_error_on_success(client):
    from web.session_store import update_generation_status, get_session
    session_id = _post_session(client).get_json()["id"]
    update_generation_status(session_id, "01_specification.md", "failed", error="timeout")

    update_generation_status(session_id, "01_specification.md", "done", content="ok")

    s = get_session(session_id)
    assert "01_specification.md" not in s.get("generation_errors", {})


# ── TC-API-25: concurrent regen protection ───────────────

def test_concurrent_regen_blocked(client):
    from web.session_store import set_tables, update_generation_status
    session_id = _post_session(client).get_json()["id"]
    set_tables(session_id, [_make_table()], [])
    update_generation_status(session_id, "01_specification.md", "loading")

    resp = client.post(f"/api/sessions/{session_id}/outputs/01_specification.md/regenerate")
    assert resp.status_code == 409


# ══════════════════════════════════════════════════════════════════════════════
# TC-API-DB: Query and Explain endpoints
# ══════════════════════════════════════════════════════════════════════════════

def test_query_no_db_url(client, _isolate_data):
    """POST /query returns 400 when session has no db_url."""
    resp = client.post("/api/sessions", json={"title": "q-test"})
    session_id = resp.get_json()["id"]
    resp2 = client.post(f"/api/sessions/{session_id}/query", json={"sql": "SELECT 1"})
    assert resp2.status_code == 400
    assert "no database" in resp2.get_json()["error"]


def test_query_empty_sql(client, _isolate_data):
    """POST /query returns 400 when sql is empty."""
    resp = client.post("/api/sessions", json={"title": "q-test2"})
    session_id = resp.get_json()["id"]
    import web.session_store as ss
    ss.update_session(session_id, {"db_url": "postgresql://fake/db"})
    resp2 = client.post(f"/api/sessions/{session_id}/query", json={"sql": ""})
    assert resp2.status_code == 400


def test_query_forbidden_sql(client, _isolate_data):
    """POST /query returns 400 for DDL statements."""
    resp = client.post("/api/sessions", json={"title": "q-test3"})
    session_id = resp.get_json()["id"]
    import web.session_store as ss
    ss.update_session(session_id, {"db_url": "postgresql://fake/db"})
    resp2 = client.post(f"/api/sessions/{session_id}/query", json={"sql": "DROP TABLE users"})
    assert resp2.status_code == 400
    assert "error" in resp2.get_json()


def test_db_url_not_exposed_in_get_session(client, _isolate_data):
    """GET /api/sessions/<id> must not expose db_url."""
    resp = client.post("/api/sessions", json={"title": "q-test4"})
    session_id = resp.get_json()["id"]
    import web.session_store as ss
    ss.update_session(session_id, {"db_url": "postgresql://secret:pass@host/db"})
    resp2 = client.get(f"/api/sessions/{session_id}")
    assert resp2.status_code == 200
    assert "db_url" not in resp2.get_json()


def test_db_url_not_exposed_in_create(client, _isolate_data):
    """POST /api/sessions must not echo db_url back in the response."""
    resp = client.post("/api/sessions", json={"title": "c-test", "db_url": ""})
    assert resp.status_code == 201
    assert "db_url" not in resp.get_json()


def test_schema_tree_design_fallback(client, _isolate_data):
    """schema-tree returns designed tables when no db_url is set."""
    import web.session_store as ss
    from models.schema import ColumnSpec, TableSpec
    resp = client.post("/api/sessions", json={"title": "st-test"})
    session_id = resp.get_json()["id"]
    t = TableSpec(table_name="widgets", description="",
                  columns=[ColumnSpec(name="id", data_type="uuid", nullable=False,
                                      description="", is_primary_key=True)])
    ss.set_tables(session_id, [t], ["kp"])
    resp2 = client.get(f"/api/sessions/{session_id}/schema-tree")
    assert resp2.status_code == 200
    data = resp2.get_json()
    assert data["source"] == "design"
    assert data["tables"][0]["name"] == "widgets"
    assert data["tables"][0]["columns"][0]["is_pk"] is True


def test_schema_tree_404(client, _isolate_data):
    resp = client.get("/api/sessions/nonexistent/schema-tree")
    assert resp.status_code == 404


def test_ddl_import_valid(client, _isolate_data):
    ddl = ("CREATE TABLE users (id serial PRIMARY KEY, email varchar(255) NOT NULL);\n"
           "CREATE TABLE posts (id serial PRIMARY KEY, user_id integer REFERENCES users(id), title text);")
    resp = client.post("/api/ddl-import", json={"title": "DDL", "ddl": ddl})
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["table_count"] == 2
    sess = client.get(f"/api/sessions/{data['id']}").get_json()
    assert sess["phase"] == "confirming"
    assert len(sess["tables"]) == 2


def test_ddl_import_invalid(client, _isolate_data):
    resp = client.post("/api/ddl-import", json={"ddl": "this is not ddl"})
    assert resp.status_code == 400


def test_ddl_import_empty(client, _isolate_data):
    resp = client.post("/api/ddl-import", json={"ddl": ""})
    assert resp.status_code == 400


def test_explain_no_db_url(client, _isolate_data):
    """POST /explain returns 400 when session has no db_url."""
    resp = client.post("/api/sessions", json={"title": "e-test"})
    session_id = resp.get_json()["id"]
    resp2 = client.post(f"/api/sessions/{session_id}/explain", json={"sql": "SELECT 1"})
    assert resp2.status_code == 400


def test_explain_forbidden_sql(client, _isolate_data):
    """POST /explain returns 400 for DDL statements."""
    resp = client.post("/api/sessions", json={"title": "e-test2"})
    session_id = resp.get_json()["id"]
    import web.session_store as ss
    ss.update_session(session_id, {"db_url": "postgresql://fake/db"})
    resp2 = client.post(f"/api/sessions/{session_id}/explain", json={"sql": "CREATE TABLE x (id int)"})
    assert resp2.status_code == 400


# ── LLM health check ────────────────────────────────────

def test_llm_health_missing_env(client, monkeypatch):
    """Unset LLM env → 503 with a clear setup hint (no cached client)."""
    import utils.client as uc
    monkeypatch.setattr(uc, "_client", None)
    for var in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        monkeypatch.delenv(var, raising=False)
    resp = client.get("/api/llm/health")
    assert resp.status_code == 503
    assert "LLM_BASE_URL" in resp.get_json()["error"]


def test_llm_health_gateway_down(client, monkeypatch):
    fake = MagicMock()
    fake.ping.return_value = {"ok": False, "error": "ConnectionError: refused", "url": "u"}
    monkeypatch.setattr("utils.client.get_api", lambda: fake)
    resp = client.get("/api/llm/health")
    assert resp.status_code == 503
    assert "ConnectionError" in resp.get_json()["error"]


def test_llm_health_ok(client, monkeypatch):
    fake = MagicMock()
    fake.ping.return_value = {"ok": True, "model": "m"}
    fake.probe_system_prompt.return_value = {"honored": True, "reply": "SYSMARK_OK"}
    fake.probe_history.return_value = {"honored": True, "reply": "SYNC42"}
    fake.system_mode = "system"
    monkeypatch.setattr("utils.client.get_api", lambda: fake)
    resp = client.get("/api/llm/health")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["system_mode"] == "system"
    assert body["system_prompt_honored"] is True
    assert body["history_honored"] is True
    assert "hint" not in body


def test_llm_health_system_not_honored_hints_inline(client, monkeypatch):
    """system mode 下若 system prompt 未被遵循，提示改用 LLM_SYSTEM_MODE=inline。"""
    fake = MagicMock()
    fake.ping.return_value = {"ok": True, "model": "m"}
    fake.probe_system_prompt.return_value = {"honored": False, "reply": "我不知道你在說什麼"}
    fake.probe_history.return_value = {"honored": True, "reply": "SYNC42"}
    fake.system_mode = "system"
    monkeypatch.setattr("utils.client.get_api", lambda: fake)
    resp = client.get("/api/llm/health")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["system_prompt_honored"] is False
    assert "LLM_SYSTEM_MODE=inline" in body["hint"]
    assert "/api/llm/diagnose" in body["hint"]


def test_llm_health_inline_not_honored_hints_model_capability(client, monkeypatch):
    """已是 inline 模式仍未遵循，提示改確認模型能力，不再建議切 inline。"""
    fake = MagicMock()
    fake.ping.return_value = {"ok": True, "model": "m"}
    fake.probe_system_prompt.return_value = {"honored": False, "reply": "我不知道你在說什麼"}
    fake.probe_history.return_value = {"honored": True, "reply": "SYNC42"}
    fake.system_mode = "inline"
    monkeypatch.setattr("utils.client.get_api", lambda: fake)
    resp = client.get("/api/llm/health")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["system_prompt_honored"] is False
    assert "LLM_SYSTEM_MODE=inline" not in body["hint"]
    assert "LLM_MODEL" in body["hint"]


def test_llm_health_history_not_honored_hints_diagnose(client, monkeypatch):
    """system prompt honored 但多輪歷史未被遵循，提示打 /api/llm/diagnose。"""
    fake = MagicMock()
    fake.ping.return_value = {"ok": True, "model": "m"}
    fake.probe_system_prompt.return_value = {"honored": True, "reply": "SYSMARK_OK"}
    fake.probe_history.return_value = {"honored": False, "reply": "我不知道你的暗號"}
    fake.system_mode = "system"
    monkeypatch.setattr("utils.client.get_api", lambda: fake)
    resp = client.get("/api/llm/health")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["system_prompt_honored"] is True
    assert body["history_honored"] is False
    assert "/api/llm/diagnose" in body["hint"]


# ── /api/llm/diagnose ────────────────────────────────────

def test_llm_diagnose_missing_env(client, monkeypatch):
    def _raise():
        raise RuntimeError("請設定環境變數 LLM_BASE_URL（參考 .env.example）")
    monkeypatch.setattr("utils.client.run_capability_matrix", _raise)
    resp = client.get("/api/llm/diagnose")
    assert resp.status_code == 503
    assert "LLM_BASE_URL" in resp.get_json()["error"]


def test_llm_diagnose_returns_matrix_and_recommendation(client, monkeypatch):
    fake_result = {
        "matrix": [
            {"content_format": "string", "system_mode": "system",
             "system_prompt_honored": True, "history_honored": True},
        ],
        "recommendation": {"LLM_CONTENT_FORMAT": "string", "LLM_SYSTEM_MODE": "system"},
    }
    monkeypatch.setattr("utils.client.run_capability_matrix", lambda: fake_result)
    resp = client.get("/api/llm/diagnose")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["matrix"] == fake_result["matrix"]
    assert body["recommended"] == fake_result["recommendation"]
