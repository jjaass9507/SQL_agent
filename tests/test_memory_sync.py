"""Eager knowledge upload: existing-DB structure is pushed on import."""
import dataclasses
from unittest.mock import patch

import pytest

from models.schema import ColumnSpec, TableSpec


@pytest.fixture(autouse=True)
def _isolate_data(tmp_path, monkeypatch):
    import web.session_store as ss
    monkeypatch.setattr(ss, "DATA_DIR", tmp_path)


def _table():
    return TableSpec("users", "u", [ColumnSpec("id", "uuid", False, "", is_primary_key=True)])


# ── run_memory_sync ──────────────────────────────────────

def test_memory_sync_uploads_and_marks_synced(monkeypatch):
    import web.generation_worker as gw
    from web.session_store import create_session, get_session

    class FakeAPI:
        def __init__(self): self.calls = []
        def update_memory(self, content): self.calls.append(content); return True
    fake = FakeAPI()
    monkeypatch.setattr("utils.client.get_api", lambda: fake)

    sid = create_session("t", context_text="現有結構TEXT", mode="design")["id"]
    gw._memory_sync(sid)  # run synchronously

    assert fake.calls == ["現有結構TEXT"]
    assert get_session(sid)["memory_synced"] is True


def test_memory_sync_skips_when_no_context(monkeypatch):
    import web.generation_worker as gw
    from web.session_store import create_session, get_session

    called = []
    monkeypatch.setattr("utils.client.get_api",
                        lambda: type("A", (), {"update_memory": lambda s, c: called.append(c) or True})())
    sid = create_session("t", mode="design")["id"]  # no context_text
    gw._memory_sync(sid)
    assert called == []
    assert get_session(sid)["memory_synced"] is False


def test_memory_sync_upload_failure_keeps_unsynced(monkeypatch):
    import web.generation_worker as gw
    from web.session_store import create_session, get_session

    monkeypatch.setattr("utils.client.get_api",
                        lambda: type("A", (), {"update_memory": lambda s, c: False})())
    sid = create_session("t", context_text="x", mode="design")["id"]
    gw._memory_sync(sid)
    assert get_session(sid)["memory_synced"] is False


# ── import-db triggers eager sync ────────────────────────

def test_import_db_triggers_memory_sync(monkeypatch):
    import app as application
    application.app.config.update(TESTING=True)
    from web.session_store import create_session

    sid = create_session("t", mode="design")["id"]

    with patch("app.run_memory_sync") as mock_sync, \
         patch("web.db_introspect.extract_schema", return_value=([_table()], "")):
        with application.app.test_client() as c:
            resp = c.post(f"/api/sessions/{sid}/import-db",
                          json={"db_url": "postgresql://x/y"})
    assert resp.status_code == 200
    mock_sync.assert_called_once_with(sid)
