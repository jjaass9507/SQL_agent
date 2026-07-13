"""SQL / DDL safety checks — ported from v0.5.

v0.5 split these checks across two DB-connecting modules: the read-only
query guard lived inside ``web/db_manager.py`` (``_sql_skeleton`` /
``_check_sql``), and the DDL allowlist guard was ``web/ddl_guard.py``
(``_skeleton`` / ``check_ddl_safety``). Neither module existed as a
standalone, dependency-free "sql_safety" file in v0.5. Since the checking
logic in both is pure (no DB I/O), this module consolidates just that pure
logic — unchanged — under the names used by the v2 rebuild plan
(``skeleton`` / ``split`` / ``check_read_only`` / ``check_ddl_allowlist``).
The DB-connecting parts of those two v0.5 modules (``execute_query`` etc.)
are out of scope here; they belong in a future workbench service/repo layer.
"""
import re

# ---- read-only query guard (ported from web/db_manager.py) ----

_FORBIDDEN_RE = re.compile(
    r"^\s*(CREATE|DROP|ALTER|TRUNCATE|GRANT|REVOKE|INSERT|UPDATE|DELETE|MERGE)\b",
    re.IGNORECASE,
)

# ---- DDL allowlist guard (ported from web/ddl_guard.py) ----

_MAX_DDL_LEN = 8_000
_MAX_STATEMENTS = 20

# Allowlist: each statement must start with one of these patterns
_ALLOWED_RE = re.compile(
    r"^\s*("
    r"CREATE\s+(TABLE|UNIQUE\s+INDEX|INDEX)"
    r"|ALTER\s+TABLE\s+\S+\s+ADD\s+(COLUMN|CONSTRAINT)"
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
    """Strip comments and string/identifier literals so keyword checks cannot
    be bypassed by leading comments and cannot false-positive on literal
    text."""
    s = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    s = re.sub(r"--[^\n]*", " ", s)
    s = re.sub(r"'(?:''|[^'])*'", " ", s)
    s = re.sub(r'"(?:""|[^"])*"', " ", s)
    return s


def split(sql: str) -> list[str]:
    """Split a (skeletonized) SQL string into individual statements on ';'."""
    return [s.strip() for s in sql.split(";") if s.strip()]


def check_read_only(sql: str) -> str | None:
    """Return an error string if sql is not a read-only SELECT/EXPLAIN query,
    else None. Ported from web/db_manager.py's ``_check_sql``."""
    if not sql or not sql.strip():
        return "SQL query is empty"
    skel = skeleton(sql).strip()
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

    Ported from web/ddl_guard.py's ``check_ddl_safety``. Checks:
    - Size limits
    - Each statement matches the allowlist
    - No forbidden keywords anywhere
    - No ALTER COLUMN
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

    statements = split(skel)
    if not statements:
        return "DDL 不可為空"
    if len(statements) > _MAX_STATEMENTS:
        return f"單次最多 {_MAX_STATEMENTS} 條語句"

    for stmt in statements:
        if not _ALLOWED_RE.match(stmt):
            first_words = " ".join(stmt.split()[:4])
            return (f"不允許的語句類型：「{first_words}…」"
                    f"（僅接受 CREATE TABLE、CREATE INDEX、ALTER TABLE ADD COLUMN/CONSTRAINT）")

    return None
