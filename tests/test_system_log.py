import json


def test_log_event_writes_jsonl(tmp_path, monkeypatch):
    import web.system_log as system_log

    monkeypatch.setattr(system_log, "LOG_DIR", tmp_path)
    monkeypatch.setattr(system_log, "SYSTEM_LOG_FILE", tmp_path / "system.log.jsonl")

    system_log.log_event("sample_event", session_id="s1", count=2)

    lines = system_log.SYSTEM_LOG_FILE.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1

    record = json.loads(lines[0])
    assert record["event_type"] == "sample_event"
    assert record["context"]["session_id"] == "s1"
    assert record["context"]["count"] == 2
    assert "timestamp" in record


def test_log_event_redacts_sensitive_fields(tmp_path, monkeypatch):
    import web.system_log as system_log

    monkeypatch.setattr(system_log, "LOG_DIR", tmp_path)
    monkeypatch.setattr(system_log, "SYSTEM_LOG_FILE", tmp_path / "system.log.jsonl")

    system_log.log_event(
        "db_import_failed",
        db_url="postgresql://user:password@localhost/db",
        nested={"api_token": "secret-token", "safe": "value"},
    )

    [line] = system_log.SYSTEM_LOG_FILE.read_text(encoding="utf-8").splitlines()
    record = json.loads(line)

    assert record["context"]["db_url"] == "***REDACTED***"
    assert record["context"]["nested"]["api_token"] == "***REDACTED***"
    assert record["context"]["nested"]["safe"] == "value"
