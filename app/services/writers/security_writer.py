"""SecurityWriter：LLM 產生效能與安全規劃書；先以規則式偵測敏感欄位，提示 LLM 特別說明。"""

from app.llm.provider import LLMProvider
from app.rules.spec_models import TableSpec
from app.services.writers._common import BASE_PROMPT, ask, load_prompt, tables_payload

_TASK_PROMPT = load_prompt("security")

_SENSITIVE_KEYWORDS = {
    "password", "passwd", "email", "phone", "mobile", "id_number",
    "credit_card", "ssn", "token", "secret", "address",
}


def _find_sensitive(tables: list[TableSpec]) -> list[str]:
    return [
        f"{t.table_name}.{c.name}"
        for t in tables
        for c in t.columns
        if any(kw in c.name.lower() for kw in _SENSITIVE_KEYWORDS)
    ]


class SecurityWriter:
    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider

    async def generate(self, tables: list[TableSpec]) -> str:
        sensitive = _find_sensitive(tables)
        sensitive_note = (
            f"偵測到以下可能含有敏感資料的欄位，請特別說明安全處理建議：{sensitive}"
            if sensitive
            else "未偵測到明顯敏感欄位。"
        )
        system_prompt = f"{BASE_PROMPT}\n\n{_TASK_PROMPT.format(sensitive_note=sensitive_note)}"
        human_prompt = tables_payload(tables)
        response = await ask(self._provider, system_prompt, human_prompt)
        return f"# 效能與安全規劃書\n\n{response}\n"
