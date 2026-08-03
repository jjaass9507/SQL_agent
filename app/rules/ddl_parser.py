"""
Parse PostgreSQL CREATE TABLE DDL into TableSpec / ColumnSpec objects.

Best-effort parser for the common case (column definitions, PK/UNIQUE/NOT NULL,
REFERENCES, table-level PRIMARY KEY/FOREIGN KEY). It deliberately ignores what
it cannot understand rather than failing, so a user can paste real-world DDL and
get a usable starting point to refine in the editor.
"""
import re

from app.rules.spec_models import ColumnSpec, TableSpec

# CREATE [GLOBAL|LOCAL] [TEMP] TABLE [IF NOT EXISTS] [schema.]name ( body )
_TABLE_RE = re.compile(
    r"CREATE\s+(?:(?:GLOBAL|LOCAL)\s+)?(?:(?:TEMP|TEMPORARY|UNLOGGED)\s+)?"
    r"TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r'(?:"?\w+"?\.)?'                 # optional schema prefix
    r'"?(?P<name>\w+)"?\s*'
    r"\((?P<body>.*?)\)\s*(?:;|$)",   # body up to the matching close paren
    re.IGNORECASE | re.DOTALL,
)

_TYPE_ALIASES = {
    "int": "integer", "int4": "integer", "int8": "bigint", "int2": "smallint",
    "bool": "boolean", "float4": "real", "float8": "double precision",
    "serial": "serial", "bigserial": "bigserial", "varchar": "varchar",
    "timestamptz": "timestamptz",
}


def _split_top_level(body: str) -> list[str]:
    """Split the CREATE TABLE body on commas that are not inside parentheses."""
    parts, depth, buf = [], 0, []
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def _clean_type(raw: str) -> tuple[str, int | None]:
    base = raw.strip()
    # type with length/precision, e.g. varchar(255), numeric(10,2)
    len_m = re.match(r'^(\w+)\s*\(\s*(\d+)', base)
    if len_m:
        name = len_m.group(1).lower()
        return _TYPE_ALIASES.get(name, name), int(len_m.group(2))
    # multi-word base types we want to preserve
    low = base.lower()
    for compound in ("double precision", "timestamp with time zone",
                     "timestamp without time zone", "character varying"):
        if low.startswith(compound):
            return compound, None
    # otherwise the type is just the first token
    name = base.split()[0].split("(")[0].strip().lower() if base.split() else ""
    return _TYPE_ALIASES.get(name, name), None


_TABLE_LEVEL = ("constraint", "primary", "unique", "check", "foreign",
                "exclude", "like", "--", "/*")


def _parse_column(line: str) -> ColumnSpec | None:
    line = line.strip().rstrip(",")
    if not line or line.lower().startswith(_TABLE_LEVEL):
        return None
    m = re.match(r'^"?(\w+)"?\s+(.+)$', line, re.DOTALL)
    if not m:
        return None
    name = m.group(1)
    rest = m.group(2).strip()
    data_type, length = _clean_type(rest)
    upper = rest.upper()

    is_pk = "PRIMARY KEY" in upper
    is_serial = data_type in ("serial", "bigserial")
    nullable = ("NOT NULL" not in upper) and not is_pk and not is_serial
    is_unique = ("UNIQUE" in upper) or is_pk
    fk_m = re.search(r'REFERENCES\s+"?(\w+)"?\s*(?:\(\s*"?(\w+)"?\s*\))?', rest, re.IGNORECASE)
    is_fk = bool(fk_m)
    references = None
    if fk_m:
        references = fk_m.group(1) + (f".{fk_m.group(2)}" if fk_m.group(2) else "")
    default_m = re.search(
        r'DEFAULT\s+([^,]+?)(?:\s+(?:NOT\s+NULL|UNIQUE|PRIMARY|REFERENCES|CHECK)|$)',
        rest, re.IGNORECASE)
    default = default_m.group(1).strip() if default_m else None

    return ColumnSpec(
        name=name,
        data_type=data_type,
        nullable=nullable,
        description="",
        is_primary_key=is_pk,
        is_foreign_key=is_fk,
        references=references,
        is_unique=is_unique,
        is_indexed=is_pk or is_fk,
        length=length,
        default=default,
    )


