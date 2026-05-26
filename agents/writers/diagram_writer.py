import json
from pathlib import Path
from models.schema import TableSpec
from utils.client import get_api

_WRITERS_PROMPT = (Path(__file__).parent.parent.parent / "prompts" / "writers.txt").read_text(encoding="utf-8")


class DiagramWriter:
    def generate(self, tables: list[TableSpec]) -> str:
        # other_system_prompt = role definition + task instructions
        system_prompt = (
            _WRITERS_PROMPT + "\n\n"
            "根據提供的資料表規格，產出 Mermaid erDiagram 格式的 ER Diagram。\n"
            "輸出格式：\n"
            "1. 先用一段繁體中文說明各表之間的關聯設計決策\n"
            "2. 然後輸出 Mermaid 程式碼區塊（```mermaid ... ```）\n"
            "Mermaid 只輸出 erDiagram 區塊，不要加其他內容。"
        )

        # other_human_prompt = table spec JSON to process
        human_prompt = json.dumps(
            [
                {
                    "table_name": t.table_name,
                    "columns": [
                        {
                            "name": c.name,
                            "data_type": c.data_type,
                            "is_primary_key": c.is_primary_key,
                            "is_foreign_key": c.is_foreign_key,
                            "references": c.references,
                            "nullable": c.nullable,
                        }
                        for c in t.columns
                    ],
                }
                for t in tables
            ],
            ensure_ascii=False,
            indent=2,
        )

        response = get_api().chat(system_prompt=system_prompt, human_prompt=human_prompt)
        return f"# 結構與關聯圖\n\n{response or ''}\n"
