import re
from pathlib import Path
from utils.client import get_api

_PROMPT_TEMPLATE = (Path(__file__).parent.parent / "prompts" / "db_agent.txt").read_text(encoding="utf-8")
# Support optional db="name" attribute: <QUERY db="mydb">...</QUERY>
_QUERY_TAG_RE = re.compile(r'<QUERY(?:\s+db="([^"]*)")?>(.*?)</QUERY>', re.DOTALL)
_DDL_TAG_RE = re.compile(r'<DDL_SUGGESTION(?:\s+db="([^"]*)")?>(.*?)</DDL_SUGGESTION>', re.DOTALL)
_FENCE_RE = re.compile(r"```(?:sql)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def _strip_fence(text: str) -> str:
    m = _FENCE_RE.search(text)
    return m.group(1).strip() if m else text.strip()


class DbAgent:
    """Global conversational agent for one or more named business databases.

    chat() returns (display_text, query, ddl) where query/ddl are either
    None or {"db": str|None, "sql": str}.
    """

    def __init__(self):
        self._api = get_api()
        self._history: list[dict] = []

    def chat(self, user_message: str, schema_text: str) -> tuple[str, dict | None, dict | None]:
        """Send a message.

        Returns (display_text, query_or_None, ddl_or_None).
        query/ddl are {"db": name_or_None, "sql": sql_string} when present.
        schema_text is passed fresh on every call so the agent always has
        the latest structure (e.g. after the user executes a DDL change).
        """
        self._history.append({"role": "user", "content": user_message})

        history_lines = "\n".join(
            f"[{'使用者' if h['role'] == 'user' else '助手'}]: {h['content']}"
            for h in self._history[:-1]
        )
        system_prompt = _PROMPT_TEMPLATE.replace(
            "{SCHEMA_TEXT}", schema_text or "（尚無資料表或未設定業務資料庫）"
        ).replace(
            "{HISTORY}", history_lines or "（無歷史）"
        )

        response_text = self._api.chat(
            system_prompt=system_prompt,
            human_prompt=user_message,
        )
        if not response_text:
            return "抱歉，無法取得回應，請稍後再試。", None, None

        query_match = _QUERY_TAG_RE.search(response_text)
        ddl_match = _DDL_TAG_RE.search(response_text)

        query = None
        if query_match:
            query = {
                "db": query_match.group(1) or None,
                "sql": _strip_fence(query_match.group(2)),
            }

        ddl = None
        if ddl_match:
            ddl = {
                "db": ddl_match.group(1) or None,
                "sql": ddl_match.group(2).strip(),
            }

        clean_text = _DDL_TAG_RE.sub("", _QUERY_TAG_RE.sub("", response_text)).strip()

        self._history.append({"role": "assistant", "content": clean_text})
        return clean_text, query, ddl
