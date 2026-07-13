"""Unified SQL safety layer.

Single source of truth for:
- Comment/string-literal stripping ("skeletonizing") used to prevent keyword
  checks from being bypassed by comments or false-positiving on literal text.
- Statement splitting that respects string/comment boundaries (a `;` inside a
  string literal or comment must not be treated as a statement separator).
- Read-only guard for ad-hoc queries (db_manager) — rejects anything but a
  single SELECT/EXPLAIN statement.
- DDL allowlist guard (used by the change-request approval flow) — rejects
  anything outside a narrow set of additive DDL operations.

Both guards are built on the same `skeleton`/`split_statements` primitives so
statement boundaries can never disagree between the read-only path and the
DDL path.
"""
import re

# ── read-only guard ─────────────────────────────────────────────────────────

# Reject DML, DDL, and DCL
_FORBIDDEN_RE = re.compile(
    r"^\s*(CREATE|DROP|ALTER|TRUNCATE|GRANT|REVOKE|INSERT|UPDATE|DELETE|MERGE)\b",
    re.IGNORECASE,
)

# ── DDL allowlist guard ─────────────────────────────────────────────────────

_MAX_DDL_LEN = 8_000
_MAX_STATEMENTS = 20

# Allowlist: each statement must start with one of these patterns
_ALLOWED_RE = re.compile(
    r"^\s*("
    r"CREATE\s+(TABLE|UNIQUE\s+INDEX|INDEX)"
    r"|ALTER\s+TABLE\s+\S+\s+ADD\s+(COLUMN|CONSTRAINT)"
    r"|COMMENT\s+ON\s+(TABLE|COLUMN)"
    r")\b",
    re.IGNORECASE,
)

# Denylist: these keywords must not appear anywhere in the skeleton
_FORBIDDEN_KEYWORDS = re.compile(
    r"\b(DROP|TRUNCATE|DELETE|GRANT|REVOKE|INSERT|UPDATE|REPLACE)\b",
    re.IGNORECASE,
)

# Block ALTER COLUMN (type changes / renames / drops)
_ALTER_COLUMN_RE = re.compile(
    r"\bALTER\s+COLUMN\b",
    re.IGNORECASE,
)


def skeleton(sql: str) -> str:
    """
    Strip comments and string/identifier literals so keyword checks cannot be
    bypassed by leading comments and cannot false-positive on literal text.

    Matches are replaced with equal-length runs of spaces (not a single
    space), so `len(skeleton(sql)) == len(sql)` and every offset in the
    skeleton (e.g. a `;` position) can be used directly to index the
    original `sql` string.
    """
    def _blank(m: re.Match) -> str:
        return " " * len(m.group(0))

    s = re.sub(r"/\*.*?\*/", _blank, sql, flags=re.DOTALL)   # block comments
    s = re.sub(r"--[^\n]*", _blank, s)                        # line comments
    s = re.sub(r"'(?:''|[^'])*'", _blank, s)                  # single-quoted strings
    s = re.sub(r'"(?:""|[^"])*"', _blank, s)                  # quoted identifiers
    return s


def split_statements(sql: str) -> list[str]:
    """
    Split the original SQL text into statements, using the skeleton's `;`
    positions as split points (so `;` inside a string/comment is not a
    boundary). Returns non-empty, whitespace-stripped statements.
    """
    skel = skeleton(sql)
    parts = []
    start = 0
    for i, ch in enumerate(skel):
        if ch == ";":
            parts.append(sql[start:i])
            start = i + 1
    parts.append(sql[start:])
    return [p.strip() for p in parts if p.strip()]


def check_read_only(sql: str) -> str | None:
    """Returns an error string if the SQL is forbidden, else None.

    Only a single SELECT/EXPLAIN statement is allowed — stacked statements
    (e.g. `SELECT 1; DELETE FROM t`) are rejected outright.
    """
    if not sql or not sql.strip():
        return "SQL query is empty"
    statements = split_statements(sql)
    if len(statements) != 1:
        return "Only a single SQL statement is allowed"
    skel = skeleton(statements[0]).strip()
    if not skel:
        return "SQL query is empty"
    # First keyword after stripping comments must not be a write/DDL/DCL verb.
    if _FORBIDDEN_RE.match(skel):
        return "Only SELECT and EXPLAIN queries are allowed"
    # Data-modifying CTE, e.g. WITH x AS (...) DELETE ...
    if re.match(r"^\s*WITH\b", skel, re.IGNORECASE) and re.search(
        r"\b(INSERT|UPDATE|DELETE|MERGE)\b", skel, re.IGNORECASE
    ):
        return "Data-modifying statements are not allowed"
    # SELECT ... INTO creates a table (a write disguised as a SELECT).
    if re.match(r"^\s*SELECT\b", skel, re.IGNORECASE) and re.search(
        r"\bINTO\b", skel, re.IGNORECASE
    ):
        return "SELECT ... INTO is not allowed"
    return None


def check_ddl_allowlist(ddl: str) -> str | None:
    """Return an error string if the DDL is forbidden, else None.

    Checks:
    - Size limits
    - No forbidden keywords anywhere
    - No ALTER COLUMN
    - Statement count limit
    - Each statement matches the allowlist
    """
    if not ddl or not ddl.strip():
        return "DDL 不可為空"
    if len(ddl) > _MAX_DDL_LEN:
        return f"DDL 過長（上限 {_MAX_DDL_LEN} 字元）"

    skel = skeleton(ddl)

    if _FORBIDDEN_KEYWORDS.search(skel):
        return "DDL 包含不允許的關鍵字（DROP、TRUNCATE、DELETE、INSERT、UPDATE 等）"
    if _ALTER_COLUMN_RE.search(skel):
        return "不允許修改現有欄位（ALTER COLUMN）"

    statements = split_statements(ddl)
    if not statements:
        return "DDL 不可為空"
    if len(statements) > _MAX_STATEMENTS:
        return f"單次最多 {_MAX_STATEMENTS} 條語句"

    for stmt in statements:
        if not _ALLOWED_RE.match(skeleton(stmt).strip()):
            first_words = " ".join(stmt.split()[:4])
            return (f"不允許的語句類型：「{first_words}…」"
                    f"（僅接受 CREATE TABLE、CREATE INDEX、ALTER TABLE ADD COLUMN/CONSTRAINT）")

    return None
