import json
from pathlib import Path
from models.schema import TableSpec
from utils.client import get_api

_WRITERS_PROMPT = (Path(__file__).parent.parent.parent / "prompts" / "writers.txt").read_text()


class DDLWriter:
    def generate(self, tables: list[TableSpec]) -> str:
        # other_system_prompt = role definition + task instructions
        system_prompt = (
            _WRITERS_PROMPT + "\n\n"
            "根據提供的資料表規格，產出 PostgreSQL DDL 腳本。\n"
            "請依序輸出以下區塊（用 `-- ===` 分隔）：\n"
            "1. 建立腳本：完整 CREATE TABLE，含 PRIMARY KEY、FOREIGN KEY、UNIQUE、CHECK constraints，"
            "以及每個欄位的 COMMENT ON COLUMN\n"
            "2. 索引建立：為 is_indexed=true 的欄位建立 CREATE INDEX\n"
            "3. Migration 腳本：CREATE TABLE IF NOT EXISTS 版本 + 回滾（DROP TABLE IF EXISTS）\n"
            "4. Seed Data：每個表各 5 筆範例 INSERT 語句\n\n"
            "只輸出 SQL，不要加 markdown 程式碼區塊標記。"
        )

        # other_human_prompt = table spec JSON to process
        human_prompt = json.dumps(
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

        response = get_api().chat(system_prompt=system_prompt, human_prompt=human_prompt)
        header = "-- 資料庫 DDL 腳本（PostgreSQL）\n-- 由 SQL Agent 自動產生\n\n"
        return header + (response or "")
