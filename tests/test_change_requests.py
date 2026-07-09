"""Tests for web/change_requests.py (JSON-mode storage) and the
web/routes/changes.py HITL approval blueprint. No real Postgres/psycopg2
connection — web.ddl_validator.validate_ddl / web.ddl_executor.execute_ddl
are monkeypatched, same pattern as tests/test_ddl_executor.py and
tests/test_ddl_validator.py. Flask fixtures mirror tests/test_api.py.
"""
import pytest

from web import change_requests


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _isolate_data(tmp_path, monkeypatch):
    import web.app_settings as settings
    monkeypatch.setattr(change_requests, "DATA_DIR", tmp_path)
    monkeypatch.setattr(change_requests, "_JSON_PATH", tmp_path / "change_requests.json")
    monkeypatch.setattr(settings, "_SETTINGS_PATH", tmp_path / "app_settings.json")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)


@pytest.fixture
def client():
    import app as application
    application.app.config["TESTING"] = True
    with application.app.test_client() as c:
        yield c


def _set_one_business_db(monkeypatch, name="demo", url="postgresql://x/demo"):
    import web.app_settings as app_settings
    monkeypatch.setattr(app_settings, "get_business_databases", lambda: [{"name": name, "url": url}])
    monkeypatch.setattr(app_settings, "get_business_database",
                        lambda n: {"name": name, "url": url} if n == name else None)


# ── web/change_requests.py: create/get/list/decide lifecycle ────────────────

def test_create_get_list_and_decide_lifecycle():
    req = change_requests.create("demo", "CREATE INDEX idx_x ON t(id);", "speed up", dry_run_ok=True)
    assert req["status"] == "pending"
    assert req["dry_run_ok"] is True
    assert req["decided_at"] is None
    assert change_requests.get(req["id"])["id"] == req["id"]

    pending = change_requests.list_requests(status="pending")
    assert any(r["id"] == req["id"] for r in pending)

    approved = change_requests.decide(req["id"], "approved")
    assert approved["status"] == "approved"
    assert approved["decided_at"] is not None

    executed = change_requests.decide(req["id"], "executed")
    assert executed["status"] == "executed"
    assert change_requests.list_requests(status="pending") == []


def test_decide_unknown_id_returns_none():
    assert change_requests.decide("does-not-exist", "approved") is None


def test_get_unknown_id_returns_none():
    assert change_requests.get("does-not-exist") is None


def test_list_requests_without_status_returns_all():
    change_requests.create("demo", "CREATE INDEX a ON t(id);", "", dry_run_ok=True)
    change_requests.create("demo", "CREATE INDEX b ON t(id);", "", dry_run_ok=True)
    assert len(change_requests.list_requests()) == 2


def test_decide_records_error():
    req = change_requests.create("demo", "CREATE INDEX a ON t(id);", "", dry_run_ok=True)
    failed = change_requests.decide(req["id"], "failed", error="boom")
    assert failed["status"] == "failed"
    assert failed["error"] == "boom"


# ── POST /api/change-requests: manual "送審" path (dry-run gate) ────────────

def test_create_change_request_blocks_on_dry_run_failure(client, monkeypatch):
    _set_one_business_db(monkeypatch)
    import web.ddl_validator as ddl_validator
    monkeypatch.setattr(ddl_validator, "validate_ddl", lambda ddl, url: {"ok": False, "error": "syntax error"})

    resp = client.post("/api/change-requests", json={"ddl": "CREATE INDEX idx_x ON t(id);"})
    assert resp.status_code == 400
    assert "syntax error" in resp.get_json()["error"]
    assert change_requests.list_requests() == []


def test_create_change_request_blocks_on_allowlist_violation(client, monkeypatch):
    _set_one_business_db(monkeypatch)
    resp = client.post("/api/change-requests", json={"ddl": "DROP TABLE t;"})
    assert resp.status_code == 400
    assert change_requests.list_requests() == []


