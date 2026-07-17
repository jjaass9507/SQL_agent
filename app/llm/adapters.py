"""降級轉接層：CapabilityProfile 某項能力缺失時，把「標準用法」轉成 gateway
實際能接受的請求，並把回應轉回標準結果，讓呼叫端（services/agents）永遠
只寫標準用法、無感於降級（見 docs/v2_rebuild_plan.md §4-3）。

五項轉接可獨立疊加，套用順序為：
    system_role → native_tools → json_schema → multi_turn（最後手段）
`multi_turn` 放最後，是因為它會把整段訊息攤平成單一則，其餘轉接注入的
內容（工具目錄、schema 說明）必須先寫進訊息裡，才會一併被攤平進去。
"""

import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass

from pydantic import BaseModel

from app.llm.structured import strip_code_fence
from app.llm.types import ChatChunk, ChatResult, Message, ToolCall, ToolDef

_ROLE_LABELS = {
    "system": "[系統指示]",
    "user": "[使用者]",
    "assistant": "[助理]",
    "tool": "[工具結果]",
}


@dataclass
class AdaptedRequest:
    """`apply()` 的輸出：實際要送給 API 的訊息／參數，以及事後要不要從文字模擬解析。"""

    messages: list[Message]
    api_tools: list[ToolDef] | None
    api_response_model: type[BaseModel] | None
    emulate_tools: bool
    emulate_schema: type[BaseModel] | None


def apply(
    messages: list[Message],
    tools: list[ToolDef] | None,
    response_model: type[BaseModel] | None,
    *,
    multi_turn: bool,
    system_role: bool,
    native_tools: bool,
    json_schema: bool,
) -> AdaptedRequest:
    """依 CapabilityProfile 四項旗標（streaming 由 provider 另外處理）套用降級轉接。"""
    result_messages = [dict(m) for m in messages]

    if not native_tools:
        result_messages = _adapt_native_tool_history(result_messages)

    if not system_role:
        result_messages = _adapt_system_role(result_messages)

    api_tools = tools
    emulate_tools = False
    if tools and not native_tools:
        result_messages = _inject_instruction(result_messages, _build_tool_prompt_injection(tools))
        api_tools = None
        emulate_tools = True

    api_response_model = response_model
    emulate_schema = None
    if response_model and not json_schema:
        result_messages = _inject_instruction(
            result_messages, _build_schema_prompt_injection(response_model)
        )
        api_response_model = None
        emulate_schema = response_model

    if not multi_turn:
        result_messages = _adapt_multi_turn(result_messages)

    return AdaptedRequest(
        messages=result_messages,
        api_tools=api_tools,
        api_response_model=api_response_model,
        emulate_tools=emulate_tools,
        emulate_schema=emulate_schema,
    )


def _adapt_system_role(messages: list[Message]) -> list[Message]:
    """system_role 缺失：system 內容併入第一則 user 訊息開頭。"""
    if not messages or messages[0].get("role") != "system":
        return messages
    system_content = messages[0]["content"]
    rest = messages[1:]
    if rest and rest[0].get("role") == "user":
        merged = dict(rest[0])
        merged["content"] = f"{system_content}\n\n{merged['content']}"
        return [merged, *rest[1:]]
    return [{"role": "user", "content": system_content}, *rest]


def _adapt_native_tool_history(messages: list[Message]) -> list[Message]:
    """native_tools 缺失：把歷史中的原生工具訊息轉成與注入格式一致的純文字。

    否則 `assistant.tool_calls`（content 為 None）與 `role:"tool"` 訊息在攤平/送出時
    會遺失工具呼叫軌跡（content=None 變成字面 "None"），模型第二輪失去 ReAct 脈絡、
    可能不再實際呼叫工具而直接編造答案。轉成文字後 transcript 連貫，多步推理才可靠。
    """
    adapted: list[Message] = []
    for m in messages:
        role = m.get("role")
        if role == "assistant" and m.get("tool_calls"):
            calls = "\n".join(
                f"執行動作》{tc.get('function', {}).get('name', '')}"
                f"｜參數》{tc.get('function', {}).get('arguments', '') or '{}'}"
                for tc in m["tool_calls"]
            )
            text = m.get("content") or calls
            adapted.append({"role": "assistant", "content": text})
        elif role == "tool":
            adapted.append({"role": "user", "content": f"（工具結果）\n{m.get('content', '')}"})
        else:
            adapted.append(m)
    return adapted


