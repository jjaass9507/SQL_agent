import json
import re
from pathlib import Path
from models.schema import TableSpec
from utils.client import get_api

_WRITERS_PROMPT = (Path(__file__).parent.parent.parent / "prompts" / "writers.txt").read_text(encoding="utf-8")

_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)


def _safe_ident(s: str) -> str:
    """Mermaid identifiers must be alphanumeric/underscore; map anything else safely."""
    s = re.sub(r"[^0-9A-Za-z_]", "_", (s or "").strip())
    if not s:
        return "unnamed"
    return ("t_" + s) if s[0].isdigit() else s


def _safe_type(data_type: str) -> str:
    """A Mermaid attribute type must be a single token — drop length/precision
    like varchar(255) → varchar and replace any stray characters."""
    base = (data_type or "").split("(")[0].strip()
    base = re.sub(r"[^0-9A-Za-z_]", "_", base)
    return base or "string"


def build_mermaid_er(tables: list[TableSpec]) -> str:
    """Deterministically render a valid Mermaid erDiagram from the schema.

    Generated rather than LLM-produced so the syntax always parses (Mermaid 10.x
    rejects parentheses in types, unquoted non-ASCII comments, etc.)."""
    names = {t.table_name for t in tables}
    ent = {t.table_name: _safe_ident(t.table_name) for t in tables}
    lines = ["erDiagram"]
    relations: list[str] = []
    for t in tables:
        lines.append(f"    {ent[t.table_name]} {{")
        for c in t.columns:
            key = ("PK" if c.is_primary_key else
                   "FK" if c.is_foreign_key else
                   "UK" if c.is_unique else "")
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
    def generate(self, tables: list[TableSpec]) -> str:
        # The diagram itself is generated deterministically (valid syntax guaranteed);
        # the LLM only writes the prose that explains the relationship design.
        system_prompt = (
            _WRITERS_PROMPT + "\n\n"
            "根據提供的資料表規格，用一段繁體中文（3~5 句）說明各資料表之間的關聯設計決策。\n"
            "只輸出說明文字，不要輸出 Mermaid、程式碼區塊或欄位清單。"
        )
        human_prompt = json.dumps(
            [
                {
                    "table_name": t.table_name,
                    "columns": [
                        {
                            "name": c.name,
                            "data_type": c.data_type,
                            "is_primary_key": c.is_primary_key,
                            "is_foreign_key": c.is_foreign_key,
                            "references": c.references,
                            "nullable": c.nullable,
                        }
                        for c in t.columns
                    ],
                }
                for t in tables
            ],
            ensure_ascii=False,
            indent=2,
        )

        response = get_api().chat(system_prompt=system_prompt, human_prompt=human_prompt)
        # Strip any code/diagram block the model may have added anyway, keep prose only
        prose = _FENCE_RE.sub("", response or "").strip()
        diagram = build_mermaid_er(tables)
        return f"# 結構與關聯圖\n\n{prose}\n\n```mermaid\n{diagram}\n```\n"
