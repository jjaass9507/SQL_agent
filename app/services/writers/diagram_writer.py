"""DiagramWriter：Mermaid ER 圖確定性產生（保證語法合法）+ LLM 只負責撰寫關聯說明文字。"""

import re

from app.llm.provider import LLMProvider
from app.rules.spec_models import TableSpec
from app.services.writers._common import ask, tables_prompt, task_instructions

_TASK_PROMPT = task_instructions("diagram")
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)


def _safe_ident(s: str) -> str:
    """Mermaid identifier 只能是英數字/底線；其餘字元一律轉底線。"""
    s = re.sub(r"[^0-9A-Za-z_]", "_", (s or "").strip())
    if not s:
        return "unnamed"
    return ("t_" + s) if s[0].isdigit() else s


def _entity_name(table_name: str) -> str:
    """實體名稱：中文表名必須加引號，不加引號 mermaid 會直接語法錯誤。

    加引號的實體名可以原樣保留中文（已對 vendored mermaid 10.9 實測）；
    不加引號時 `_safe_ident` 會把每個中文字轉成底線，整張圖變成一堆 `___`，
    對中文命名的資料庫等於不可讀。
    """
    name = (table_name or "").strip()
    if not name:
        return "unnamed"
    return f'"{name}"' if re.search(r"[^0-9A-Za-z_]", name) else name


def _quote_label(s: str) -> str:
    """mermaid 的引號字串沒有跳脫語法，內部的引號只能換掉。"""
    return (s or "").replace('"', "'")


def _safe_type(data_type: str) -> str:
    """Mermaid 屬性型態只能是單一 token：去掉長度/精度（如 varchar(255) → varchar）。"""
    base = (data_type or "").split("(")[0].strip()
    base = re.sub(r"[^0-9A-Za-z_]", "_", base)
    return base or "string"


def build_mermaid_er(tables: list[TableSpec]) -> str:
    """由 TableSpec 確定性產生合法的 Mermaid erDiagram 語法（不經 LLM）。"""
    names = {t.table_name for t in tables}
    ent = {t.table_name: _entity_name(t.table_name) for t in tables}
    lines = ["erDiagram"]
    relations: list[str] = []
    for t in tables:
        lines.append(f"    {ent[t.table_name]} {{")
        for c in t.columns:
            key = (
                "PK" if c.is_primary_key else "FK" if c.is_foreign_key else "UK" if c.is_unique
                else ""
            )
            # 屬性「名稱」不能是中文（引號會被當成註解位置），所以中文欄位名只能
            # 轉成安全識別字，再把原名放進註解欄位，讓看圖的人仍讀得到真實欄位名。
            ident = _safe_ident(c.name)
            attr = f"        {_safe_type(c.data_type)} {ident}"
            if key:
                attr = f"{attr} {key}"
            if ident != (c.name or "").strip():
                attr = f'{attr} "{_quote_label(c.name)}"'
            lines.append(attr)
        lines.append("    }")
        for c in t.columns:
            if c.is_foreign_key and c.references and "." in c.references:
                parent = c.references.split(".")[0].strip()
                if parent in names:
                    relations.append(
                        f'    {ent[parent]} ||--o{{ {ent[t.table_name]} : "{_quote_label(c.name)}"'
                    )
    lines.extend(relations)
    return "\n".join(lines)


class DiagramWriter:
    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider

    async def generate(self, tables: list[TableSpec]) -> str:
        response = await ask(self._provider, tables_prompt(_TASK_PROMPT, tables))
        prose = _FENCE_RE.sub("", response).strip()
        diagram = build_mermaid_er(tables)
        return f"# 結構與關聯圖\n\n{prose}\n\n```mermaid\n{diagram}\n```\n"
