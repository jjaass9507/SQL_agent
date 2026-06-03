import re
from pathlib import Path
from utils.client import get_api

_PROMPT_TEMPLATE = (Path(__file__).parent.parent / "prompts" / "schema_chat.txt").read_text(encoding="utf-8")
_DDL_TAG_RE = re.compile(r"<DDL_SUGGESTION>(.*?)</DDL_SUGGESTION>", re.DOTALL)


class SchemaChatAgent:
    def __init__(self):
        self._api = get_api()
        self._history: list[dict] = []

    def chat(self, user_message: str, schema_text: str) -> tuple[str, str | None]:
        """Send a message. Returns (display_text, ddl_suggestion_or_None).

        schema_text is passed on every call so the agent always has the
        latest schema (e.g. after the user executes a CREATE TABLE).
        """
        self._history.append({"role": "user", "content": user_message})

        history_lines = "\n".join(
            f"[{'使用者' if h['role'] == 'user' else '助手'}]: {h['content']}"
            for h in self._history[:-1]
        )
        system_prompt = _PROMPT_TEMPLATE.replace(
            "{SCHEMA_TEXT}", schema_text or "（尚無資料表）"
        ).replace(
            "{HISTORY}", history_lines or "（無歷史）"
        )

        response_text = self._api.chat(
            system_prompt=system_prompt,
            human_prompt=user_message,
        )
        if not response_text:
            return "抱歉，無法取得回應，請稍後再試。", None

        ddl_match = _DDL_TAG_RE.search(response_text)
        ddl = ddl_match.group(1).strip() if ddl_match else None
        clean_text = _DDL_TAG_RE.sub("", response_text).strip()

        self._history.append({"role": "assistant", "content": clean_text})
        return clean_text, ddl
