"""Deterministic scoring of how a requirement / new table design relates to
an existing database — which existing tables are reusable, which foreign
keys should point at them, and where a new table risks duplicating one.

Pure rule-based analysis (no LLM), zero API cost.
"""
import re

from models.schema import TableSpec

_DUPLICATE_OVERLAP_THRESHOLD = 0.6
_MIN_NAME_LEN = 3


def _normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _name_similar(a: str, b: str) -> bool:
    na, nb = _normalize(a), _normalize(b)
    if len(na) < _MIN_NAME_LEN or len(nb) < _MIN_NAME_LEN:
        return False
    return na in nb or nb in na


def _bigrams(text: str) -> set[str]:
    text = text or ""
    return {text[i:i + 2] for i in range(len(text) - 1)}


def _keyword_hit(requirement_text: str, table: TableSpec) -> bool:
    """True if the requirement text mentions this table by name/column (ASCII
    substring match) or shares a 2-character shingle with its description /
    column descriptions (works for CJK text, which has no word boundaries)."""
    if not requirement_text:
        return False
    req_lower = requirement_text.lower()
    if table.table_name and len(table.table_name) >= _MIN_NAME_LEN and table.table_name.lower() in req_lower:
        return True
    for c in table.columns:
        if c.name and len(c.name) >= _MIN_NAME_LEN and c.name.lower() in req_lower:
            return True
    haystack = " ".join([table.description or ""] + [c.description or "" for c in table.columns])
    return bool(_bigrams(requirement_text) & _bigrams(haystack))


def _match_existing_table(base: str, existing_by_name: dict[str, TableSpec]) -> TableSpec | None:
    """Match a `xxx_id` column's base name against existing table names,
    trying the base itself and simple English pluralisations."""
    for candidate in (base, base + "s", base + "es"):
        if candidate in existing_by_name:
            return existing_by_name[candidate]
    return None


def find_related(requirement_text: str, design_tables: list[TableSpec] | None,
                 existing_tables: list[TableSpec]) -> dict:
    """Score requirement/design tables against an existing DB structure.

    Returns {"related": [{table, reason, score}], "fk_suggestions":
    [{from_table, column, to_table}], "duplicate_risks": [{design_table,
    existing_table, overlap}]}.
    """
    related_scores: dict[str, dict] = {}
    fk_suggestions: list[dict] = []
    duplicate_risks: list[dict] = []

    def _add_related(table_name: str, reason: str, score: float) -> None:
        existing = related_scores.get(table_name)
        if existing is None or score > existing["score"]:
            related_scores[table_name] = {"table": table_name, "reason": reason, "score": score}

    existing_by_name = {t.table_name.lower(): t for t in existing_tables}

    # (d) requirement text mentions an existing table's name/columns/comment
    for et in existing_tables:
        if _keyword_hit(requirement_text, et):
            _add_related(et.table_name, "需求文字提及", 1.0)

    for dt in (design_tables or []):
        # (a) design table name closely resembles an existing table's name
        for et in existing_tables:
            if dt.table_name.lower() == et.table_name.lower():
                continue
            if _name_similar(dt.table_name, et.table_name):
                _add_related(et.table_name, f"與設計表「{dt.table_name}」名稱相近", 0.6)

        # (b) xxx_id column → FK suggestion against an existing table's PK
        for c in dt.columns:
            if not c.name.lower().endswith("_id"):
                continue
            base = c.name[:-3].lower()
            if not base:
                continue
            target = _match_existing_table(base, existing_by_name)
            if target and any(tc.is_primary_key for tc in target.columns):
                fk_suggestions.append({
                    "from_table": dt.table_name, "column": c.name, "to_table": target.table_name,
                })

        # (c) column-name overlap → duplicate-table risk
        design_cols = {c.name.lower() for c in dt.columns}
        if not design_cols:
            continue
        for et in existing_tables:
            existing_cols = {c.name.lower() for c in et.columns}
            if not existing_cols:
                continue
            overlap = len(design_cols & existing_cols) / len(design_cols)
            if overlap > _DUPLICATE_OVERLAP_THRESHOLD:
                duplicate_risks.append({
                    "design_table": dt.table_name, "existing_table": et.table_name,
                    "overlap": round(overlap, 2),
                })

    related = sorted(related_scores.values(), key=lambda r: -r["score"])
    return {"related": related, "fk_suggestions": fk_suggestions, "duplicate_risks": duplicate_risks}
