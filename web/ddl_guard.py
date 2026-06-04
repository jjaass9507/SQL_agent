"""DDL safety validation for Schema Chat.

Only a narrow allowlist of DDL operations is permitted — the rest is rejected
before any connection to the database is made.
"""
import re

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


def _skeleton(sql: str) -> str:
    """Strip comments and string literals to prevent bypass via embedded keywords."""
    s = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    s = re.sub(r"--[^\n]*", " ", s)
    s = re.sub(r"'(?:''|[^'])*'", " ", s)
    s = re.sub(r'"(?:""|[^"])*"', " ", s)
    return s


def check_ddl_safety(ddl: str) -> str | None:
    """Return an error string if the DDL is forbidden, else None.

    Checks:
    - Size limits
    - Each statement matches the allowlist
    - No forbidden keywords anywhere
    - No ALTER COLUMN
    """
    if not ddl or not ddl.strip():
        return "DDL 不可為空"
    if len(ddl) > _MAX_DDL_LEN:
        return f"DDL 過長（上限 {_MAX_DDL_LEN} 字元）"

    skeleton = _skeleton(ddl)

    if _FORBIDDEN_KEYWORDS.search(skeleton):
        return "DDL 包含不允許的關鍵字（DROP、TRUNCATE、DELETE、INSERT、UPDATE 等）"
    if _ALTER_COLUMN_RE.search(skeleton):
        return "不允許修改現有欄位（ALTER COLUMN）"

    # Split into individual statements
    statements = [s.strip() for s in skeleton.split(";") if s.strip()]
    if not statements:
        return "DDL 不可為空"
    if len(statements) > _MAX_STATEMENTS:
        return f"單次最多 {_MAX_STATEMENTS} 條語句"

    for stmt in statements:
        if not _ALLOWED_RE.match(stmt):
            first_words = " ".join(stmt.split()[:4])
            return f"不允許的語句類型：「{first_words}…」（僅接受 CREATE TABLE、CREATE INDEX、ALTER TABLE ADD COLUMN/CONSTRAINT）"

    return None