def _apply_table_constraints(body_parts: list[str], cols: dict[str, ColumnSpec]) -> None:
    """Apply table-level PRIMARY KEY (...) / FOREIGN KEY (...) to parsed columns."""
    for part in body_parts:
        up = part.strip().upper()
        pk_m = re.search(r'PRIMARY\s+KEY\s*\(([^)]+)\)', part, re.IGNORECASE)
        if up.startswith(("PRIMARY KEY", "CONSTRAINT")) and pk_m:
            for raw in pk_m.group(1).split(","):
                cname = raw.strip().strip('"')
                if cname in cols:
                    cols[cname].is_primary_key = True
                    cols[cname].is_unique = True
                    cols[cname].is_indexed = True
                    cols[cname].nullable = False
        fk_m = re.search(
            r'FOREIGN\s+KEY\s*\(\s*"?(\w+)"?\s*\)\s*REFERENCES\s+"?(\w+)"?\s*(?:\(\s*"?(\w+)"?\s*\))?',
            part, re.IGNORECASE)
        if fk_m:
            cname = fk_m.group(1)
            if cname in cols:
                cols[cname].is_foreign_key = True
                cols[cname].references = (
                    fk_m.group(2) + (f".{fk_m.group(3)}" if fk_m.group(3) else "")
                )
                cols[cname].is_indexed = True


# 先移除註解再解析：真實世界貼進來的 DDL 常帶欄位註解，行尾 `--` 會把同一個
# 逗號區段內的下一行整個吃掉。字串常值（如 DEFAULT 'x'）要保留，故一併比對。
_COMMENT_RE = re.compile(r"('(?:''|[^'])*')|--[^\n]*|/\*.*?\*/", re.DOTALL)


def _strip_comments(sql: str) -> str:
    return _COMMENT_RE.sub(lambda m: m.group(1) or "", sql)


def parse_ddl(sql: str) -> list[TableSpec]:
    """Parse one or more CREATE TABLE statements into TableSpec objects."""
    if not sql or not sql.strip():
        return []
    sql = _strip_comments(sql)
    tables: list[TableSpec] = []
    for match in _TABLE_RE.finditer(sql):
        body_parts = _split_top_level(match.group("body"))
        ordered: list[ColumnSpec] = []
        col_map: dict[str, ColumnSpec] = {}
        for part in body_parts:
            col = _parse_column(part)
            if col:
                ordered.append(col)
                col_map[col.name] = col
        if not ordered:
            continue
        _apply_table_constraints(body_parts, col_map)
        related = sorted({c.references.split(".")[0] for c in ordered if c.references})
        tables.append(TableSpec(
            table_name=match.group("name"),
            description=f"{match.group('name')} 資料表（由 DDL 匯入）",
            columns=ordered,
            constraints=[],
            related_tables=related,
        ))
    return tables


# ── 反向：TableSpec → CREATE TABLE 文字 ─────────────────────────────────────
#
# 給「確認頁以 DDL 編輯」用：把目前的設計轉成可讀可改的建表語法，使用者改完
# 再走 parse_ddl() 解回 TableSpec。因此本函式只能輸出 parse_ddl 認得的語法，
# 兩者必須成對維護（tests/rules/test_ddl_parser.py 有往返測試盯著）。
#
# 說明文字（description）以 `-- ` 註解帶出，parse_ddl 會忽略它——那是刻意的：
# 欄位說明對使用者有意義，但不屬於 CREATE TABLE 的語法。


def _column_ddl(col: ColumnSpec) -> str:
    parts = [f'  {col.name} {col.data_type}' + (f"({col.length})" if col.length else "")]
    if col.is_primary_key:
        parts.append("PRIMARY KEY")
    if not col.nullable and not col.is_primary_key:
        parts.append("NOT NULL")
    if col.is_unique and not col.is_primary_key:
        parts.append("UNIQUE")
    if col.default:
        parts.append(f"DEFAULT {col.default}")
    if col.is_foreign_key and col.references:
        table, _, column = col.references.partition(".")
        parts.append(f"REFERENCES {table}({column})" if column else f"REFERENCES {table}")
    return " ".join(parts)


def to_ddl(tables: list[TableSpec]) -> str:
    """把 TableSpec 列表輸出成 CREATE TABLE 文字（可被 parse_ddl 解回）。"""
    blocks = []
    for table in tables:
        lines = []
        if table.description:
            lines.append(f"-- {table.description}")
        lines.append(f"CREATE TABLE {table.table_name} (")
        # 逗號要放在註解之前，否則整行會被 `--` 吃掉
        rendered = []
        for i, col in enumerate(table.columns):
            body = _column_ddl(col)
            if i < len(table.columns) - 1:
                body += ","
            if col.description:
                body += f"  -- {col.description}"
            rendered.append(body)
        lines.extend(rendered)
        lines.append(");")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + ("\n" if blocks else "")
