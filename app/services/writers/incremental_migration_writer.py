"""IncrementalMigrationWriter：依現有 DB 與設計結構的差異，LLM 產生增量 ALTER migration。

沒有現有 DB 可比對，或比對後沒有差異時直接短路，不呼叫 LLM
（重用 `app.rules.schema_diff.compute_diff` 界定變更範圍）。
"""

import json

from app.llm.provider import LLMProvider
from app.rules.schema_diff import compute_diff
from app.rules.spec_models import TableSpec
from app.services.writers._common import BASE_PROMPT, ask, load_prompt

_TASK_PROMPT = load_prompt("incremental")


class IncrementalMigrationWriter:
    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider

    async def generate(self, designed: list[TableSpec], existing: list[TableSpec]) -> str:
        diff = compute_diff(designed, existing)
        if diff is None:
            return "-- （沒有匯入現有資料庫，無法產生增量 migration）\n"
        if not diff.get("has_changes"):
            return "-- 設計與現有資料庫結構一致，無需任何變更。\n"

        system_prompt = f"{BASE_PROMPT}\n\n{_TASK_PROMPT}"
        human_prompt = json.dumps(
            {
                "diff_summary": diff,
                "existing_schema": [t.model_dump() for t in existing],
                "designed_schema": [t.model_dump() for t in designed],
            },
            ensure_ascii=False,
            indent=2,
        )
        response = await ask(self._provider, system_prompt, human_prompt)
        return response or "-- （增量 migration 產出失敗，請稍後再試）\n"
