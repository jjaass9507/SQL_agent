"""LLM 呼叫層共用的資料型別（純資料、無邏輯）。

獨立成檔是為了避免 provider.py 與 adapters.py／capabilities.py 互相
import 造成循環依賴——本檔案不 import 專案內任何其他模組。
"""

from dataclasses import dataclass, field
from typing import Any

# Chat Completions 訊息與工具定義維持原生 dict 形態，不另包一層型別。
Message = dict[str, Any]
ToolDef = dict[str, Any]


@dataclass
class ToolCall:
    """原生 function calling 或降級轉接器解析出的工具呼叫。"""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class Usage:
    """token 用量。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class ChatResult:
    """`LLMProvider.chat()` 非串流回傳結果。"""

    text: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    parsed: Any | None = None  # 有給 response_model 時為該 Pydantic 模型的實例
    usage: Usage = field(default_factory=Usage)


@dataclass
class ChatChunk:
    """`LLMProvider.chat(stream=True)` 的串流增量。"""

    delta: str | None
    done: bool = False
    usage: Usage | None = None  # 通常只在最後一個 chunk 帶（若 gateway 有回傳）