def test_create_change_request_requires_ddl(client, monkeypatch):
    _set_one_business_db(monkeypatch)
    resp = client.post("/api/change-requests", json={})
    assert resp.status_code == 400


def test_create_change_request_no_business_db_configured(client):
    resp = client.post("/api/change-requests", json={"ddl": "CREATE INDEX idx_x ON t(id);"})
    assert resp.status_code == 400


def test_create_change_request_success(client, monkeypatch):
    _set_one_business_db(monkeypatch)
    import web.ddl_validator as ddl_validator
    monkeypatch.setattr(ddl_validator, "validate_ddl", lambda ddl, url: {"ok": True})

    resp = client.post("/api/change-requests", json={"ddl": "CREATE INDEX idx_x ON t(id);", "reason": "speed"})
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["status"] == "pending"
    assert data["db_name"] == "demo"
    assert data["dry_run_ok"] is True


# ── GET /api/change-requests?status=pending ──────────────────────────────────

def test_list_endpoint_filters_by_status(client):
    change_requests.create("demo", "CREATE INDEX a ON t(id);", "", dry_run_ok=True)
    r2 = change_requests.create("demo", "CREATE INDEX b ON t(id);", "", dry_run_ok=True)
    change_requests.decide(r2["id"], "rejected")

    resp = client.get("/api/change-requests?status=pending")
    ids = [x["id"] for x in resp.get_json()]
    assert r2["id"] not in ids
    assert len(ids) == 1


def test_list_endpoint_without_filter_returns_all(client):
    change_requests.create("demo", "CREATE INDEX a ON t(id);", "", dry_run_ok=True)
    change_requests.create("demo", "CREATE INDEX b ON t(id);", "", dry_run_ok=True)
    resp = client.get("/api/change-requests")
    assert len(resp.get_json()) == 2


# ── admin token gate: missing ADMIN_TOKEN -> 403, wrong token -> 401 ─────────

def test_approve_without_admin_token_env_returns_403(client):
    req = change_requests.create("demo", "CREATE INDEX a ON t(id);", "", dry_run_ok=True)
    resp = client.post(f"/api/change-requests/{req['id']}/approve")
    assert resp.status_code == 403


def test_approve_with_wrong_token_returns_401(client, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "secret")
    req = change_requests.create("demo", "CREATE INDEX a ON t(id);", "", dry_run_ok=True)
    resp = client.post(f"/api/change-requests/{req['id']}/approve",
                       headers={"X-Admin-Token": "wrong"})
    assert resp.status_code == 401


def test_reject_without_admin_token_env_returns_403(client):
    req = change_requests.create("demo", "CREATE INDEX a ON t(id);", "", dry_run_ok=True)
    resp = client.post(f"/api/change-requests/{req['id']}/reject")
    assert resp.status_code == 403


def test_reject_with_wrong_token_returns_401(client, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "secret")
    req = change_requests.create("demo", "CREATE INDEX a ON t(id);", "", dry_run_ok=True)
    resp = client.post(f"/api/change-requests/{req['id']}/reject",
                       headers={"X-Admin-Token": "wrong"})
    assert resp.status_code == 401


# ── approve: pending -> approved -> executed (mocked executor) ──────────────

