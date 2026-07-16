"""LLMProvider：封裝官方 `openai` SDK 的 `AsyncOpenAI`，是全專案唯一允許
`import openai` 的模組。統一負責 retry（429/5xx 指數退避）、timeout、
結構化 request log（begin/done、耗時、token 用量）、串流，以及依
`CapabilityProfile` 自動套用降級轉接（`adapters.py`）——呼叫端永遠只寫
標準用法，無感於降級。
"""

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
import openai
from pydantic import BaseModel, ValidationError

from app.config import Settings, get_settings
from app.llm import adapters, structured
from app.llm.adapters import AdaptedRequest
from app.llm.capabilities import CapabilityProfile
from app.llm.errors import LLMError
from app.llm.types import ChatChunk, ChatResult, Message, ToolCall, ToolDef, Usage

logger = logging.getLogger(__name__)

# 429/5xx 重試等待秒數：最多 3 次重試（共 4 次嘗試）。
_RETRY_DELAYS = (2.0, 4.0, 8.0)

_STRUCTURED_RETRY_PROMPT = "上一則回覆不是合法 JSON，請重新只輸出符合格式的 JSON，不要有其他文字。"


def forced_profile(settings: Settings) -> CapabilityProfile | None:
    """解析 `LLM_FORCE_PROFILE`（JSON）為手動覆蓋用的 CapabilityProfile。

    留空 → 回傳 None（走自動偵測）。格式錯誤直接拋 `LLMError`（啟動期即發現，
    不靜默忽略）。未列出的欄位沿用 `CapabilityProfile` 預設（True）。
    """
    raw = settings.llm_force_profile
    if not raw or not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LLMError(f"LLM_FORCE_PROFILE 不是合法 JSON：{exc}") from exc
    try:
        return CapabilityProfile.model_validate(data)
    except ValidationError as exc:
        raise LLMError(f"LLM_FORCE_PROFILE 內容不符合 CapabilityProfile 格式：{exc}") from exc


