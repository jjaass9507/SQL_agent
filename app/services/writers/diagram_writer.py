"""DiagramWriter：Mermaid ER 圖確定性產生（保證語法合法）+ LLM 只負責撰寫關聯說明文字。"""

import re

from app.llm.provider import LLMProvider
from app.rules.spec_models import TableSpec
from app.services.writers._common import BASE_PROMPT, ask, load_prompt, tables_payload

_TASK_PROMPT = load_prompt("diagram")
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)


def _safe_ident(s: str) -> str:
    """Mermaid identifier 只能是英數字/底線；其餘字元一律轉底線。"""
    s = re.sub(r"[^0-9A-Za-z_]", "_", (s or "").strip())
    if not s:
        return "unnamed"
    return ("t_" + s) if s[0].isdigit() else s


def _safe_type(data_type: str) -> str:
    """Mermaid 屬性型態只能是單一 token：去掉長度/精度（如 varchar(255) → varchar）。"""
    base = (data_type or "").split("(")[0].strip()
    base = re.sub(r"[^0-9A-Za-z_]", "_", base)
    return base or "string"


def build_mermaid_er(tables: list[TableSpec]) -> str:
    """由 TableSpec 確定性產生合法的 Mermaid erDiagram 語法（不經 LLM）。"""
    names = {t.table_name for t in tables}
    ent = {t.table_name: _safe_ident(t.table_name) for t in tables}
    lines = ["erDiagram"]
    relations: list[str] = []
    for t in tables:
        lines.append(f"    {ent[t.table_name]} {{")
        for c in t.columns:
            key = (
                "PK" if c.is_primary_key else "FK" if c.is_foreign_key else "UK" if c.is_unique
                else ""
            )
            attr = f"        {_safe_type(c.data_type)} {_safe_ident(c.name)}"
            lines.append(f"{attr} {key}" if key else attr)
        lines.append("    }")
        for c in t.columns:
            if c.is_foreign_key and c.references and "." in c.references:
                parent = c.references.split(".")[0].strip()
                if parent in names:
                    relations.append(
                        f'    {ent[parent]} ||--o{{ {ent[t.table_name]} : "{_safe_ident(c.name)}"'
                    )
    lines.extend(relations)
    return "\n".join(lines)


class DiagramWriter:
    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider

    async def generate(self, tables: list[TableSpec]) -> str:
        system_prompt = f"{BASE_PROMPT}\n\n{_TASK_PROMPT}"
        human_prompt = tables_payload(tables)
        response = await ask(self._provider, system_prompt, human_prompt)
        prose = _FENCE_RE.sub("", response).strip()
        diagram = build_mermaid_er(tables)
        return f"# 結構與關聯圖\n\n{prose}\n\n```mermaid\n{diagram}\n```\n"
