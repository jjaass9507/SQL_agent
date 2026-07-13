"""Turn schema_advisor warnings into a remediation SQL script.

Pure deterministic mapping (no LLM): each warning code maps either to a runnable
ALTER/CREATE statement, or — when the fix needs human judgement (which column is
the PK, what the allowed enum values are) — to a `-- TODO` comment template.
"""

# Warning codes that we can turn into a runnable statement deterministically.
_RUNNABLE = {"fk_no_index", "likely_unique", "naive_timestamp", "missing_audit"}


def _runnable_sql(code: str, t: str, c: str) -> str | None:
    if code == "fk_no_index":
        return f"CREATE INDEX IF NOT EXISTS idx_{t}_{c} ON {t} ({c});"
    if code == "likely_unique":
        return f"ALTER TABLE {t} ADD CONSTRAINT uq_{t}_{c} UNIQUE ({c});"
    if code == "naive_timestamp":
        return f"ALTER TABLE {t} ALTER COLUMN {c} TYPE timestamptz USING {c} AT TIME ZONE 'UTC';"
    if code == "missing_audit":
        return (
            f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS created_at timestamptz "
            f"NOT NULL DEFAULT now();\n"
            f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS updated_at timestamptz "
            f"NOT NULL DEFAULT now();"
        )
    return None


def _todo_comment(code: str, t: str, c: str) -> str | None:
    if code == "no_pk":
        return f"-- [{t}] 缺少主鍵：請指定欄位後執行 ALTER TABLE {t} ADD PRIMARY KEY (<column>);"
    if code == "varchar_no_length":
        return (f"-- [{t}.{c}] varchar 未指定長度：評估上限後 "
                f"ALTER TABLE {t} ALTER COLUMN {c} TYPE varchar(<N>);")
    if code == "enum_no_check":
        return (f"-- [{t}.{c}] 建議限制可用值："
                f"ALTER TABLE {t} ADD CONSTRAINT chk_{t}_{c} CHECK ({c} IN ('<v1>','<v2>'));")
    if code == "secret_plaintext":
        return f"-- [{t}.{c}] 敏感資料：應於應用層雜湊/加密儲存，非 schema 變更"
    return None


def build_remediation_sql(warnings: list[dict]) -> str:
    """Return a remediation .sql script for the given advisor warnings, or "" if none.

    Runnable fixes come first (each annotated with the issue); items needing human
    judgement follow as TODO comments."""
    if not warnings:
        return ""

    runnable: list[str] = []
    todos: list[str] = []
    for w in warnings:
        code = w.get("code", "")
        t, c = w.get("table", ""), w.get("column", "")
        msg = w.get("message", "")
        if not t:
            continue
        sql = _runnable_sql(code, t, c) if code in _RUNNABLE else None
        if sql:
            runnable.append(f"-- {t}{('.' + c) if c else ''}：{msg}\n{sql}")
            continue
        todo = _todo_comment(code, t, c)
        if todo:
            todos.append(todo)

    if not runnable and not todos:
        return ""

    parts = ["-- 審查修復腳本（由規則式紅旗自動產生，套用前請逐項確認）",
             "-- 破壞性或需判斷的項目以註解（-- TODO）列於末段。\n"]
    if runnable:
        parts.append("-- ── 可直接套用的修復 ──")
        parts.extend(runnable)
        parts.append("")
    if todos:
        parts.append("-- ── 需人工判斷（請補齊後再執行）──")
        parts.extend(todos)
    return "\n".join(parts).rstrip() + "\n"
