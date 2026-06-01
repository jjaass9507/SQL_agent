"""Tests for IncrementalMigrationWriter — diff-driven ALTER migration."""
from agents.writers.incremental_migration_writer import IncrementalMigrationWriter
from models.schema import ColumnSpec, TableSpec


def _col(name, dtype="UUID", **kw):
    return ColumnSpec(name=name, data_type=dtype, nullable=kw.pop("nullable", False),
                      description=kw.pop("description", ""), **kw)


def _users(extra_cols=()):
    cols = [_col("id", is_primary_key=True), *extra_cols]
    return TableSpec(table_name="users", description="u", columns=cols)


def test_no_existing_db_short_circuits():
    # No existing schema → nothing to diff against, no API call
    out = IncrementalMigrationWriter().generate([_users()], [])
    assert "沒有匯入現有資料庫" in out


def test_no_changes_short_circuits():
    designed = [_users()]
    existing = [_users()]
    out = IncrementalMigrationWriter().generate(designed, existing)
    assert "無需任何變更" in out


def test_changes_invoke_api(monkeypatch):
    captured = {}

    class FakeAPI:
        def chat(self, system_prompt, human_prompt):
            captured["system"] = system_prompt
            captured["human"] = human_prompt
            return "ALTER TABLE users ADD COLUMN email VARCHAR(255);"

    monkeypatch.setattr("agents.writers.incremental_migration_writer.get_api", lambda: FakeAPI())

    designed = [_users(extra_cols=[_col("email", "VARCHAR", length=255)])]
    existing = [_users()]
    out = IncrementalMigrationWriter().generate(designed, existing)

    assert "ALTER TABLE users ADD COLUMN email" in out
    # diff summary + both schemas are handed to the LLM
    assert "diff_summary" in captured["human"]
    assert "email" in captured["human"]


def test_api_empty_response_fallback(monkeypatch):
    class FakeAPI:
        def chat(self, system_prompt, human_prompt):
            return None

    monkeypatch.setattr("agents.writers.incremental_migration_writer.get_api", lambda: FakeAPI())
    designed = [_users(extra_cols=[_col("email", "VARCHAR", length=255)])]
    out = IncrementalMigrationWriter().generate(designed, [_users()])
    assert "產出失敗" in out
