"""structured output 輔助：Pydantic v2 模型 → `response_format` json_schema 組裝，
以及寬鬆解析 fallback（剝除 markdown code fence）。

呼叫 LLM 後的解析失敗自動重試（重新呼叫一次 LLM）由 `provider.py` 負責，
本模組只做「格式組裝」與「單次文字解析」，不觸發任何 LLM 呼叫。
"""

import re

from pydantic import BaseModel, ValidationError

_CODE_FENCE_RE = re.compile(r"^```[a-zA-Z0-9]*\n?|\n?```$")


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
    """寬鬆解析：剝除 markdown code fence 後嘗試以 model 驗證。

    失敗時回傳 None（不拋例外），是否重試由呼叫端（provider.py）決定。
    """
    if not text:
        return None
    candidate = strip_code_fence(text)
    try:
        return model.model_validate_json(candidate)
    except (ValidationError, ValueError):
        return None
