"""DDLWriter：LLM 產生 PostgreSQL DDL（建表 + 索引 + migration + seed data）。"""

from app.llm.provider import LLMProvider
from app.rules.spec_models import TableSpec
from app.services.writers._common import BASE_PROMPT, ask, load_prompt, tables_payload

_TASK_PROMPT = load_prompt("ddl")


class DDLWriter:
    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider

    async def generate(self, tables: list[TableSpec]) -> str:
        system_prompt = f"{BASE_PROMPT}\n\n{_TASK_PROMPT}"
        human_prompt = tables_payload(tables)
        response = await ask(self._provider, system_prompt, human_prompt)
        header = "-- 資料庫 DDL 腳本（PostgreSQL）\n-- 由 SQL Agent 自動產生\n\n"
        return header + response
