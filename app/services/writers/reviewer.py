"""Reviewer：審查模式的 LLM 單發呼叫——四維度報告 + X/10 評分。"""

from app.llm.provider import LLMProvider
from app.rules.spec_models import TableSpec
from app.services.writers._common import ask, load_prompt, tables_payload

_PROMPT = load_prompt("reviewer")


class Reviewer:
    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider

    async def review(self, tables: list[TableSpec]) -> str:
        human_prompt = (
            f"請審查以下 {len(tables)} 張資料表的結構：\n\n```json\n{tables_payload(tables)}\n```"
        )
        response = await ask(self._provider, _PROMPT, human_prompt)
        return response or "（審查失敗，請稍後再試）"
