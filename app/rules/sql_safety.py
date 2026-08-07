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

# A read-only statement must *start* with one of these. An allowlist is used
# rather than a denylist of write verbs: the latter silently admits anything
# nobody thought to add (CALL, DO, COPY ... TO PROGRAM, EXECUTE of a prepared
# write, ...), which is exactly the failure mode this guard exists to prevent.
_READ_ONLY_START_RE = re.compile(
    r"^\s*(SELECT|WITH|VALUES|TABLE|SHOW)\b",
    re.IGNORECASE,
)

# `EXPLAIN [ ( option, ... ) | option ... ] <statement>` is allowed only when the
# statement it wraps is itself read-only. This matters because `EXPLAIN ANALYZE`
# *executes* the wrapped statement — `EXPLAIN ANALYZE DELETE FROM t` really does
# delete the rows.
_EXPLAIN_HEAD_RE = re.compile(r"^\s*EXPLAIN\b\s*(\([^)]*\))?\s*", re.IGNORECASE)
_EXPLAIN_OPTION_RE = re.compile(
    r"^\s*(ANALYZE|ANALYSE|VERBOSE|COSTS|SETTINGS|BUFFERS|WAL|TIMING|SUMMARY"
    r"|GENERIC_PLAN|FORMAT|TEXT|XML|JSON|YAML|ON|OFF|TRUE|FALSE)\b\s*",
    re.IGNORECASE,
)

# Write verbs anywhere in the skeleton, not just at the start: catches them when
# nested inside an otherwise-allowed statement (`EXPLAIN ANALYZE DELETE ...`,
# `WITH c AS (...) INSERT ...`). Word boundaries keep identifiers such as
# `create_date`, `updated_at` and a table named `updates` from tripping this.
_WRITE_VERB_ANYWHERE_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|TRUNCATE|DROP|CREATE|ALTER"
    r"|GRANT|REVOKE|COPY|CALL|DO)\b",
    re.IGNORECASE,
)

# Functions that write, reach outside the current connection, or kill sessions.
# `default_transaction_read_only` does not constrain these: dblink opens its own
# connection, and pg_terminate_backend needs no transaction at all. Requiring a
# following `(` keeps same-named columns from tripping the check.
_DANGEROUS_FUNC_RE = re.compile(
    r"\b("
    # 跨連線：另開連線執行任意 SQL，不受本連線的唯讀交易約束
    r"dblink\w*"
    # 序列寫入：setval/nextval 會真的改動序列值。它們回傳數字、長得像讀取，
    # 但呼叫過後序列就回不去了——比誤下 DELETE 更難察覺。
    r"|setval|nextval"
    # 執行任意查詢字串（等同把 SQL 藏進參數裡）
    r"|query_to_xml\w*"
    # 砍連線 / 改設定 / 佔用資源
    r"|pg_terminate_backend|pg_cancel_backend|pg_reload_conf|pg_rotate_logfile"
    r"|pg_\w*advisory\w*|pg_notify|pg_sleep\w*|pg_stat_reset\w*"
    r"|pg_switch_wal|pg_create_restore_point|pg_logical_emit_message"
    r"|pg_\w*replication_slot|pg_replication_origin\w*"
    # 檔案系統
    r"|pg_read_file|pg_read_binary_file|pg_write_file|pg_ls_dir|pg_stat_file"
    r"|lo_import|lo_export|lo_get|lo_put|lo_unlink"
    r"|set_config"
    r")\s*\(",
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


def _strip_explain_prefix(skel: str) -> str:
    """Return whatever an `EXPLAIN ...` prefix wraps (unchanged if there is none)."""
    head = _EXPLAIN_HEAD_RE.match(skel)
    if not head:
        return skel
    rest = skel[head.end() :]
    while True:  # bare option words, e.g. EXPLAIN ANALYZE VERBOSE SELECT ...
        option = _EXPLAIN_OPTION_RE.match(rest)
        if not option:
            return rest.lstrip()
        rest = rest[option.end() :]


def check_read_only(sql: str) -> str | None:
    """Returns an error string if the SQL is forbidden, else None.

    Only a single SELECT/EXPLAIN statement is allowed — stacked statements
    (e.g. `SELECT 1; DELETE FROM t`) are rejected outright.
    """
    if not sql or not sql.strip():
        return "請輸入查詢內容。"
    statements = split_statements(sql)
    if len(statements) != 1:
        return "一次只能執行一句查詢，請移除多餘的分號與後面的語句。"
    skel = skeleton(statements[0]).strip()
    if not skel:
        return "請輸入查詢內容。"
    # An EXPLAIN wrapper is transparent: validate whatever it wraps.
    if not _READ_ONLY_START_RE.match(_strip_explain_prefix(skel)):
        return "這裡只能查資料（SELECT／EXPLAIN），不能新增、修改或刪除任何東西。"
    # Write verbs nested inside an allowed statement (EXPLAIN ANALYZE DELETE ...,
    # WITH c AS (...) UPDATE ...). EXPLAIN ANALYZE executes what it wraps.
    write_verb = _WRITE_VERB_ANYWHERE_RE.search(skel)
    if write_verb:
        return f"這裡只能查資料，不能執行 {write_verb.group(1).upper()}（會改動資料）。"
    # SELECT ... INTO creates a table (a write disguised as a SELECT).
    if re.match(r"^\s*SELECT\b", skel, re.IGNORECASE) and re.search(
        r"\bINTO\b", skel, re.IGNORECASE
    ):
        return "SELECT ... INTO 會建立新資料表，這裡只能查資料。"
    # Functions that write or reach outside this connection.
    danger = _DANGEROUS_FUNC_RE.search(skel)
    if danger:
        return f"{danger.group(1)}() 這個函式會改動資料或影響資料庫運作，唯讀查詢不能使用。"
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
