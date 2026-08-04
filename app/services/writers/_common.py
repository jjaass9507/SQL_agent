"""LLM writers 共用工具：載入 prompt 純文字檔、TableSpec 轉 JSON、組單發 prompt、
呼叫 LLMProvider。

writers 一律**只送一則 user 訊息**（角色設定、資料、任務指示全部寫在裡面），
完全不依賴 system role——內網 gateway 常見會忽略或吃掉 system 訊息，指示放在
那裡等於沒送，模型只會收到一串資料表 JSON，不知道要做什麼。
"""

import json
from pathlib import Path

from app.llm.provider import LLMProvider
from app.rules.spec_models import TableSpec

_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "llm" / "prompts" / "writers"


def load_prompt(name: str) -> str:
    """讀取 app/llm/prompts/writers/{name}.txt 的純文字內容。"""
    return (_PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8")


BASE_PROMPT = load_prompt("base")


def tables_payload(tables: list[TableSpec]) -> str:
    """把 TableSpec 列表序列化成給 LLM 的 JSON 字串（供 prompt 使用）。"""
    return json.dumps([t.model_dump() for t in tables], ensure_ascii=False, indent=2)


def build_prompt(instructions: str, data_label: str, payload: str) -> str:
    """組單發訊息：先標示資料是什麼、再放資料，角色設定與任務指示放最後。

    指示放在資料後面，是因為 payload 可能很長（幾十張表的 JSON），
    結尾的指示比開頭的更不容易被模型忽略。
    """
    return f"{data_label}\n\n{payload}\n\n---\n\n{instructions.strip()}"


def tables_prompt(instructions: str, tables: list[TableSpec]) -> str:
    """`build_prompt` 的常用情況：資料就是一份 TableSpec 清單。"""
    label = f"以下是 {len(tables)} 張資料表的規格（JSON）："
    return build_prompt(instructions, label, tables_payload(tables))


def task_instructions(kind: str) -> str:
    """角色設定（base.txt）+ 任務指示（`{kind}.txt`）。"""
    return f"{BASE_PROMPT}\n\n{load_prompt(kind)}"


async def ask(provider: LLMProvider, prompt: str) -> str:
    """單發呼叫：送出單一則 user 訊息，回傳文字（無回覆時回傳空字串）。"""
    result = await provider.chat([{"role": "user", "content": prompt}])
    return result.text or ""
