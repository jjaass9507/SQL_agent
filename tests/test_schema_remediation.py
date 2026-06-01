"""Tests for schema_advisor warning codes and deterministic remediation SQL."""
from models.schema import ColumnSpec, TableSpec
from web.schema_advisor import analyze
from web.schema_remediation import build_remediation_sql


def _col(name, dtype="UUID", **kw):
    return ColumnSpec(name=name, data_type=dtype, nullable=kw.pop("nullable", True),
                      description=kw.pop("description", ""), **kw)


# ── advisor codes ────────────────────────────────────────

def test_advisor_emits_codes():
    t = TableSpec("orders", "", [
        _col("id", is_primary_key=True),
        _col("user_id", is_foreign_key=True, references="users.id"),  # fk_no_index
        _col("email", "varchar"),                                     # likely_unique + varchar_no_length
        _col("created", "timestamp"),                                 # naive_timestamp
    ])
    codes = {w["code"] for w in analyze([t])}
    assert "fk_no_index" in codes
    assert "likely_unique" in codes
    assert "naive_timestamp" in codes
    assert "varchar_no_length" in codes


def test_advisor_missing_audit_and_no_pk():
    t = TableSpec("logs", "", [_col("msg", "text")])
    codes = {w["code"] for w in analyze([t])}
    assert "no_pk" in codes
    assert "missing_audit" in codes


# ── remediation SQL ──────────────────────────────────────

def test_remediation_runnable_fixes():
    warnings = [
        {"code": "fk_no_index", "table": "orders", "column": "user_id", "message": "x"},
        {"code": "likely_unique", "table": "users", "column": "email", "message": "x"},
        {"code": "naive_timestamp", "table": "logs", "column": "created", "message": "x"},
        {"code": "missing_audit", "table": "users", "column": "", "message": "x"},
    ]
    sql = build_remediation_sql(warnings)
    assert "CREATE INDEX IF NOT EXISTS idx_orders_user_id ON orders (user_id);" in sql
    assert "ADD CONSTRAINT uq_users_email UNIQUE (email);" in sql
    assert "ALTER COLUMN created TYPE timestamptz" in sql
    assert "created_at timestamptz NOT NULL DEFAULT now()" in sql


def test_remediation_todos_for_judgement_items():
    warnings = [
        {"code": "no_pk", "table": "logs", "column": "", "message": "x"},
        {"code": "secret_plaintext", "table": "users", "column": "password", "message": "x"},
        {"code": "enum_no_check", "table": "orders", "column": "status", "message": "x"},
    ]
    sql = build_remediation_sql(warnings)
    assert "-- TODO" in sql or "需人工判斷" in sql
    assert "ADD PRIMARY KEY" in sql       # no_pk template
    assert "password" in sql              # secret note
    assert "CHECK (status IN" in sql      # enum template


def test_remediation_empty():
    assert build_remediation_sql([]) == ""
    # warnings that map to nothing actionable still yield "" (e.g. unknown code)
    assert build_remediation_sql([{"code": "unknown", "table": "t", "column": "c", "message": "x"}]) == ""