def _adapt_multi_turn(messages: list[Message]) -> list[Message]:
    """multi_turn 缺失（最後手段）：整段歷史攤平成單一則 user 訊息。"""
    lines = []
    for m in messages:
        label = _ROLE_LABELS.get(m.get("role"), f"[{m.get('role')}]")
        lines.append(f"{label}\n{m.get('content') or ''}")
    return [{"role": "user", "content": "\n\n".join(lines)}]


def _inject_instruction(messages: list[Message], text: str) -> list[Message]:
    """把額外指示文字塞進訊息：有 system 訊息就附加在後面，否則附加在第一則 user 訊息開頭。"""
    messages = [dict(m) for m in messages]
    if messages and messages[0].get("role") == "system":
        messages[0]["content"] = f"{messages[0]['content']}\n\n{text}"
        return messages
    if messages and messages[0].get("role") == "user":
        messages[0] = {**messages[0], "content": f"{text}\n\n{messages[0]['content']}"}
        return messages
    return [{"role": "user", "content": text}, *messages]


def _build_tool_prompt_injection(tools: list[ToolDef]) -> str:
    """native_tools 缺失：把工具目錄轉成散文，並要求以自訂單行格式輸出工具呼叫。

    刻意**不**使用標準 function-calling 的 JSON 目錄（`[{name,description,parameters}]`）與
    `{"tool_call":...}` 輸出格式：部分 OpenAI 相容平台會偵測到這種格式就主動接手執行工具
    （回報 `No ToolCallback found` 並逾時），破壞本地執行的模擬工具協定。
    改用散文目錄 + 「執行動作》<名稱>｜參數》<JSON>」單行格式，平台認不出來即不攔截
    （解析見 parse_tool_call_from_text）。
    """
    lines = []
    for t in tools:
        func = t.get("function", t)
        params = func.get("parameters", {}) or {}
        props = params.get("properties", {}) or {}
        required = set(params.get("required", []) or [])
        if props:
            param_desc = "；".join(
                f"{name}（{spec.get('type', '值')}，{'必填' if name in required else '選填'}）"
                for name, spec in props.items()
            )
        else:
            param_desc = "無"
        lines.append(f"● {func.get('name')} — {func.get('description', '')}｜參數：{param_desc}")
    catalog = "\n".join(lines)
    return (
        "以下是你可以請外部系統代為執行的資料庫動作（每項為：動作代號 — 說明｜參數）：\n"
        f"{catalog}\n\n"
        "當你需要外部系統代為執行某個動作時，請「只輸出一行純文字」，格式如下：\n"
        "執行動作》<動作代號>｜參數》<一段 JSON 物件>\n"
        '範例：執行動作》get_schema｜參數》{"db": "CIM"}\n'
        "外部系統會讀取這行、代為執行，再把結果以文字貼回給你，你再據此繼續。\n"
        "若不需要外部動作，直接以一般文字回覆使用者。不要使用 markdown code fence。"
    )


def _build_schema_prompt_injection(response_model: type[BaseModel]) -> str:
    """json_schema 缺失：把 schema 說明寫進 prompt，要求輸出符合格式的 JSON。"""
    schema = response_model.model_json_schema()
    return (
        "請只回覆一個符合以下 JSON Schema 的 JSON 物件，不要有其他文字、\n"
        "不要使用 markdown code fence：\n"
        f"{json.dumps(schema, ensure_ascii=False)}"
    )


# 模型輸出的自訂工具呼叫格式：`執行動作》<名稱>｜參數》<JSON>`（見 _build_tool_prompt_injection）。
# 參數 JSON 以貪婪 `\{.*\}` 抓到最後一個 `}`，容許其中含換行（如 DDL）；｜ 亦容許半形 |。
_TOOL_CALL_RE = re.compile(
    r"執行動作》\s*(?P<name>[A-Za-z_]\w*)\s*[｜|]\s*參數》\s*(?P<args>\{.*\})",
    re.DOTALL,
)


def parse_tool_call_from_text(text: str) -> ToolCall | None:
    """從模型輸出的自訂單行工具呼叫格式解析出 ToolCall；解析失敗回傳 None。"""
    if not text:
        return None
    match = _TOOL_CALL_RE.search(strip_code_fence(text))
    if match is None:
        return None
    try:
        arguments = json.loads(match.group("args"))
    except json.JSONDecodeError:
        arguments = {}
    if not isinstance(arguments, dict):
        arguments = {}
    return ToolCall(id="adapter-call-0", name=match.group("name"), arguments=arguments)


async def single_chunk_stream(result: ChatResult) -> AsyncIterator[ChatChunk]:
    """streaming 缺失：非串流呼叫後包成單一 chunk，SSE 端仍照常推一個完整 event。"""
    yield ChatChunk(delta=result.text, done=True, usage=result.usage)
