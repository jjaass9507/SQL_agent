"""Heuristic check of new table designs against the naming/structure
conventions inferred (by majority vote) from an existing database.

Pure rule-based analysis (no LLM), zero API cost. Warning shape mirrors
schema_advisor.analyze(): {level, code, table, column, message}.
"""
import re
from collections import Counter

from models.schema import TableSpec

_MIN_SAMPLE = 3
_SOFT_DELETE_COLS = {"deleted_at", "is_deleted", "deleted", "archived_at", "is_archived"}
# Thresholds above which a majority is considered "the convention" worth
# enforcing (avoids flagging designs against a weak/noisy signal).
_NAMING_CONFIDENCE_THRESHOLD = 0.7
_RATIO_THRESHOLD = 0.5
_FK_SUFFIX_THRESHOLD = 0.7


def _style(name: str) -> str:
    """Classify an identifier as 'camel' (has a lower-then-upper transition,
    PostgreSQL's unquoted identifiers are case-folded) or 'snake' (default)."""
    return "camel" if re.search(r"[a-z][A-Z]", name) else "snake"


def infer_conventions(existing_tables: list[TableSpec]) -> dict:
    """Infer naming/structure conventions from existing_tables by majority
    vote. Returns {} when there are fewer than 3 tables (sample too small)."""
    if len(existing_tables) < _MIN_SAMPLE:
        return {}

    names = []
    pk_names = []
    pk_types = []
    fk_col_names = []
    created_at_count = 0
    soft_delete_count = 0

    for t in existing_tables:
        names.append(t.table_name)
        col_names_lower = {c.name.lower() for c in t.columns}
        if "created_at" in col_names_lower:
            created_at_count += 1
        if col_names_lower & _SOFT_DELETE_COLS:
            soft_delete_count += 1
        for c in t.columns:
            names.append(c.name)
            if c.is_primary_key:
                pk_names.append(c.name.lower())
                pk_types.append((c.data_type or "").lower())
            if c.is_foreign_key:
                fk_col_names.append(c.name.lower())

    style_counts = Counter(_style(n) for n in names)
    naming_style, naming_hits = style_counts.most_common(1)[0]
    naming_confidence = naming_hits / sum(style_counts.values())

    n = len(existing_tables)
    fk_suffix_ratio = (
        sum(1 for c in fk_col_names if c.endswith("_id")) / len(fk_col_names)
        if fk_col_names else 0.0
    )

    return {
        "sample_size": n,
        "naming_style": naming_style,
        "naming_confidence": round(naming_confidence, 2),
        "pk_name": Counter(pk_names).most_common(1)[0][0] if pk_names else None,
        "pk_type": Counter(pk_types).most_common(1)[0][0] if pk_types else None,
        "timestamp_ratio": round(created_at_count / n, 2),
        "fk_suffix_ratio": round(fk_suffix_ratio, 2),
        "soft_delete_ratio": round(soft_delete_count / n, 2),
    }


def check_conventions(design_tables: list[TableSpec], conventions: dict) -> list[dict]:
    """Compare design_tables against inferred conventions. Returns warnings
    shaped like schema_advisor's: {level, code, table, column, message}."""
    if not conventions:
        return []

    warnings: list[dict] = []
    naming_style = conventions.get("naming_style")
    naming_confident = conventions.get("naming_confidence", 0) >= _NAMING_CONFIDENCE_THRESHOLD

    for t in design_tables:
        if naming_confident and _style(t.table_name) != naming_style:
            warnings.append({
                "level": "warn", "code": "convention_naming", "table": t.table_name, "column": "",
                "message": f"表名為 {_style(t.table_name)} 風格，與現有資料庫多數採用的 {naming_style} 命名不一致",
            })

        col_names = {c.name.lower() for c in t.columns}

        if conventions.get("timestamp_ratio", 0) >= _RATIO_THRESHOLD and "created_at" not in col_names:
            warnings.append({
                "level": "info", "code": "convention_timestamps", "table": t.table_name, "column": "",
                "message": "現有資料庫多數資料表都有 created_at 稽核欄位，建議補上",
            })

        if conventions.get("soft_delete_ratio", 0) >= _RATIO_THRESHOLD and not (col_names & _SOFT_DELETE_COLS):
            warnings.append({
                "level": "info", "code": "convention_soft_delete", "table": t.table_name, "column": "",
                "message": "現有資料庫多數資料表都有軟刪除欄位（如 deleted_at），建議補上",
            })

        for c in t.columns:
            if naming_confident and _style(c.name) != naming_style:
                warnings.append({
                    "level": "warn", "code": "convention_naming", "table": t.table_name, "column": c.name,
                    "message": f"欄位名為 {_style(c.name)} 風格，與現有資料庫多數採用的 {naming_style} 命名不一致",
                })

            if (c.is_primary_key and conventions.get("pk_type")
                    and (c.data_type or "").lower() != conventions["pk_type"]):
                warnings.append({
                    "level": "info", "code": "convention_pk_type", "table": t.table_name, "column": c.name,
                    "message": f"主鍵型態為 {c.data_type}，與現有資料庫多數採用的 {conventions['pk_type']} 不一致",
                })

            if (c.is_foreign_key and conventions.get("fk_suffix_ratio", 0) >= _FK_SUFFIX_THRESHOLD
                    and not c.name.lower().endswith("_id")):
                warnings.append({
                    "level": "info", "code": "convention_fk_naming", "table": t.table_name, "column": c.name,
                    "message": "現有資料庫的外鍵欄位多以 _id 結尾，建議統一命名",
                })

    return warnings
