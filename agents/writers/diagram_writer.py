import json
from pathlib import Path
from models.schema import TableSpec
from utils.client import get_client, MODEL, MAX_TOKENS

SYSTEM_PROMPT = (Path(__file__).parent.parent.parent / "prompts" / "writers.txt").read_text()


class DiagramWriter:
    def generate(self, tables: list[TableSpec]) -> str:
        spec_json = json.dumps(
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

        prompt = (
            f"根據以下資料表規格，產出 Mermaid erDiagram 格式的 ER Diagram。\n"
            f"規格如下：\n```json\n{spec_json}\n```\n\n"
            f"輸出格式：\n"
            f"1. 先用一段繁體中文說明各表之間的關聯設計決策\n"
            f"2. 然後輸出 Mermaid 程式碼區塊（```mermaid ... ```）\n"
            f"Mermaid 只輸出 erDiagram 區塊，不要加其他內容。"
        )

        response = get_client().messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        return f"# 結構與關聯圖\n\n{response.content[0].text}\n"
