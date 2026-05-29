import json
from pathlib import Path
from models.schema import TableSpec
from utils.client import get_api

_WRITERS_PROMPT = (Path(__file__).parent.parent.parent / "prompts" / "writers.txt").read_text(encoding="utf-8")


class ORMWriter:
    """Generates SQLAlchemy ORM model classes from the schema."""

    def generate(self, tables: list[TableSpec]) -> str:
        system_prompt = (
            _WRITERS_PROMPT + "\n\n"
            "根據提供的資料表規格，產出 SQLAlchemy 2.0 ORM 模型程式碼（Python）。\n"
            "要求：\n"
            "1. 使用 `DeclarativeBase` 與 `Mapped` / `mapped_column` 語法\n"
            "2. 正確對應型態、nullable、primary_key、unique、index\n"
            "3. 外鍵用 `ForeignKey`，並建立 `relationship`\n"
            "4. 每個 class 前用繁體中文註解說明用途\n"
            "只輸出 Python 程式碼，不要加 markdown 程式碼區塊標記。"
        )
        human_prompt = json.dumps(
            [
                {
                    "table_name": t.table_name,
                    "description": t.description,
                    "columns": [
                        {
                            "name": c.name, "data_type": c.data_type, "length": c.length,
                            "nullable": c.nullable, "is_primary_key": c.is_primary_key,
                            "is_foreign_key": c.is_foreign_key, "references": c.references,
                            "is_unique": c.is_unique, "is_indexed": c.is_indexed,
                        }
                        for c in t.columns
                    ],
                }
                for t in tables
            ],
            ensure_ascii=False, indent=2,
        )
        response = get_api().chat(system_prompt=system_prompt, human_prompt=human_prompt)
        return response or "# （ORM 模型產出失敗，請稍後再試）\n"
