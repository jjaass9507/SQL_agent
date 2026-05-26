import json
import re
from pathlib import Path
from models.schema import ColumnSpec, TableSpec
from utils.client import get_api

SYSTEM_PROMPT = (Path(__file__).parent.parent / "prompts" / "interviewer.txt").read_text(encoding="utf-8")
_SPEC_RE = re.compile(r"<TABLE_SPECS>(.*?)</TABLE_SPECS>", re.DOTALL)


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
                data_type=c["data_type"],
                length=c.get("length"),
                nullable=c["nullable"],
                default=c.get("default"),
                description=c["description"],
                is_primary_key=c["is_primary_key"],
                is_foreign_key=c["is_foreign_key"],
                references=c.get("references"),
                is_unique=c["is_unique"],
                is_indexed=c["is_indexed"],
            )
            for c in t["columns"]
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
    def __init__(self, context: str = ""):
        self._api = get_api()
        self._history: list[dict] = []  # {"role": "user"|"assistant", "content": str}
        self._context = context  # existing DB schema injected before SYSTEM_PROMPT

    def chat(self, user_message: str) -> tuple[str, list[TableSpec] | None]:
        """Send a message. Returns (response_text, tables) where tables is
        non-None only when the LLM signals requirements are complete."""
        self._history.append({"role": "user", "content": user_message})
        is_first_turn = len(self._history) == 1

        # other_system_prompt = role instructions + conversation history (context)
        history_lines = "\n".join(
            f"[{'使用者' if h['role'] == 'user' else 'AI架構師'}]: {h['content']}"
            for h in self._history[:-1]
        )
        system_prompt = SYSTEM_PROMPT
        # Inject DB schema context only on the first turn; history carries it implicitly after that
        if self._context and is_first_turn:
            system_prompt = self._context + "\n\n" + system_prompt
        if history_lines:
            system_prompt += f"\n\n--- 對話歷史 ---\n{history_lines}"

        # other_human_prompt = current user message
        response_text = self._api.chat(
            system_prompt=system_prompt,
            human_prompt=user_message,
        )

        if not response_text:
            return "抱歉，無法取得回應，請稍後再試。", None

        # Extract <TABLE_SPECS> block if present
        match = _SPEC_RE.search(response_text)
        tables = _parse_tables(match.group(1)) if match else None
        clean_text = _SPEC_RE.sub("", response_text).strip()

        self._history.append({"role": "assistant", "content": clean_text})
        return clean_text, tables
