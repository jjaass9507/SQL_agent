"""structured output 輔助：Pydantic v2 模型 → `response_format` json_schema 組裝，
以及寬鬆解析 fallback（剝除 markdown code fence、思考標籤、前後贅字）。

呼叫 LLM 後的解析失敗自動重試（重新呼叫 LLM）由 `provider.py` 負責，
本模組只做「格式組裝」與「單次文字解析」，不觸發任何 LLM 呼叫。
"""

import re
from collections.abc import Iterator

from pydantic import BaseModel, ValidationError

_CODE_FENCE_RE = re.compile(r"^```[a-zA-Z0-9]*\n?|\n?```$")
_THINK_RE = re.compile(r"<(think|thinking|reasoning)>.*?</\1>", re.DOTALL | re.IGNORECASE)
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")


def build_response_format(model: type[BaseModel]) -> dict:
    """把 Pydantic 模型轉成 Chat Completions 的 `response_format`（json_schema 型）。"""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": model.__name__,
            "schema": model.model_json_schema(),
            "strict": False,  # Pydantic 產生的 schema 未必符合各家 strict mode 的限制
        },
    }


def strip_code_fence(text: str) -> str:
    """剝除常見的 ```json ... ``` / ``` ... ``` markdown 包裹。"""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = _CODE_FENCE_RE.sub("", stripped)
    return stripped.strip()


def parse_lenient(text: str, model: type[BaseModel]) -> BaseModel | None:
    """寬鬆解析：依序試各種候選字串，第一個通過 model 驗證的即回傳。

    失敗時回傳 None（不拋例外），是否重試由呼叫端（provider.py）決定。
    """
    for candidate in json_candidates(text or ""):
        try:
            return model.model_validate_json(candidate)
        except (ValidationError, ValueError):
            continue
    return None


def json_candidates(text: str) -> Iterator[str]:
    """由「最可能正確」到「最寬鬆」列出候選 JSON 字串。

    對應實測常見的不合格輸出：推理模型的 `<think>` 區塊、markdown code fence、
    JSON 前後夾帶說明文字（「好的，以下是結果：{...}」）、結尾多餘逗號。

    除了 structured output，`adapters.parse_tool_call_from_text`（native tools
    降級時解析模擬工具呼叫）也共用同一套候選規則。
    """
    body = _THINK_RE.sub("", text).strip()
    if not body:
        return
    yield body
    unfenced = strip_code_fence(body)
    if unfenced != body:
        yield unfenced
    for block in _json_blocks(unfenced):
        if block != unfenced:
            yield block
        repaired = _TRAILING_COMMA_RE.sub(r"\1", block)
        if repaired != block:
            yield repaired


def _json_blocks(text: str) -> Iterator[str]:
    """掃出文字中每一段括號平衡的 JSON 物件／陣列（字串與跳脫感知，不重疊）。"""
    closers = {"{": "}", "[": "]"}
    i = 0
    while i < len(text):
        closer = closers.get(text[i])
        if closer is None:
            i += 1
            continue
        end = _matching_end(text, i, text[i], closer)
        if end is None:
            i += 1
            continue
        yield text[i : end + 1]
        i = end + 1


def _matching_end(text: str, start: int, opener: str, closer: str) -> int | None:
    """回傳 `start` 處括號的配對位置；沒有配對回傳 None。"""
    depth = 0
    in_string = False
    escaped = False
    for j in range(start, len(text)):
        ch = text[j]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return j
    return None
