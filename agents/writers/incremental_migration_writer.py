import json
from pathlib import Path

from models.schema import TableSpec
from utils.client import get_api
from web.schema_diff import compute_diff

_WRITERS_PROMPT = (Path(__file__).parent.parent.parent / "prompts" / "writers.txt").read_text(encoding="utf-8")


def _table_to_dict(t: TableSpec) -> dict:
    return {
        "table_name": t.table_name,
        "columns": [
            {
                "name": c.name, "data_type": c.data_type, "length": c.length,
                "nullable": c.nullable, "default": c.default,
                "is_primary_key": c.is_primary_key, "is_foreign_key": c.is_foreign_key,
                "references": c.references, "is_unique": c.is_unique, "is_indexed": c.is_indexed,
            }
            for c in t.columns
        ],
        "constraints": t.constraints,
    }


class IncrementalMigrationWriter:
    """Generates an incremental ALTER migration to evolve the existing DB into the designed schema.

    Reuses web.schema_diff.compute_diff to scope changes; short-circuits (no LLM call)
    when there is no existing DB to compare against, or no differences."""

    def generate(self, designed: list[TableSpec], existing: list[TableSpec]) -> str:
        diff = compute_diff(designed, existing)
        if diff is None:
            return "-- （沒有匯入現有資料庫，無法產生增量 migration）\n"
        if not diff.get("has_changes"):
            return "-- 設計與現有資料庫結構一致，無需任何變更。\n"

        system_prompt = (
            _WRITERS_PROMPT + "\n\n"
            "根據「現有資料庫結構」與「目標設計結構」的差異，產出 PostgreSQL 增量 migration 腳本。\n"
            "要求：\n"
            "1. 只針對差異產生變更，不要重建已存在且未變更的資料表。\n"
            "2. 新增的表用 CREATE TABLE；新增欄位用 ALTER TABLE ADD COLUMN；型態或可空性變更用 ALTER COLUMN；新增 UNIQUE / 索引用對應語句。\n"
            "3. 對「現有 DB 有、但設計已移除」的表或欄位，僅以**註解**形式提供 DROP 語句（破壞性，預設註解掉讓人工確認）。\n"
            "4. 每段變更前用 `-- ` 加繁體中文註解說明用途。\n"
            "5. 結尾提供對應的回滾（Rollback）區塊。\n"
            "只輸出 SQL，不要加 markdown 程式碼區塊標記。"
        )
        human_prompt = json.dumps(
            {
                "diff_summary": diff,
                "existing_schema": [_table_to_dict(t) for t in existing],
                "designed_schema": [_table_to_dict(t) for t in designed],
            },
            ensure_ascii=False, indent=2,
        )
        response = get_api().chat(system_prompt=system_prompt, human_prompt=human_prompt)
        return response or "-- （增量 migration 產出失敗，請稍後再試）\n"
