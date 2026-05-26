import json
from pathlib import Path
from models.schema import TableSpec
from utils.client import get_client, MODEL, MAX_TOKENS

SYSTEM_PROMPT = (Path(__file__).parent.parent.parent / "prompts" / "writers.txt").read_text()

SENSITIVE_KEYWORDS = {"password", "passwd", "email", "phone", "mobile", "id_number",
                      "credit_card", "ssn", "token", "secret", "address"}


def _find_sensitive(tables: list[TableSpec]) -> list[str]:
    found = []
    for t in tables:
        for c in t.columns:
            if any(kw in c.name.lower() for kw in SENSITIVE_KEYWORDS):
                found.append(f"{t.table_name}.{c.name}")
    return found


class SecurityWriter:
    def generate(self, tables: list[TableSpec]) -> str:
        sensitive = _find_sensitive(tables)

        spec_json = json.dumps(
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

        sensitive_note = (
            f"偵測到以下可能含有敏感資料的欄位，請特別說明其安全處理建議：{sensitive}"
            if sensitive
            else "未偵測到明顯敏感欄位。"
        )

        prompt = (
            f"根據以下 PostgreSQL 資料表規格，產出效能與安全規劃書。\n"
            f"規格：\n```json\n{spec_json}\n```\n\n"
            f"{sensitive_note}\n\n"
            f"請包含以下章節（使用繁體中文 Markdown）：\n"
            f"1. **索引策略**：建議的複合索引與原因，以及應避免的索引\n"
            f"2. **查詢效能建議**：EXPLAIN ANALYZE 使用建議、潛在的 N+1 問題\n"
            f"3. **分區策略**：如果表預計資料量大，建議的分區欄位與策略\n"
            f"4. **存取控制**：建議的 PostgreSQL role 設計與 GRANT 語句範例\n"
            f"5. **敏感欄位安全**：加密、雜湊、遮罩建議（如 pgcrypto、應用層加密）\n"
            f"6. **備份與維運建議**：重要性評估與備份頻率建議\n"
        )

        response = get_client().messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        return f"# 效能與安全規劃書\n\n{response.content[0].text}\n"
