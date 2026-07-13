"""Unit tests for app/rules/sql_safety.py.

The `check_read_only` tests are ported from v0.5's tests/test_db_manager.py
(`_check_sql` unit tests only — the DB-connecting tests in that file
covered web/db_manager.py's execute_query/explain_query/list_tables, which
are out of scope for this pure-rule module). `check_ddl_allowlist` had no
dedicated test file in v0.5 (web/ddl_guard.py was only exercised inline via
app.py's Flask route), so its tests below are newly written to cover the
same allowlist/denylist behaviour implemented in that module.
"""
from app.rules.sql_safety import check_ddl_allowlist, check_read_only

# ── check_read_only (ported from web/db_manager.py's _check_sql) ──────────

def test_check_read_only_allows_select():
    assert check_read_only("SELECT 1") is None
    assert check_read_only("  select * from users  ") is None
    assert check_read_only("SELECT id, name FROM orders WHERE status = 'paid'") is None


def test_check_read_only_allows_explain_prefix():
    # Note: explain wrapping is the caller's job; check_read_only checks the inner SQL
    assert check_read_only("SELECT count(*) FROM sessions") is None


def test_check_read_only_rejects_empty():
    assert check_read_only("") is not None
    assert check_read_only("   ") is not None
    assert check_read_only(None) is not None  # type: ignore


def test_check_read_only_rejects_ddl():
    for stmt in [
        "CREATE TABLE t (id int)",
        "DROP TABLE users",
        "ALTER TABLE t ADD COLUMN x int",
        "TRUNCATE sessions",
    ]:
        assert check_read_only(stmt) is not None, f"Should reject: {stmt}"


def test_check_read_only_rejects_dcl():
    assert check_read_only("GRANT SELECT ON t TO u") is not None
    assert check_read_only("REVOKE ALL ON t FROM u") is not None


def test_check_read_only_rejects_dml():
    assert check_read_only("INSERT INTO t VALUES (1)") is not None
    assert check_read_only("UPDATE t SET x = 1") is not None
    assert check_read_only("DELETE FROM t WHERE id = 1") is not None
    merge = "MERGE INTO t USING s ON t.id = s.id WHEN MATCHED THEN UPDATE SET x=1"
    assert check_read_only(merge) is not None


def test_check_read_only_rejects_leading_comment_bypass():
    # The forbidden verb is hidden behind a leading comment.
    assert check_read_only("/* x */ DROP TABLE users") is not None
    assert check_read_only("-- comment\nDROP TABLE users") is not None
    assert check_read_only("  /* a */ /* b */  TRUNCATE sessions") is not None


def test_check_read_only_rejects_cte_dml():
    assert check_read_only("WITH c AS (SELECT 1) DELETE FROM users") is not None
    assert check_read_only("WITH c AS (SELECT 1) UPDATE users SET x = 1") is not None


def test_check_read_only_rejects_select_into():
    assert check_read_only("SELECT * INTO new_table FROM old_table") is not None


def test_check_read_only_allows_select_with_keywords_in_literals():
    # Keywords appearing only inside string literals / identifiers must not trip.
    assert check_read_only("SELECT * FROM notes WHERE body = 'please delete this'") is None
    assert check_read_only("SELECT create_date, updated_at FROM events") is None
    assert check_read_only("SELECT * FROM updates WHERE id IN (1, 2)") is None


# ── check_ddl_allowlist (ported from web/ddl_guard.py's check_ddl_safety) ──

def test_check_ddl_allowlist_allows_create_table():
    assert check_ddl_allowlist("CREATE TABLE t (id uuid primary key);") is None


def test_check_ddl_allowlist_allows_create_index():
    assert check_ddl_allowlist("CREATE INDEX idx_t_id ON t (id);") is None
    assert check_ddl_allowlist("CREATE UNIQUE INDEX idx_t_id ON t (id);") is None


def test_check_ddl_allowlist_allows_alter_add_column_or_constraint():
    assert check_ddl_allowlist("ALTER TABLE t ADD COLUMN x int;") is None
    assert check_ddl_allowlist("ALTER TABLE t ADD CONSTRAINT uq_x UNIQUE (x);") is None


def test_check_ddl_allowlist_rejects_empty():
    assert check_ddl_allowlist("") is not None
    assert check_ddl_allowlist("   ") is not None


def test_check_ddl_allowlist_rejects_forbidden_keywords():
    for stmt in [
        "DROP TABLE t;",
        "TRUNCATE t;",
        "DELETE FROM t;",
        "INSERT INTO t VALUES (1);",
        "UPDATE t SET x = 1;",
        "GRANT SELECT ON t TO u;",
        "REVOKE ALL ON t FROM u;",
    ]:
        assert check_ddl_allowlist(stmt) is not None, f"Should reject: {stmt}"


def test_check_ddl_allowlist_rejects_alter_column():
    assert check_ddl_allowlist("ALTER TABLE t ALTER COLUMN x TYPE text;") is not None


def test_check_ddl_allowlist_rejects_too_many_statements():
    ddl = "CREATE TABLE t (id int);" * 21
    assert check_ddl_allowlist(ddl) is not None


def test_check_ddl_allowlist_rejects_too_long():
    ddl = "CREATE TABLE t (id int); -- " + ("x" * 8001)
    assert check_ddl_allowlist(ddl) is not None
