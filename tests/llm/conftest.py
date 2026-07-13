"""tests/llm 共用輔助函式：組出符合 OpenAI Chat Completions 格式的 mock 回應
（respx 攔截 HTTP，全程不需要真實 gateway）。"""

import json

import httpx

BASE_URL = "http://mock-gateway.test/v1"


def chat_completion_response(
    *,
    content: str | None = None,
    tool_calls: list[dict] | None = None,
    finish_reason: str = "stop",
    usage: dict | None = None,
) -> httpx.Response:
    """組一個標準（非串流）Chat Completions 回應。"""
    message: dict = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 1,
            "model": "test-model",
            "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
            "usage": usage or {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        },
    )


def tool_call_payload(call_id: str, name: str, arguments: dict) -> dict:
    """組一個 Chat Completions 回應中的 `tool_calls` 陣列元素。"""
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def text_chunk(text: str, *, finish_reason: str | None = None) -> dict:
    """組一個串流文字增量 chunk（`chat.completion.chunk`）。"""
    delta = {"content": text} if text else {}
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "test-model",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }


def usage_only_chunk(usage: dict) -> dict:
    """組一個只帶 usage、沒有 choices 的串流結尾 chunk（`stream_options.include_usage`）。"""
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "test-model",
        "choices": [],
        "usage": usage,
    }


def sse_stream_response(chunks: list[dict]) -> httpx.Response:
    """把多個 chunk 組成一個 `text/event-stream` SSE 回應。"""
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})
