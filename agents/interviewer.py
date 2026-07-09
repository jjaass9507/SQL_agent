import json
import re
from pathlib import Path
from models.schema import ColumnSpec, TableSpec
from utils.client import get_api

SYSTEM_PROMPT = (Path(__file__).parent.parent / "prompts" / "interviewer.txt").read_text(encoding="utf-8")
_SPEC_RE = re.compile(r"<TABLE_SPECS>(.*?)</TABLE_SPECS>", re.DOTALL)
_SUMMARY_RE = re.compile(r"<REQUIREMENTS_SUMMARY>(.*?)</REQUIREMENTS_SUMMARY>", re.DOTALL)


def _parse_summary(text: str) -> list[str]:
    match = _SUMMARY_RE.search(text)
    if not match:
        return []
    lines = [l.strip().lstrip("-").strip() for l in match.group(1).strip().splitlines()]
    return [l for l in lines if l]


def _parse_tables(json_str: str) -> list[TableSpec] | None:
    try:
        raw = json.loads(json_str.strip())
    except json.JSONDecodeError:
        print("[Interviewer] TABLE_SPECS JSON 解析失敗。")
        return None

    result = []
    for t in raw:
        columns = [
            ColumnSpec(
                name=c["name"],
                data_type=c.get("data_type", "text"),
                length=c.get("length"),
                nullable=c.get("nullable", True),
                default=c.get("default"),
                description=c.get("description", ""),
                is_primary_key=c.get("is_primary_key", False),
                is_foreign_key=c.get("is_foreign_key", False),
                references=c.get("references"),
                is_unique=c.get("is_unique", False),
                is_indexed=c.get("is_indexed", False),
            )
            for c in t.get("columns", [])
        ]
        result.append(TableSpec(
            table_name=t["table_name"],
            description=t["description"],
            columns=columns,
            constraints=t.get("constraints", []),
            related_tables=t.get("related_tables", []),
        ))
    return result or None


class Interviewer:
    def __init__(self, context: str = "", existing_tables: list[str] | None = None,
                 memory_text: str = "", existing_table_specs: list[TableSpec] | None = None):
        self._api = get_api()
        self._history: list[dict] = []  # {"role": "user"|"assistant", "content": str}
        self._context = context  # designed-schema continuity, injected before SYSTEM_PROMPT
        self._existing_lower = {n.lower() for n in (existing_tables or [])}
        self._memory_text = memory_text  # existing DB structure (txt) injected into system prompt
        # Full TableSpec list (with columns) for convention inference / related-table
        # scoring — existing_tables above only carries names.
        self._existing_specs = existing_table_specs or []
        self._conventions = {}
        if len(self._existing_specs) >= 3:
            from web.convention_checker import infer_conventions
            self._conventions = infer_conventions(self._existing_specs)
        # Once the conversation touches an existing table, keep injecting the
        # existing-DB structure every turn (sticky).
        self._fallback_active = False

    def _conventions_and_relation_text(self, user_message: str) -> str:
        """Conventions summary + top related existing tables for this turn's
        message, so the LLM is nudged to follow existing standards and reuse
        existing tables while designing."""
        lines = []
        if self._conventions:
            c = self._conventions
            lines.append("--- 現有資料庫建立慣例（新設計請盡量遵循）---")
            lines.append(f"命名風格：{c.get('naming_style', '')}")
            if c.get("pk_name"):
                lines.append(f"主鍵慣例：欄位名 {c['pk_name']}，型態多為 {c.get('pk_type', '')}")
            if c.get("timestamp_ratio", 0) >= 0.5:
                lines.append("多數資料表有 created_at 稽核欄位")
            if c.get("soft_delete_ratio", 0) >= 0.5:
                lines.append("多數資料表有軟刪除欄位（如 deleted_at）")
            lines.append("--- 慣例說明結束 ---")
        if self._existing_specs:
            from web.table_relation import find_related
            top = find_related(user_message, None, self._existing_specs).get("related", [])[:3]
            if top:
                lines.append("--- 與此次需求可能相關的現有資料表 ---")
                for r in top:
                    lines.append(f"- {r['table']}（{r.get('reason', '')}）")
                lines.append("--- 相關表說明結束 ---")
        return "\n".join(lines)

    def _mentions_existing(self, text: str) -> bool:
        """True if the text references any existing table name (word-boundary, case-insensitive)."""
        if not self._existing_lower or not text:
            return False
        lowered = text.lower()
        return any(re.search(rf"\b{re.escape(name)}\b", lowered) for name in self._existing_lower)

    def _specs_reference_existing(self, tables: list[TableSpec] | None) -> bool:
        """True if newly designed tables reuse an existing table name or FK-reference one."""
        if not self._existing_lower or not tables:
            return False
        for t in tables:
            if t.table_name.lower() in self._existing_lower:
                return True
            for c in t.columns:
                if c.is_foreign_key and c.references:
                    target = c.references.split(".")[0].strip().lower()
                    if target in self._existing_lower:
                        return True
        return False

    def chat(self, user_message: str) -> tuple[str, list[TableSpec] | None, list[str]]:
        """Send a message. Returns (response_text, tables, summary_points).
        tables and summary_points are non-empty only when requirements are complete."""
        self._history.append({"role": "user", "content": user_message})
        is_first_turn = len(self._history) == 1

        # Existing DB is relevant once the user names an existing table, or it
        # already became relevant on a previous turn (sticky).
        if self._mentions_existing(user_message):
            self._fallback_active = True

        # other_system_prompt = role instructions + conversation history (context)
        history_lines = "\n".join(
            f"[{'使用者' if h['role'] == 'user' else 'AI架構師'}]: {h['content']}"
            for h in self._history[:-1]
        )
        system_prompt = SYSTEM_PROMPT
        # Designed-schema continuity context: first turn only
        if self._context and is_first_turn:
            system_prompt = self._context + "\n\n" + system_prompt
        # Existing-DB structure: inject while relevant (sticky once touched)
        if self._memory_text and self._fallback_active:
            system_prompt = self._memory_text + "\n\n" + system_prompt
            extra = self._conventions_and_relation_text(user_message)
            if extra:
                system_prompt = extra + "\n\n" + system_prompt
        if history_lines:
            system_prompt += f"\n\n--- 對話歷史 ---\n{history_lines}"

        # other_human_prompt = current user message
        response_text = self._api.chat(
            system_prompt=system_prompt,
            human_prompt=user_message,
        )

        if not response_text:
            return "抱歉，無法取得回應，請稍後再試。", None, []

        # Extract structured blocks, then strip them from the displayed text
        spec_match = _SPEC_RE.search(response_text)
        tables = _parse_tables(spec_match.group(1)) if spec_match else None
        summary = _parse_summary(response_text) if spec_match else []

        clean_text = _SUMMARY_RE.sub("", _SPEC_RE.sub("", response_text)).strip()

        self._history.append({"role": "assistant", "content": clean_text})

        # The designed schema may reveal a link to an existing table too.
        if self._specs_reference_existing(tables):
            self._fallback_active = True

        return clean_text, tables, summary
