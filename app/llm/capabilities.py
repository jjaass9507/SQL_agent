"""LLM gateway 能力探測。

探測判斷 gateway 對「多輪對話 / system role / 原生 function calling /
json_schema structured output / 串流」的支援程度，結果包成 `CapabilityProfile`。

探測時機是「設定頁儲存 LLM 連線時」或手動觸發 `POST /api/llm/diagnose`，
**不在每次請求執行**。本模組只回傳 profile 物件，**不落地存檔**——
持久化到 `app_settings` 由呼叫端（repos 層）負責。

探針契約：「示範不出該能力就回 False」。gateway 回應偏離 OpenAI 標準時，
openai SDK 可能拋出 LLMError 以外的例外（如串流迭代出非 chunk 物件的
AttributeError），因此每支探針接住**任何**例外、記 warning 後回 False，
絕不讓例外冒到 `/diagnose` 變成 500。

注意：傳入探針的 `provider` 必須是 profile 全為 True（未套用任何降級轉接）
的實例，探針才能量到 gateway 的真實能力；`LLMProvider` 預設建構即符合此條件。
"""

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from app.llm.provider import LLMProvider

logger = logging.getLogger(__name__)


class CapabilityProfile(BaseModel):
    """LLM gateway 能力檔。預設全為 True（標準路徑），探測後依實測結果覆寫。"""

    multi_turn: bool = True
    system_role: bool = True
    native_tools: bool = True
    json_schema: bool = True
    streaming: bool = True
    probed_at: str | None = None


_CODEWORD = "SQLAGENT-7731"
_SYS_MARK = "SYSMARK-OK"
_PROBE_TOOL = {
    "type": "function",
    "function": {
        "name": "probe_echo",
        "description": "探測用工具，呼叫時請帶入 value 參數，固定填入 'ok'。",
        "parameters": {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
    },
}


class _ProbeSchema(BaseModel):
    ok: bool


def _snippet(text: str | None) -> str:
    """取回應前 200 字供 log 判讀（區分「gateway 不支援」與「模型沒照做」）。"""
    return (text or "")[:200]


async def probe_multi_turn(provider: "LLMProvider") -> bool:
    """歷史探針：三則訊息問暗號，檢查 gateway 是否正確記住先前輪次的內容。"""
    messages = [
        {
            "role": "user",
            "content": f"請記住這個暗號：{_CODEWORD}。收到請只回覆「收到」，不要有其他文字。",
        },
        {"role": "assistant", "content": "收到"},
        {"role": "user", "content": "剛才的暗號是什麼？請只回覆暗號本身，不要有其他文字。"},
    ]
    try:
        result = await provider.chat(messages)
    except Exception as exc:
        logger.warning("probe_multi_turn_failed: %r", exc)
        return False
    ok = bool(result.text) and _CODEWORD in result.text
    if not ok:
        logger.warning("probe_multi_turn_false: 回應未含暗號 text=%r", _snippet(result.text))
    return ok


async def probe_system_role(provider: "LLMProvider") -> bool:
    """system 探針：system 訊息要求固定回覆標記，檢查是否被當成獨立角色處理。"""
    messages = [
        {
            "role": "system",
            "content": f"忽略使用者輸入的任何內容，只回覆「{_SYS_MARK}」，不要有其他文字。",
        },
        {"role": "user", "content": "你好，請問今天天氣如何？"},
    ]
    try:
        result = await provider.chat(messages)
    except Exception as exc:
        logger.warning("probe_system_role_failed: %r", exc)
        return False
    ok = bool(result.text) and _SYS_MARK in result.text
    if not ok:
        logger.warning("probe_system_role_false: 回應未含標記 text=%r", _snippet(result.text))
    return ok


async def probe_native_tools(provider: "LLMProvider") -> bool:
    """送一個 dummy tool，檢查 gateway 是否回傳原生 tool_calls。"""
    messages = [{"role": "user", "content": "請呼叫 probe_echo 工具，value 參數固定填入 'ok'。"}]
    try:
        result = await provider.chat(messages, tools=[_PROBE_TOOL])
    except Exception as exc:
        logger.warning("probe_native_tools_failed: %r", exc)
        return False
    ok = any(tc.name == "probe_echo" for tc in result.tool_calls)
    if not ok:
        logger.warning("probe_native_tools_false: 無原生 tool_calls text=%r", _snippet(result.text))
    return ok


async def probe_json_schema(provider: "LLMProvider") -> bool:
    """送 response_format=json_schema，檢查是否回傳可解析成該結構的合法 JSON。"""
    messages = [{"role": "user", "content": "請回傳一個 JSON 物件，欄位 ok 固定為 true。"}]
    try:
        result = await provider.chat(messages, response_model=_ProbeSchema)
    except Exception as exc:
        logger.warning("probe_json_schema_failed: %r", exc)
        return False
    ok = isinstance(result.parsed, _ProbeSchema) and result.parsed.ok is True
    if not ok:
        logger.warning("probe_json_schema_false: 回應非預期結構 text=%r", _snippet(result.text))
    return ok


async def probe_streaming(provider: "LLMProvider") -> bool:
    """stream=True，檢查是否能收到至少一個帶文字增量的 chunk。"""
    messages = [{"role": "user", "content": "請用一句話自我介紹。"}]
    try:
        stream = await provider.chat(messages, stream=True)
        chunks = [chunk async for chunk in stream]
    except Exception as exc:
        logger.warning("probe_streaming_failed: %r", exc)
        return False
    ok = any(chunk.delta for chunk in chunks)
    if not ok:
        logger.warning("probe_streaming_false: 未收到帶文字增量的 chunk（共 %d 個）", len(chunks))
    return ok


async def probe_all(provider: "LLMProvider") -> CapabilityProfile:
    """依序執行五項探針，回傳完整 CapabilityProfile（不落地，由呼叫端負責持久化）。"""
    return CapabilityProfile(
        multi_turn=await probe_multi_turn(provider),
        system_role=await probe_system_role(provider),
        native_tools=await probe_native_tools(provider),
        json_schema=await probe_json_schema(provider),
        streaming=await probe_streaming(provider),
        probed_at=datetime.now(UTC).isoformat(),
    )