def test_approve_executes_and_marks_executed(client, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "secret")
    _set_one_business_db(monkeypatch)
    import web.ddl_validator as ddl_validator
    import web.ddl_executor as ddl_executor
    monkeypatch.setattr(ddl_validator, "validate_ddl", lambda ddl, url: {"ok": True})
    monkeypatch.setattr(ddl_executor, "execute_ddl", lambda url, ddl: {"ok": True, "statements_run": 1})

    req = change_requests.create("demo", "CREATE INDEX idx_x ON t(id);", "", dry_run_ok=True)
    resp = client.post(f"/api/change-requests/{req['id']}/approve",
                       headers={"X-Admin-Token": "secret"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "executed"
    assert change_requests.get(req["id"])["status"] == "executed"


def test_approve_execution_failure_marks_failed_with_error(client, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "secret")
    _set_one_business_db(monkeypatch)
    import web.ddl_validator as ddl_validator
    import web.ddl_executor as ddl_executor
    monkeypatch.setattr(ddl_validator, "validate_ddl", lambda ddl, url: {"ok": True})
    monkeypatch.setattr(ddl_executor, "execute_ddl", lambda url, ddl: {"ok": False, "error": "constraint violation"})

    req = change_requests.create("demo", "CREATE INDEX idx_x ON t(id);", "", dry_run_ok=True)
    resp = client.post(f"/api/change-requests/{req['id']}/approve",
                       headers={"X-Admin-Token": "secret"})
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["status"] == "failed"
    assert "constraint violation" in data["error"]
    assert change_requests.get(req["id"])["status"] == "failed"


def test_approve_reruns_dry_run_and_blocks_on_failure(client, monkeypatch):
    """The DB may have drifted since the request was proposed — approve()
    re-validates before executing, and a now-failing dry-run must block
    execution rather than run the (possibly now-invalid) DDL anyway."""
    monkeypatch.setenv("ADMIN_TOKEN", "secret")
    _set_one_business_db(monkeypatch)
    import web.ddl_validator as ddl_validator
    import web.ddl_executor as ddl_executor
    monkeypatch.setattr(ddl_validator, "validate_ddl", lambda ddl, url: {"ok": False, "error": "schema drifted"})
    executed = []
    monkeypatch.setattr(ddl_executor, "execute_ddl", lambda url, ddl: executed.append(1) or {"ok": True})

    req = change_requests.create("demo", "CREATE INDEX idx_x ON t(id);", "", dry_run_ok=True)
    resp = client.post(f"/api/change-requests/{req['id']}/approve",
                       headers={"X-Admin-Token": "secret"})
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["status"] == "failed"
    assert "schema drifted" in data["error"]
    assert executed == []  # never reached execute_ddl


def test_approve_nonexistent_request_returns_404(client, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "secret")
    resp = client.post("/api/change-requests/does-not-exist/approve",
                       headers={"X-Admin-Token": "secret"})
    assert resp.status_code == 404


def test_approve_already_decided_request_returns_400(client, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "secret")
    req = change_requests.create("demo", "CREATE INDEX idx_x ON t(id);", "", dry_run_ok=True)
    change_requests.decide(req["id"], "rejected")
    resp = client.post(f"/api/change-requests/{req['id']}/approve",
                       headers={"X-Admin-Token": "secret"})
    assert resp.status_code == 400


# ── reject: rejected requests never execute ──────────────────────────────────

def test_reject_marks_rejected_and_never_executes(client, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "secret")
    import web.ddl_executor as ddl_executor
    executed = []
    monkeypatch.setattr(ddl_executor, "execute_ddl", lambda url, ddl: executed.append(1) or {"ok": True})

    req = change_requests.create("demo", "CREATE INDEX idx_x ON t(id);", "", dry_run_ok=True)
    resp = client.post(f"/api/change-requests/{req['id']}/reject",
                       headers={"X-Admin-Token": "secret"})
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "rejected"
    assert executed == []
    assert change_requests.get(req["id"])["status"] == "rejected"


def test_reject_nonexistent_request_returns_404(client, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "secret")
    resp = client.post("/api/change-requests/does-not-exist/reject",
                       headers={"X-Admin-Token": "secret"})
    assert resp.status_code == 404


def test_reject_already_decided_request_returns_400(client, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "secret")
    req = change_requests.create("demo", "CREATE INDEX idx_x ON t(id);", "", dry_run_ok=True)
    change_requests.decide(req["id"], "executed")
    resp = client.post(f"/api/change-requests/{req['id']}/reject",
                       headers={"X-Admin-Token": "secret"})
    assert resp.status_code == 400
