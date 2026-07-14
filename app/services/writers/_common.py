"""LLM writers 共用工具：載入 prompt 純文字檔、TableSpec 轉 JSON、單發呼叫 LLMProvider。"""

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


async def ask(provider: LLMProvider, system_prompt: str, human_prompt: str) -> str:
    """單發呼叫：組 messages、呼叫 provider.chat()，回傳文字（無回覆時回傳空字串）。"""
    messages: list[Message] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": human_prompt},
    ]
    result = await provider.chat(messages)
    return result.text or ""
