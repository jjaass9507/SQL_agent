import json
from pathlib import Path
from models.schema import TableSpec
from utils.client import get_client, MODEL, MAX_TOKENS

SYSTEM_PROMPT = (Path(__file__).parent.parent.parent / "prompts" / "writers.txt").read_text()


class DDLWriter:
    def generate(self, tables: list[TableSpec]) -> str:
        spec_json = json.dumps(
            [
                {
                    "table_name": t.table_name,
                    "description": t.description,
                    "columns": [
                        {
                            "name": c.name,
                            "data_type": c.data_type,
                            "length": c.length,
                            "nullable": c.nullable,
                            "default": c.default,
                            "description": c.description,
                            "is_primary_key": c.is_primary_key,
                            "is_foreign_key": c.is_foreign_key,
                            "references": c.references,
                            "is_unique": c.is_unique,
                            "is_indexed": c.is_indexed,
                        }
                        for c in t.columns
                    ],
                    "constraints": t.constraints,
                }
                for t in tables
            ],
            ensure_ascii=False,
            indent=2,
        )

        prompt = (
            f"根據以下資料表規格，產出 PostgreSQL DDL 腳本。\n"
            f"規格：\n```json\n{spec_json}\n```\n\n"
            f"請輸出以下內容（用 `-- ===` 分隔區塊）：\n"
            f"1. **建立腳本**：完整的 `CREATE TABLE`，含 PRIMARY KEY、FOREIGN KEY、UNIQUE、CHECK constraints，"
            f"以及每個欄位的 `COMMENT ON COLUMN`\n"
            f"2. **索引建立**：為 is_indexed=true 的欄位建立 `CREATE INDEX`\n"
            f"3. **Migration 腳本**：`CREATE TABLE IF NOT EXISTS` 版本 + 回滾區塊（`DROP TABLE IF EXISTS`）\n"
            f"4. **Seed Data**：每個表各 5 筆範例 `INSERT` 語句\n\n"
            f"只輸出 SQL，不要加 markdown 程式碼區塊標記。"
        )

        response = get_client().messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        sql = response.content[0].text
        header = "-- 資料庫 DDL 腳本（PostgreSQL）\n-- 由 SQL Agent 自動產生\n\n"
        return header + sql
