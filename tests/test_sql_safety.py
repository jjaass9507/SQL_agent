"""Tests for web/sql_safety.py — the unified SQL safety layer."""
from web.sql_safety import (
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
