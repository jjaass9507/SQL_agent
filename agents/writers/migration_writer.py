import json
from pathlib import Path
from models.schema import TableSpec
from utils.client import get_api

_WRITERS_PROMPT = (Path(__file__).parent.parent.parent / "prompts" / "writers.txt").read_text(encoding="utf-8")


class MigrationWriter:
    """Generates an Alembic migration script (upgrade/downgrade) from the schema."""

    def generate(self, tables: list[TableSpec]) -> str:
        system_prompt = (
            _WRITERS_PROMPT + "\n\n"
            "根據提供的資料表規格，產出一份 Alembic migration 腳本（Python）。\n"
            "要求：\n"
            "1. 含 `revision`、`down_revision` 變數（down_revision 設為 None）\n"
            "2. `upgrade()`：用 `op.create_table` 建立所有表，含欄位、主鍵、外鍵、unique、index\n"
            "3. `downgrade()`：用 `op.drop_table` 反向刪除（依相依順序）\n"
            "4. 為外鍵欄位建立 `op.create_index`\n"
            "只輸出 Python 程式碼，不要加 markdown 程式碼區塊標記。"
        )
        human_prompt = json.dumps(
            [
                {
                    "table_name": t.table_name,
                    "columns": [
                        {
                            "name": c.name, "data_type": c.data_type, "length": c.length,
                            "nullable": c.nullable, "default": c.default,
                            "is_primary_key": c.is_primary_key, "is_foreign_key": c.is_foreign_key,
                            "references": c.references, "is_unique": c.is_unique,
                            "is_indexed": c.is_indexed,
                        }
                        for c in t.columns
                    ],
                    "constraints": t.constraints,
                }
                for t in tables
            ],
            ensure_ascii=False, indent=2,
        )
        response = get_api().chat(system_prompt=system_prompt, human_prompt=human_prompt)
        return response or "# （Migration 腳本產出失敗，請稍後再試）\n"
