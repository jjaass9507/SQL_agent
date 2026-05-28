import json
from pathlib import Path

from models.schema import TableSpec
from utils.client import get_api

_PROMPT = (Path(__file__).parent.parent / "prompts" / "reviewer.txt").read_text(encoding="utf-8")


class Reviewer:
    def __init__(self):
        self._api = get_api()

    def review(self, tables: list[TableSpec]) -> str:
        """Analyze existing DB schema and return a structured review report (Markdown)."""
        table_data = [
            {
                "table_name": t.table_name,
                "description": t.description,
                "columns": [
                    {
                        "name": c.name,
                        "data_type": c.data_type,
                        "nullable": c.nullable,
                        "is_primary_key": c.is_primary_key,
                        "is_foreign_key": c.is_foreign_key,
                        "references": c.references,
                        "is_unique": c.is_unique,
                        "is_indexed": c.is_indexed,
                        "description": c.description,
                    }
                    for c in t.columns
                ],
            }
            for t in tables
        ]
        human_prompt = (
            f"請審查以下 {len(tables)} 張資料表的結構：\n\n"
            f"```json\n{json.dumps(table_data, ensure_ascii=False, indent=2)}\n```"
        )
        result = self._api.chat(_PROMPT, human_prompt)
        return result or "（審查失敗，請稍後再試）"
