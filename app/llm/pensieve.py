"""Pensieve 自訂 API adapter。

把 SQL Agent 的標準 chat 介面轉為 Pensieve 的 token/empno/variables envelope，
再把 Result 包回 OpenAI-like response，重用 LLMProvider 的 structured output、
工具文字模擬與非串流降級流程。
"""

import json
import logging
import time
from types import SimpleNamespace
from typing import Any

import httpx

from app.llm.capabilities import CapabilityProfile
from app.llm.errors import LLMError
from app.llm.provider import LLMProvider

logger = logging.getLogger(__name__)

PENSIEVE_PROFILE = CapabilityProfile(
    multi_turn=True,
    system_role=True,
    native_tools=False,
    json_schema=False,
    streaming=False,
)

_RETRY_DELAYS = (2.0, 4.0, 8.0)
_ROLE_LABELS = {
    "user": "[使用者]",
    "assistant": "[助理]",
    "tool": "[工具結果]",
}


class PensieveProvider(LLMProvider):
    """以 Pensieve envelope 實作與 LLMProvider 相同的 async chat 契約。"""

    def __init__(
        self,
        *,
        url: str,
        token: str,
        empno: str,
        building: str = "option",
        verify: bool = False,
        trust_env: bool = True,
        timeout: float = 300.0,
        label: str = "Pensieve",
        profile: CapabilityProfile | None = None,
    ) -> None:
        self.url = url
        self.token = token
        self.empno = empno
        self.building = building
        self.model = label
        self.profile = (profile or PENSIEVE_PROFILE).model_copy()
        self._http = httpx.AsyncClient(verify=verify, trust_env=trust_env, timeout=timeout)

    async def _call(self, **kwargs: Any) -> Any:
        system, human = _to_pensieve_prompts(kwargs.get("messages", []))
        payload = {
            "token": self.token,
            "empno": self.empno,
            "variables": {
                "building": self.building,
                "other_system_prompt": system,
                "other_human_prompt": human,
            },
        }
        for attempt in range(1, len(_RETRY_DELAYS) + 2):
            logger.info(
                "llm_call_begin",
                extra={"attempt": attempt, "model": self.model, "backend": "pensieve"},
            )
            started = time.monotonic()
            try:
                response = await self._http.post(self.url, json=payload)
            except httpx.ProxyError as exc:
                raise LLMError(
                    "Pensieve 連線被 HTTP Proxy 拒絕；請檢查 LLM_TRUST_ENV、"
                    f"NO_PROXY 或 Proxy allowlist：{str(exc)[:200]}"
                ) from exc
            except httpx.HTTPError as exc:
                raise LLMError(f"Pensieve 連線失敗：{exc}") from exc

            if response.status_code in {429, 500, 502, 503, 504} and attempt <= len(
                _RETRY_DELAYS
            ):
                await self._retry_wait(attempt, str(response.status_code))
                continue
            if response.is_error:
                raise LLMError(
                    f"Pensieve API 回傳 HTTP {response.status_code}：{response.text[:300]}"
                )
            try:
                envelope = response.json()
            except ValueError as exc:
                raise LLMError("Pensieve API 回傳內容不是合法 JSON") from exc
            if not envelope.get("isSuccess"):
                detail = envelope.get("message") or envelope.get("Message") or "未提供原因"
                raise LLMError(f"Pensieve API 回傳失敗：{str(detail)[:300]}")
            result = envelope.get("Result")
            if result is None:
                raise LLMError("Pensieve API 回傳成功但缺少 Result")
            content = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
            logger.info(
                "llm_call_done",
                extra={
                    "attempt": attempt,
                    "model": self.model,
                    "backend": "pensieve",
                    "elapsed_s": round(time.monotonic() - started, 3),
                    "usage": None,
                },
            )
            return _openai_like_response(content)
        raise AssertionError("unreachable")


def _to_pensieve_prompts(messages: list[dict[str, Any]]) -> tuple[str, str]:
    system_parts = [str(m.get("content") or "") for m in messages if m.get("role") == "system"]
    conversational = [m for m in messages if m.get("role") != "system"]
    if len(conversational) == 1 and conversational[0].get("role") == "user":
        human = str(conversational[0].get("content") or "")
    else:
        chunks = []
        for message in conversational:
            role = str(message.get("role") or "user")
            content = str(message.get("content") or "")
            tool_calls = message.get("tool_calls") or []
            if tool_calls:
                content = f"{content}\n{json.dumps(tool_calls, ensure_ascii=False)}".strip()
            chunks.append(f"{_ROLE_LABELS.get(role, f'[{role}]')}\n{content}")
        human = "\n\n".join(chunks)
    return "\n\n".join(system_parts), human


def _openai_like_response(content: str) -> SimpleNamespace:
    message = SimpleNamespace(content=content, tool_calls=[])
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice], usage=None)
