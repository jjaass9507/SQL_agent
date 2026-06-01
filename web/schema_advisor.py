"""Heuristic schema advisor — flags common design issues before confirmation.

Pure rule-based analysis (no LLM). Returns a list of warnings so the confirm
page can act like a senior architect raising red flags before generation.
"""
import re

from models.schema import TableSpec

# Column-name keywords that usually warrant a UNIQUE constraint
_UNIQUE_HINTS = ("email", "username", "account", "code", "serial", "序號", "代碼", "帳號")
# Column-name keywords that store secrets and should be hashed/encrypted
_SECRET_HINTS = ("password", "passwd", "pwd", "token", "secret", "api_key", "apikey", "密碼")
# Column-name keywords that look like enum/status fields
_ENUM_HINTS = ("status", "state", "type", "kind", "狀態", "類型")
_AUDIT_COLS = ("created_at", "updated_at", "create_time", "update_time")
_SOFT_DELETE_COLS = ("deleted_at", "is_deleted", "deleted", "archived_at", "is_archived")
# Generic catch-all column names that often hide an under-designed schema
_BLOB_HINTS = ("data", "info", "details", "extra", "metadata", "payload", "attributes")


def _ref_str(ref) -> str:
    if isinstance(ref, dict):
        return f"{ref.get('table', '')}.{ref.get('column', '')}"
    return ref or ""


def analyze(tables: list[TableSpec]) -> list[dict]:
    """Return a list of {level, table, column, message} warnings.

    level is "warn" (likely problem) or "info" (suggestion)."""
    warnings: list[dict] = []

    for t in tables:
        cols = t.columns
        col_names = {c.name.lower() for c in cols}

        # 1. No primary key
        if not any(c.is_primary_key for c in cols):
            warnings.append({"level": "warn", "code": "no_pk", "table": t.table_name, "column": "",
                             "message": "沒有主鍵（PK），建議至少指定一個主鍵欄位"})

        # 2. No audit timestamps
        if not (col_names & set(_AUDIT_COLS)):
            warnings.append({"level": "info", "code": "missing_audit", "table": t.table_name, "column": "",
                             "message": "缺少 created_at / updated_at 稽核欄位，建議補上"})

        for c in cols:
            name_l = c.name.lower()
            type_l = (c.data_type or "").lower()

            # 3. FK without index → JOIN performance risk
            if c.is_foreign_key and not c.is_indexed and not c.is_primary_key:
                warnings.append({"level": "warn", "code": "fk_no_index", "table": t.table_name, "column": c.name,
                                 "message": "外鍵欄位沒有索引，JOIN 查詢可能變慢，建議建立索引"})

            # 4. Likely-unique field without UNIQUE
            if any(h in name_l for h in _UNIQUE_HINTS) and not c.is_unique and not c.is_primary_key:
                warnings.append({"level": "warn", "code": "likely_unique", "table": t.table_name, "column": c.name,
                                 "message": "看起來是業務唯一值，建議加上 UNIQUE 約束"})

            # 5. Secret field — must be hashed/encrypted
            if any(h in name_l for h in _SECRET_HINTS):
                warnings.append({"level": "warn", "code": "secret_plaintext", "table": t.table_name, "column": c.name,
                                 "message": "疑似敏感資料，務必雜湊或加密儲存，切勿明文"})

            # 6. varchar without length
            if type_l in ("varchar", "character varying") and not c.length:
                warnings.append({"level": "info", "code": "varchar_no_length", "table": t.table_name, "column": c.name,
                                 "message": "varchar 未指定長度，建議明確設定上限"})

            # 7. enum-like field without CHECK constraint
            if any(h in name_l for h in _ENUM_HINTS) and type_l in ("varchar", "text", "character varying"):
                has_check = any("check" in ct.lower() and c.name.lower() in ct.lower()
                                for ct in t.constraints)
                if not has_check:
                    warnings.append({"level": "info", "code": "enum_no_check", "table": t.table_name, "column": c.name,
                                     "message": "狀態/類型欄位建議加 CHECK 約束限制可用值"})

            # 8. plain timestamp (no timezone)
            if type_l == "timestamp":
                warnings.append({"level": "info", "code": "naive_timestamp", "table": t.table_name, "column": c.name,
                                 "message": "建議使用 timestamptz 而非 timestamp 以避免時區問題"})

            # 9. camelCase column name (PostgreSQL folds unquoted identifiers)
            if re.search(r"[a-z][A-Z]", c.name):
                warnings.append({"level": "warn", "table": t.table_name, "column": c.name,
                                 "message": "使用 camelCase 命名，PostgreSQL 慣例為 underscore_case，否則查詢需加引號"})

            # 10. generic blob column — likely under-modelled
            if name_l in _BLOB_HINTS and type_l in ("json", "jsonb", "text"):
                warnings.append({"level": "info", "table": t.table_name, "column": c.name,
                                 "message": "泛用欄位（如 data/metadata）建議拆成具名欄位，較易查詢與索引"})

        # 11. entity table with audit fields but no soft-delete column
        if (col_names & set(_AUDIT_COLS)) and not (col_names & set(_SOFT_DELETE_COLS)) and len(cols) >= 5:
            warnings.append({"level": "info", "table": t.table_name, "column": "",
                             "message": "可考慮加入 deleted_at（軟刪除），避免實刪資料破壞關聯"})

    return warnings
