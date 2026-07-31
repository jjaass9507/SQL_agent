"""需要 LLM 呼叫的 writers（DDL / 圖說明 / 安全規劃 / ORM / migration / 查詢範例 /
增量 migration / Reviewer）。

純模板（零 API）的 writers 仍在 `app/rules/writers/`，這裡不重複實作。

「送 tables JSON、拿一份文字回來」的單發產出（ddl/orm/migration/query）差別只有
prompt 檔名與固定字串，統一由本檔的 `write()` 處理；有額外邏輯的（圖說明的
Mermaid 確定性產生、安全規劃的敏感欄位偵測、增量 migration 的 diff 短路）
才各自獨立成檔。
"""

from app.llm.provider import LLMProvider
from app.rules.spec_models import TableSpec
from app.services.writers._common import BASE_PROMPT, ask, load_prompt, tables_payload

# prompt 檔名 → (輸出前綴, LLM 無回覆時的預設內容)
_SINGLE_SHOT = {
    "ddl": ("-- 資料庫 DDL 腳本（PostgreSQL）\n-- 由 SQL Agent 自動產生\n\n", ""),
    "orm": ("", "# （ORM 模型產出失敗，請稍後再試）\n"),
    "migration": ("", "# （Migration 腳本產出失敗，請稍後再試）\n"),
    "query": ("", "-- （查詢範例產出失敗，請稍後再試）\n"),
}


async def write(provider: LLMProvider, kind: str, tables: list[TableSpec]) -> str:
    """單發 LLM 產出：system = base + `{kind}.txt`，human = TableSpec JSON。"""
    header, fallback = _SINGLE_SHOT[kind]
    system_prompt = f"{BASE_PROMPT}\n\n{load_prompt(kind)}"
    return header + (await ask(provider, system_prompt, tables_payload(tables)) or fallback)


async def review(provider: LLMProvider, tables: list[TableSpec]) -> str:
    """審查模式單發：四維度報告 + X/10 評分。system 只用 reviewer.txt，不套 base。"""
    human_prompt = (
        f"請審查以下 {len(tables)} 張資料表的結構：\n\n```json\n{tables_payload(tables)}\n```"
    )
    response = await ask(provider, load_prompt("reviewer"), human_prompt)
    return response or "（審查失敗，請稍後再試）"
