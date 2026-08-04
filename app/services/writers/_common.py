"""LLM writers 共用工具：載入 prompt 純文字檔、TableSpec 轉 JSON、組單發 prompt、
呼叫 LLMProvider。

writers 一律**只送一則 user 訊息**（角色設定、資料、任務指示全部寫在裡面），
完全不依賴 system role——內網 gateway 常見會忽略或吃掉 system 訊息，指示放在
那裡等於沒送，模型只會收到一串資料表 JSON，不知道要做什麼。
"""

import json
import logging
from pathlib import Path

from app.llm.provider import LLMProvider
from app.llm.structured import json_candidates
from app.rules.spec_models import TableSpec

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "llm" / "prompts" / "writers"

# 資料放中間、指示放前面，結尾再提醒一次。指示只放結尾時，實測弱模型會直接
# 把輸入那份 JSON 複述一遍當成答案（結構與關聯圖、安全規劃書都中過）。
_TAIL_REMINDER = "請依照最前面的指示作答。不要複製、改寫或輸出上面那份 JSON 資料本身。"


def load_prompt(name: str) -> str:
    """讀取 app/llm/prompts/writers/{name}.txt 的純文字內容。"""
    return (_PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8")


BASE_PROMPT = load_prompt("base")


def tables_payload(tables: list[TableSpec]) -> str:
    """把 TableSpec 列表序列化成給 LLM 的 JSON 字串（供 prompt 使用）。"""
    return json.dumps([t.model_dump() for t in tables], ensure_ascii=False, indent=2)


def build_prompt(instructions: str, data_label: str, payload: str) -> str:
    """組單發訊息：指示在前、資料在中、結尾再提醒一次。"""
    return f"{instructions.strip()}\n\n{data_label}\n\n{payload}\n\n---\n{_TAIL_REMINDER}"


def tables_prompt(instructions: str, tables: list[TableSpec]) -> str:
    """`build_prompt` 的常用情況：資料就是一份 TableSpec 清單。"""
    label = f"以下是 {len(tables)} 張資料表的規格（JSON）："
    return build_prompt(instructions, label, tables_payload(tables))


def task_instructions(kind: str) -> str:
    """角色設定（base.txt）+ 任務指示（`{kind}.txt`）。"""
    return f"{BASE_PROMPT}\n\n{load_prompt(kind)}"


async def ask(provider: LLMProvider, prompt: str) -> str:
    """單發呼叫：送出單一則 user 訊息，回傳文字（沒有可用回覆時回傳空字串）。

    模型把輸入那份 JSON 複述回來時視為「沒有回覆」，由各 writer 走自己的
    fallback——沒有任何一個 writer 的產出應該是 JSON（DDL/查詢是 SQL、ORM 與
    migration 是 Python、圖說明與安全規劃是文字），與其把 JSON 原封不動寫進
    文件裡，不如明確標成產出失敗。
    """
    result = await provider.chat([{"role": "user", "content": prompt}])
    text = result.text or ""
    if _is_json_document(text):
        logger.warning("writer_response_looks_like_echoed_json", extra={"length": len(text)})
        return ""
    return text


def _is_json_document(text: str) -> bool:
    """整段回覆是不是一份 JSON 物件／陣列（複述輸入的典型長相）。"""
    stripped = text.strip()
    if not stripped.startswith(("{", "[", "```")):
        return False
    for candidate in json_candidates(stripped):
        try:
            return isinstance(json.loads(candidate), dict | list)
        except json.JSONDecodeError:
            continue
    return False
