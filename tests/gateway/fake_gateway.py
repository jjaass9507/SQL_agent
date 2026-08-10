"""可切換能力的 OpenAI 相容假 gateway（測試用，非正式程式碼）。

真實世界的 gateway 差異就落在這幾件事：支不支援 system role、原生 tool_calls、
`response_format=json_schema`、多輪歷史，以及出錯時回什麼。免費方案尤其常見
「只吃 user/assistant」或「忽略 tools 直接回文字」。

`app/llm/capabilities.py` 的探針就是為了量測這些差異而存在，但平台在探測出結果
之後有沒有真的照著降級，只有把整條路徑對各種 gateway 跑一次才知道——實測就是
這樣抓到「只有 DB Agent 會採用探測結果，訪談與 NL2SQL 直接 500」。

用法見 `tests/gateway/conftest.py` 的 `gateway` fixture。
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# 探針的暗號（與 app/llm/capabilities.py 一致；改那邊要同步改這裡）
CODEWORD = "SQLAGENT-7731"
SYS_MARK = "SYSMARK-OK"

PROFILES = ("full", "no_tools", "no_json", "no_system", "flaky")


def _completion(content=None, tool_calls=None):
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "id": "chatcmpl-fake",
        "object": "chat.completion",
        "created": 0,
        "model": "fake-model",
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 34, "total_tokens": 46},
    }


def _tool_call(name, args):
    return [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
        }
    ]


def _synth(schema):
    """依 json_schema 合成合法物件——真實模型會照 schema 回答，假 gateway 也要。"""
    for key in ("anyOf", "oneOf"):
        if schema.get(key):
            branches = schema[key]
            # 可選欄位（list[X] | None）在 JSON Schema 裡是 anyOf；能給 null 就給 null
            if any(b.get("type") == "null" for b in branches):
                return None
            return _synth(branches[0])
    if "$ref" in schema:
        return {}
    kind = schema.get("type")
    if kind == "object":
        return {k: _synth(v) for k, v in (schema.get("properties") or {}).items()}
    if kind == "array":
        return []
    if kind == "boolean":
        return True
    if kind == "integer":
        return 1
    if kind == "number":
        return 1.0
    if schema.get("enum"):
        return schema["enum"][0]
    return "mock"


class FakeGateway:
    """在背景執行緒跑一個假 gateway；`base_url` 直接餵給 LLM_BASE_URL。"""

    def __init__(self, profile: str = "full") -> None:
        assert profile in PROFILES, profile
        self.profile = profile
        self.requests: list[dict] = []
        self._server = HTTPServer(("127.0.0.1", 0), self._handler_class())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._fail_next = profile == "flaky"  # flaky：第一次必失敗，之後成功（可重現）

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}/v1"

    def start(self) -> "FakeGateway":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    # ── 供斷言使用的統計 ──────────────────────────────────────────────
    @property
    def sent_system_role(self) -> int:
        return sum(
            any(m.get("role") == "system" for m in r.get("messages") or [])
            for r in self.requests
        )

    @property
    def sent_tools(self) -> int:
        return sum(bool(r.get("tools")) for r in self.requests)

    def _reply(self, body: dict) -> dict:
        tools = body.get("tools") or []
        fmt = (body.get("response_format") or {}).get("type")
        text = " ".join(str(m.get("content") or "") for m in body.get("messages") or [])

        # 能力探針：像個稱職的模型一樣回應
        if CODEWORD in text and "暗號是什麼" in text:
            return _completion(CODEWORD)
        if SYS_MARK in text:
            return _completion(SYS_MARK)
        if any(t.get("function", {}).get("name") == "probe_echo" for t in tools):
            if self.profile == "no_tools":
                return _completion('{"tool_call": {"name": "probe_echo", "arguments": {}}}')
            return _completion(None, _tool_call("probe_echo", {"value": "ok"}))
        if fmt and "欄位 ok 固定為 true" in text:
            return _completion('{"ok": true}')

        if fmt in ("json_schema", "json_object"):
            schema = ((body.get("response_format") or {}).get("json_schema") or {}).get(
                "schema"
            ) or {}
            payload = _synth(schema) if schema else {}
            if isinstance(payload, dict) and "sql" in payload:
                payload["sql"] = "SELECT 1"
                payload["explanation"] = "把訂單依通路分組，數每組有幾筆。"
            body_text = json.dumps(payload, ensure_ascii=False)
            if self.profile == "no_json":
                # 弱 gateway 常見：JSON 前後夾解釋文字
                return _completion(f"好的，以下是結果：\n{body_text}\n希望有幫助！")
            return _completion(body_text)

        if tools and self.profile != "no_tools":
            return _completion(None, _tool_call("list_databases", {}))
        return _completion("目前資料庫有 shop 一個。")

    def _handler_class(self):
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("content-length", 0)) or 0)
                body = json.loads(raw or b"{}")
                gateway.requests.append(body)

                if gateway.profile == "flaky" and gateway._fail_next:
                    gateway._fail_next = False
                    return self._json(429, {"error": {"message": "Rate limit exceeded"}})

                if gateway.profile == "no_system" and any(
                    m.get("role") == "system" for m in body.get("messages") or []
                ):
                    return self._json(
                        400, {"error": {"message": "system role is not supported"}}
                    )

                if body.get("stream"):
                    return self._stream(gateway._reply(body))
                self._json(200, gateway._reply(body))

            def do_GET(self):
                self._json(200, {"data": [{"id": "fake-model", "object": "model"}]})

            def _json(self, code, payload):
                data = json.dumps(payload, ensure_ascii=False).encode()
                self.send_response(code)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _stream(self, completion):
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.end_headers()
                content = completion["choices"][0]["message"].get("content") or ""
                for piece in content:
                    chunk = {
                        "id": "x",
                        "object": "chat.completion.chunk",
                        "created": 0,
                        "model": "fake-model",
                        "choices": [
                            {"index": 0, "delta": {"content": piece}, "finish_reason": None}
                        ],
                    }
                    self.wfile.write(
                        f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode()
                    )
                self.wfile.write(b"data: [DONE]\n\n")

            def log_message(self, *args):
                pass

        return Handler
