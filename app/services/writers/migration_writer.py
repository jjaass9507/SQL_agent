"""MigrationWriter：LLM 產生 Alembic migration 腳本（on-demand extra）。"""

from app.llm.provider import LLMProvider
from app.rules.spec_models import TableSpec
from app.services.writers._common import BASE_PROMPT, ask, load_prompt, tables_payload

_TASK_PROMPT = load_prompt("migration")


class MigrationWriter:
    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider

    async def generate(self, tables: list[TableSpec]) -> str:
        system_prompt = f"{BASE_PROMPT}\n\n{_TASK_PROMPT}"
        human_prompt = tables_payload(tables)
        response = await ask(self._provider, system_prompt, human_prompt)
        return response or "# （Migration 腳本產出失敗，請稍後再試）\n"
