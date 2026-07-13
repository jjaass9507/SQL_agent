"""Tests for app/rules/sql_safety.py — the unified SQL safety layer.

Ported from origin/main's tests/test_sql_safety.py. The supplementary
section at the end keeps additional coverage from the earlier v2 port:
check_read_only cases originating from v0.5's tests/test_db_manager.py
(_check_sql unit tests) and extra check_ddl_allowlist cases not present in
the official file.
"""
from app.rules.sql_safety import (
    check_ddl_allowlist,
    check_read_only,
    skeleton,
    split_statements,
)

# ── skeleton ─────────────────────────────────────────────────────────────

def test_skeleton_preserves_length():
    samples = [
        "SELECT * FROM t WHERE x = 'it''s a test' -- comment\n",
        "/* block\ncomment */ SELECT 1",
        'SELECT "weird col" FROM t;',
        "",
        "SELECT 1",
    ]
    for sql in samples:
        assert len(skeleton(sql)) == len(sql), sql


def test_skeleton_blanks_strings_and_comments():
    sql = "SELECT 'DROP TABLE users' AS x -- DROP everything\n"
    skel = skeleton(sql)
    assert "DROP" not in skel
    assert "SELECT" in skel


# ── split_statements ─────────────────────────────────────────────────────

def test_split_statements_basic():
    assert split_statements("SELECT 1; SELECT 2") == ["SELECT 1", "SELECT 2"]


def test_split_statements_ignores_semicolon_in_string():
    # A `;` inside a string literal must not be treated as a statement boundary.
    assert split_statements("SELECT ';'") == ["SELECT ';'"]


def test_split_statements_splits_on_real_semicolon_after_string():
    stmts = split_statements("SELECT ';' ; DROP TABLE x")
    assert len(stmts) == 2
    assert stmts[0] == "SELECT ';'"
    assert stmts[1] == "DROP TABLE x"


def test_split_statements_drops_empty_segments():
    assert split_statements("SELECT 1;;;") == ["SELECT 1"]


# ── check_read_only ───────────────────────────────────────────────────────

def test_check_read_only_rejects_stacked_statements():
    assert check_read_only("SELECT 1; DELETE FROM t") is not None


def test_check_read_only_rejects_string_semicolon_bypass():
    # "SELECT ';' ; DROP TABLE x" must split into exactly two statements
    # (the `;` inside the string literal is not a boundary) and be rejected
    # for having more than one statement.
    assert check_read_only("SELECT ';' ; DROP TABLE x") is not None


def test_check_read_only_allows_single_select():
    assert check_read_only("SELECT 1") is None


def test_check_read_only_allows_single_select_with_trailing_semicolon():
    assert check_read_only("SELECT 1;") is None


def test_check_read_only_rejects_ddl():
    assert check_read_only("DROP TABLE users") is not None


def test_check_read_only_rejects_empty():
    assert check_read_only("") is not None
    assert check_read_only("   ") is not None


# ── check_ddl_allowlist ────────────────────────────────────────────────────

def test_check_ddl_allowlist_allows_create_table():
    assert check_ddl_allowlist("CREATE TABLE t (id int)") is None


def test_check_ddl_allowlist_does_not_false_positive_on_string_literal():
    # A forbidden keyword appearing only inside a string literal must not
    # trip the allowlist check.
    ddl = "CREATE TABLE t (id int, note varchar(40) DEFAULT 'please DROP nothing')"
    assert check_ddl_allowlist(ddl) is None


def test_check_ddl_allowlist_rejects_drop():
    assert check_ddl_allowlist("DROP TABLE t") is not None


def test_check_ddl_allowlist_rejects_alter_column():
    assert check_ddl_allowlist("ALTER TABLE t ALTER COLUMN x TYPE int") is not None


def test_check_ddl_allowlist_rejects_empty():
    assert check_ddl_allowlist("") is not None
    assert check_ddl_allowlist("   ") is not None


# ═══ supplementary coverage (kept from earlier v2 port) ════════════════════
# check_read_only cases below are ported from v0.5's tests/test_db_manager.py
# _check_sql unit tests (db_manager._check_sql now delegates to
# sql_safety.check_read_only); they are not in the official test_sql_safety.py.

def test_check_read_only_allows_more_selects():
    assert check_read_only("  select * from users  ") is None
    assert check_read_only("SELECT id, name FROM orders WHERE status = 'paid'") is None


def test_check_read_only_rejects_more_ddl():
    for stmt in [
        "CREATE TABLE t (id int)",
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


# Extra check_ddl_allowlist cases (written for the v2 port, kept for the
# broader allowlist/denylist coverage they add over the official file).

def test_check_ddl_allowlist_allows_create_index():
    assert check_ddl_allowlist("CREATE INDEX idx_t_id ON t (id);") is None
    assert check_ddl_allowlist("CREATE UNIQUE INDEX idx_t_id ON t (id);") is None


def test_check_ddl_allowlist_allows_alter_add_column_or_constraint():
    assert check_ddl_allowlist("ALTER TABLE t ADD COLUMN x int;") is None
    assert check_ddl_allowlist("ALTER TABLE t ADD CONSTRAINT uq_x UNIQUE (x);") is None


def test_check_ddl_allowlist_allows_comment_on():
    assert check_ddl_allowlist("COMMENT ON TABLE t IS 'orders';") is None
    assert check_ddl_allowlist('COMMENT ON COLUMN "t"."x" IS \'note\';') is None


def test_check_ddl_allowlist_rejects_forbidden_keywords():
    for stmt in [
        "TRUNCATE t;",
        "DELETE FROM t;",
        "INSERT INTO t VALUES (1);",
        "UPDATE t SET x = 1;",
        "GRANT SELECT ON t TO u;",
        "REVOKE ALL ON t FROM u;",
    ]:
        assert check_ddl_allowlist(stmt) is not None, f"Should reject: {stmt}"


def test_check_ddl_allowlist_rejects_too_many_statements():
    ddl = "CREATE TABLE t (id int);" * 21
    assert check_ddl_allowlist(ddl) is not None


def test_check_ddl_allowlist_rejects_too_long():
    ddl = "CREATE TABLE t (id int); -- " + ("x" * 8001)
    assert check_ddl_allowlist(ddl) is not None
