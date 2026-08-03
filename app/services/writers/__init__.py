"""需要 LLM 呼叫的 writers（DDL / 圖說明 / 安全規劃 / ORM / migration / 查詢範例 /
增量 migration / Reviewer）。

純模板（零 API）的 writers 仍在 `app/rules/writers/`，這裡不重複實作。

「送 tables JSON、拿一份文字回來」的單發產出（ddl/orm/migration/query）差別只有
prompt 檔名與固定字串，統一由本檔的 `write()` 處理；有額外邏輯的（圖說明的
Mermaid 確定性產生、安全規劃的敏感欄位偵測、增量 migration 的 diff 短路）
才各自獨立成檔。
"""

import logging

from pydantic import BaseModel

from app.llm.errors import LLMError
from app.llm.provider import LLMProvider
from app.rules.spec_models import TableSpec
from app.services.writers._common import BASE_PROMPT, ask, load_prompt, tables_payload

logger = logging.getLogger(__name__)

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


class ReviewReport(BaseModel):
    """審查報告的結構化輸出。四個欄位對應審查頁的四個維度區塊。"""

    score: float
    summary: str
    consistency: list[str]
    integrity: list[str]
    performance: list[str]
    security: list[str]


# (輸出標題, ReviewReport 欄位名)。標題文字是審查頁分段的依據，改動要同步
# app/web/static/js/pages/review.js 的 SECTION_KEYS。
_REVIEW_SECTIONS = [
    ("1. 設計一致性", "consistency"),
    ("2. 資料完整性", "integrity"),
    ("3. 效能考量", "performance"),
    ("4. 安全性", "security"),
]


def render_review_markdown(report: ReviewReport) -> str:
    """把結構化報告轉成 Markdown。

    格式由我們自己決定而不是請 LLM 遵守，所以前端的分段與評分解析不會再因為
    模型某次沒照措辭寫就靜默失效（原本評分欄會空白、內容會落到錯誤的區塊）。
    """
    lines = [
        "# 資料庫結構審查報告",
        "",
        f"**整體評分：{report.score:g}/10**",
        "",
        report.summary.strip(),
        "",
    ]
    for title, field in _REVIEW_SECTIONS:
        lines.append(f"## {title}")
        items = getattr(report, field) or ["未發現明顯問題。"]
        lines.extend(f"- {item}" for item in items)
        lines.append("")
    return "\n".join(lines)


async def review(provider: LLMProvider, tables: list[TableSpec]) -> str:
    """審查模式單發：四維度報告 + 評分。system 只用 reviewer.txt，不套 base。

    走 structured output；gateway 不支援 json_schema 時 `adapters` 會改以 prompt
    注入模擬，仍解析失敗才退回純文字單發（至少產出得了東西，格式則不保證——
    審查頁對此有降級顯示）。
    """
    human_prompt = (
        f"請審查以下 {len(tables)} 張資料表的結構：\n\n```json\n{tables_payload(tables)}\n```"
    )
    system_prompt = load_prompt("reviewer")
    try:
        result = await provider.chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": human_prompt},
            ],
            response_model=ReviewReport,
        )
        return render_review_markdown(result.parsed)
    except LLMError:
        logger.warning("review_structured_output_failed_falling_back_to_text")

    response = await ask(provider, system_prompt, human_prompt)
    return response or "（審查失敗，請稍後再試）"
