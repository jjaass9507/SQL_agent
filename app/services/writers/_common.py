"""LLM writers 共用工具：載入 prompt 純文字檔、TableSpec 轉 JSON、組 human prompt、
單發呼叫 LLMProvider。

任務指示一律寫進 human 訊息（見 `human_prompt`），system 只留角色設定——
內網 gateway 常見會忽略或吃掉 system role，指示若只放在 system，模型收到的
就只有一串資料表 JSON，完全不知道要做什麼。
"""

import json
from pathlib import Path

from app.llm.provider import LLMProvider
from app.llm.types import Message
from app.rules.spec_models import TableSpec

_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "llm" / "prompts" / "writers"


def load_prompt(name: str) -> str:
    """讀取 app/llm/prompts/writers/{name}.txt 的純文字內容。"""
    return (_PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8")


BASE_PROMPT = load_prompt("base")


def tables_payload(tables: list[TableSpec]) -> str:
    """把 TableSpec 列表序列化成給 LLM 的 JSON 字串（供 human prompt 使用）。"""
    return json.dumps([t.model_dump() for t in tables], ensure_ascii=False, indent=2)


def human_prompt(task: str, data_label: str, payload: str) -> str:
    """組 human 訊息：先標示資料是什麼、再放資料，任務指示放最後。

    指示放在資料後面，是因為 payload 可能很長（幾十張表的 JSON），
    結尾的指示比開頭的更不容易被模型忽略。
    """
    return f"{data_label}\n\n{payload}\n\n---\n\n{task.strip()}"


def tables_prompt(task: str, tables: list[TableSpec]) -> str:
    """`human_prompt` 的常用情況：資料就是一份 TableSpec 清單。"""
    label = f"以下是 {len(tables)} 張資料表的規格（JSON）："
    return human_prompt(task, label, tables_payload(tables))


async def ask(provider: LLMProvider, system_prompt: str, human_prompt: str) -> str:
    """單發呼叫：組 messages、呼叫 provider.chat()，回傳文字（無回覆時回傳空字串）。"""
    messages: list[Message] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": human_prompt},
    ]
    result = await provider.chat(messages)
    return result.text or ""