class LLMProvider:
    """統一出口：所有 LLM 呼叫只能經過本類別。"""

    def __init__(
        self,
        *,
        base_url: str | None,
        api_key: str | None,
        model: str | None,
        verify: bool = True,
        timeout: float = 120.0,
        profile: CapabilityProfile | None = None,
    ) -> None:
        # 自簽憑證 gateway：verify=False 時改用自訂 httpx.AsyncClient(verify=False)
        http_client = None if verify else httpx.AsyncClient(verify=False)
        self._client = openai.AsyncOpenAI(
            base_url=base_url,
            api_key=api_key or "not-set",
            timeout=timeout,
            http_client=http_client,
            max_retries=0,  # 429/5xx 重試自行處理（見 _call），避免與 SDK 內建重試疊加
        )
        self.model = model
        self.profile = profile or CapabilityProfile()

    @classmethod
    def from_settings(
        cls,
        settings: Settings | None = None,
        *,
        profile: CapabilityProfile | None = None,
        apply_force_profile: bool = True,
    ) -> "LLMProvider":
        """timeout / base_url / api_key / model / verify 一律來自 Settings。

        `LLM_FORCE_PROFILE` 非空時，解析出的 profile 優先於傳入的 `profile` 參數
        （含 DB 持久化的探測結果）——這是探針誤判時的手動維運覆蓋。
        探針本身（`apply_force_profile=False`）必須量測未覆蓋的真實能力，故略過。
        """
        settings = settings or get_settings()
        if apply_force_profile:
            forced = forced_profile(settings)
            if forced is not None:
                profile = forced
        return cls(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            verify=settings.llm_verify,
            timeout=settings.llm_timeout,
            profile=profile,
        )

    async def chat(
        self,
        messages: list[Message],
        *,
        tools: list[ToolDef] | None = None,
        response_model: type[BaseModel] | None = None,
        stream: bool = False,
    ) -> ChatResult | AsyncIterator[ChatChunk]:
        adapted = adapters.apply(
            messages,
            tools,
            response_model,
            multi_turn=self.profile.multi_turn,
            system_role=self.profile.system_role,
            native_tools=self.profile.native_tools,
            json_schema=self.profile.json_schema,
        )
        if not stream:
            return await self._chat_once(adapted)
        if not self.profile.streaming:
            result = await self._chat_once(adapted)
            return adapters.single_chunk_stream(result)
        return self._chat_stream(adapted)

    # -- 內部實作 -----------------------------------------------------

    def _build_kwargs(self, adapted: AdaptedRequest) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"model": self.model, "messages": adapted.messages}
        if adapted.api_tools:
            kwargs["tools"] = adapted.api_tools
        if adapted.api_response_model:
            kwargs["response_format"] = structured.build_response_format(adapted.api_response_model)
        return kwargs

    async def _chat_once(self, adapted: AdaptedRequest) -> ChatResult:
        kwargs = self._build_kwargs(adapted)
        resp = await self._call(**kwargs)
        result = self._build_chat_result(resp)

        if adapted.emulate_tools and not result.tool_calls:
            tool_call = adapters.parse_tool_call_from_text(result.text or "")
            if tool_call:
                result.tool_calls = [tool_call]

        target_model = adapted.api_response_model or adapted.emulate_schema
        if target_model:
            result = await self._resolve_structured(kwargs, adapted.messages, result, target_model)
        return result

    async def _resolve_structured(
        self,
        kwargs: dict[str, Any],
        messages: list[Message],
        result: ChatResult,
        target_model: type[BaseModel],
    ) -> ChatResult:
        """寬鬆解析 structured output；失敗時自動重試一次（重新呼叫 LLM）。"""
        parsed = structured.parse_lenient(result.text or "", target_model)
        if parsed is None:
            retry_messages = [
                *messages,
                {"role": "assistant", "content": result.text or ""},
                {"role": "user", "content": _STRUCTURED_RETRY_PROMPT},
            ]
            retry_kwargs = {**kwargs, "messages": retry_messages}
            resp2 = await self._call(**retry_kwargs)
            result = self._build_chat_result(resp2)
            parsed = structured.parse_lenient(result.text or "", target_model)
            if parsed is None:
                raise LLMError("structured output 解析失敗（已自動重試一次仍無法解析為合法 JSON）")
        result.parsed = parsed
        return result

    async def _chat_stream(self, adapted: AdaptedRequest) -> AsyncIterator[ChatChunk]:
        kwargs = self._build_kwargs(adapted)
        kwargs["stream"] = True
        kwargs["stream_options"] = {"include_usage": True}
        stream_resp = await self._call(**kwargs)
        async for event in stream_resp:
            text_delta = None
            done = False
            if event.choices:
                choice = event.choices[0]
                text_delta = choice.delta.content
                done = choice.finish_reason is not None
            usage = None
            if getattr(event, "usage", None):
                usage = Usage(
                    prompt_tokens=event.usage.prompt_tokens,
                    completion_tokens=event.usage.completion_tokens,
                    total_tokens=event.usage.total_tokens,
                )
            if text_delta is None and usage is None and not done:
                continue
            yield ChatChunk(delta=text_delta, done=done or usage is not None, usage=usage)

    async def _call(self, **kwargs: Any) -> Any:
        """送出請求，429/5xx 指數退避重試（2/4/8 秒，最多 3 次重試）；每次呼叫寫結構化 log。"""
        attempt = 0
        while True:
            attempt += 1
            logger.info(
                "llm_call_begin",
                extra={
                    "attempt": attempt,
                    "model": kwargs.get("model"),
                    "n_messages": len(kwargs.get("messages", [])),
                },
            )
            t0 = time.monotonic()
            try:
                resp = await self._client.chat.completions.create(**kwargs)
            except openai.RateLimitError as exc:
                if attempt > len(_RETRY_DELAYS):
                    raise LLMError(f"llm 呼叫遭限流，重試 {attempt - 1} 次後放棄") from exc
                await self._retry_wait(attempt, "429")
                continue
            except openai.APIStatusError as exc:
                if exc.status_code >= 500 and attempt <= len(_RETRY_DELAYS):
                    await self._retry_wait(attempt, str(exc.status_code))
                    continue
                raise LLMError(f"llm gateway 回傳錯誤：{exc}") from exc
            except openai.APIConnectionError as exc:
                logger.error(
                    "llm_call_connection_error", extra={"attempt": attempt, "error": str(exc)}
                )
                raise LLMError(f"llm 連線失敗：{exc}") from exc

            elapsed = time.monotonic() - t0
            logger.info(
                "llm_call_done",
                extra={
                    "attempt": attempt,
                    "elapsed_s": round(elapsed, 3),
                    "usage": _usage_dict(getattr(resp, "usage", None)),
                },
            )
            return resp

    async def _retry_wait(self, attempt: int, reason: str) -> None:
        delay = _RETRY_DELAYS[attempt - 1]
        logger.warning(
            "llm_call_retry", extra={"attempt": attempt, "reason": reason, "delay": delay}
        )
        await asyncio.sleep(delay)

    def _build_chat_result(self, resp: Any) -> ChatResult:
        choice = resp.choices[0]
        message = choice.message
        tool_calls: list[ToolCall] = []
        for tc in message.tool_calls or []:
            try:
                arguments = json.loads(tc.function.arguments) if tc.function.arguments else {}
            except json.JSONDecodeError:
                arguments = {}
            tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=arguments))
        usage_obj = resp.usage
        usage = Usage(
            prompt_tokens=usage_obj.prompt_tokens if usage_obj else 0,
            completion_tokens=usage_obj.completion_tokens if usage_obj else 0,
            total_tokens=usage_obj.total_tokens if usage_obj else 0,
        )
        return ChatResult(text=message.content, tool_calls=tool_calls, parsed=None, usage=usage)


def _usage_dict(usage: Any) -> dict[str, int] | None:
    if usage is None:
        return None
    return {
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "total_tokens": usage.total_tokens,
    }
