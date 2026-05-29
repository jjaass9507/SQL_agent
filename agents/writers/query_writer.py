import json
from pathlib import Path
from models.schema import TableSpec
from utils.client import get_api

_WRITERS_PROMPT = (Path(__file__).parent.parent.parent / "prompts" / "writers.txt").read_text(encoding="utf-8")


class QueryWriter:
    """Generates common example SQL queries for the schema."""

    def generate(self, tables: list[TableSpec]) -> str:
        system_prompt = (
            _WRITERS_PROMPT + "\n\n"
            "根據提供的資料表規格，產出該系統常用的 SQL 查詢範例（PostgreSQL）。\n"
            "請涵蓋：\n"
            "1. 基本 CRUD（INSERT / SELECT / UPDATE / DELETE）各表至少一例\n"
            "2. 跨表 JOIN 查詢（利用外鍵關聯）\n"
            "3. 分頁查詢（LIMIT / OFFSET）\n"
            "4. 統計查詢（COUNT / GROUP BY / 聚合）\n"
            "每段查詢前用 `-- ` 註解說明用途（繁體中文）。\n"
            "只輸出 SQL，不要加 markdown 程式碼區塊標記。"
        )
        human_prompt = json.dumps(
            [
                {
                    "table_name": t.table_name,
                    "columns": [
                        {"name": c.name, "data_type": c.data_type,
                         "is_primary_key": c.is_primary_key, "is_foreign_key": c.is_foreign_key,
                         "references": c.references}
                        for c in t.columns
                    ],
                }
                for t in tables
            ],
            ensure_ascii=False, indent=2,
        )
        response = get_api().chat(system_prompt=system_prompt, human_prompt=human_prompt)
        return response or "-- （查詢範例產出失敗，請稍後再試）\n"
