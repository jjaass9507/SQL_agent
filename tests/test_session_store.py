"""Session store concurrency and correctness tests (no Flask, no LLM)."""
import json
import threading
import pytest

from models.schema import ColumnSpec, TableSpec
from web.session_store import (
    create_session,
    get_session,
    add_message,
    set_tables,
    update_session,
    restore_version,
    try_start_generation,
    list_sessions,
)


# ── Fixtures ────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _isolate_data(tmp_path, monkeypatch):
    import web.session_store as ss
    monkeypatch.setattr(ss, "DATA_DIR", tmp_path)


def _make_table(name="users"):
    col = ColumnSpec(name="id", data_type="UUID", nullable=False, description="PK")
    return TableSpec(table_name=name, description="", columns=[col])


# ── TC-STORE-01: Concurrent writes to different sessions ─

def test_concurrent_writes_different_sessions(tmp_path, monkeypatch):
    import web.session_store as ss
    monkeypatch.setattr(ss, "DATA_DIR", tmp_path)

    ids = [create_session(f"Session {i}")["id"] for i in range(10)]
    errors = []

    def write_one(sid):
        try:
            for j in range(5):
                add_message(sid, "user", f"msg {j}")
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=write_one, args=(sid,)) for sid in ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    for sid in ids:
        session = get_session(sid)
        assert len(session["messages"]) == 5


# ── TC-STORE-02: Concurrent add_message to same session ──

def test_concurrent_add_message_same_session():
    sid = create_session("Concurrent")["id"]
    errors = []

    def write():
        try:
            add_message(sid, "user", "hello")
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=write) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    session = get_session(sid)
    assert len(session["messages"]) == 10


# ── TC-STORE-03: try_start_generation atomicity ──────────

def test_try_start_generation_atomic():
    sid = create_session("Gen")["id"]
    # Put in confirming phase first
    table = _make_table()
    set_tables(sid, [table], ["point"])  # sets phase=confirming

    results = []
    lock = threading.Lock()

    def try_start():
        result = try_start_generation(sid)
        with lock:
            results.append(result)

    threads = [threading.Thread(target=try_start) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count(True) == 1
    assert results.count(False) == 4
    assert get_session(sid)["phase"] == "generating"


# ── TC-STORE-04: Version limit capped at 10 ─────────────

def test_version_limit():
    sid = create_session("Versioned")["id"]
    table = _make_table()

    for _ in range(12):
        set_tables(sid, [table], ["point"])

    session = get_session(sid)
    assert len(session["table_versions"]) == 10


# ── TC-STORE-05: restore_version non-existent ───────────

def test_restore_nonexistent_version():
    sid = create_session("Restore")["id"]
    result = restore_version(sid, 99)
    assert result is False
    assert get_session(sid)["phase"] == "collecting"


# ── TC-STORE-06: restore_version correctness ────────────

def test_restore_version_correct():
    sid = create_session("Restore")["id"]

    col1 = ColumnSpec(name="email", data_type="VARCHAR", nullable=False, description="")
    t1 = TableSpec(table_name="users", description="v1", columns=[col1])
    set_tables(sid, [t1], ["v1"])

    col2 = ColumnSpec(name="phone", data_type="VARCHAR", nullable=True, description="")
    t2 = TableSpec(table_name="users", description="v2", columns=[col2])
    set_tables(sid, [t2], ["v2"])

    assert restore_version(sid, 1) is True
    session = get_session(sid)
    assert session["key_points"] == ["v1"]
    assert session["phase"] == "confirming"


# ── TC-STORE-07: list_sessions pagination ────────────────

def test_list_sessions_pagination():
    for i in range(6):
        create_session(f"S{i}")

    page1 = list_sessions(limit=3, offset=0)
    page2 = list_sessions(limit=3, offset=3)

    assert len(page1) == 3
    assert len(page2) == 3
    # No overlap
    ids1 = {s["id"] for s in page1}
    ids2 = {s["id"] for s in page2}
    assert ids1.isdisjoint(ids2)


# ── TC-STORE-08: import batch record stored ──────────────

def test_import_batch_record():
    sid = create_session("Import")["id"]
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).isoformat()
    update_session(sid, {
        "last_db_import": {"imported_at": ts, "table_count": 3, "error": None},
    })
    session = get_session(sid)
    rec = session["last_db_import"]
    assert rec["table_count"] == 3
    assert rec["error"] is None
