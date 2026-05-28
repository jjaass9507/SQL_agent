import json
from pathlib import Path
from models.schema import TableSpec
from utils.client import get_api

_WRITERS_PROMPT = (Path(__file__).parent.parent.parent / "prompts" / "writers.txt").read_text(encoding="utf-8")

_SENSITIVE_KEYWORDS = {"password", "passwd", "email", "phone", "mobile", "id_number",
                       "credit_card", "ssn", "token", "secret", "address"}


def _find_sensitive(tables: list[TableSpec]) -> list[str]:
    return [
        f"{t.table_name}.{c.name}"
        for t in tables
        for c in t.columns
        if any(kw in c.name.lower() for kw in _SENSITIVE_KEYWORDS)
    ]


class SecurityWriter:
    def generate(self, tables: list[TableSpec]) -> str:
        sensitive = _find_sensitive(tables)
        sensitive_note = (
            f"偵測到以下可能含有敏感資料的欄位，請特別說明安全處理建議：{sensitive}"
            if sensitive
            else "未偵測到明顯敏感欄位。"
        )

        # other_system_prompt = role definition + task instructions (including sensitive note)
        system_prompt = (
            _WRITERS_PROMPT + "\n\n"
            f"根據提供的 PostgreSQL 資料表規格，產出效能與安全規劃書。\n"
            f"{sensitive_note}\n\n"
            "請包含以下章節（繁體中文 Markdown）：\n"
            "1. **索引策略**：建議的複合索引與原因，以及應避免的索引\n"
            "2. **查詢效能建議**：EXPLAIN ANALYZE 使用建議、潛在的 N+1 問題\n"
            "3. **分區策略**：如果表預計資料量大，建議的分區欄位與策略\n"
            "4. **存取控制**：建議的 PostgreSQL role 設計與 GRANT 語句範例\n"
            "5. **敏感欄位安全**：加密、雜湊、遮罩建議（如 pgcrypto、應用層加密）\n"
            "6. **備份與維運建議**：重要性評估與備份頻率建議\n"
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
                            "is_indexed": c.is_indexed,
                            "is_foreign_key": c.is_foreign_key,
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
        return f"# 效能與安全規劃書\n\n{response or ''}\n"
