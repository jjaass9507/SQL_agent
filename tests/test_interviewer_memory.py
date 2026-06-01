"""Tests for the Interviewer's existing-DB memory reference behaviour.

Covers trigger detection ("touched an existing table"), upload-once, and the
system-prompt fallback when the memory API hasn't synced yet. No real API."""
import json

from agents.interviewer import Interviewer
from models.schema import ColumnSpec, TableSpec


class FakeAPI:
    def __init__(self, response: str = "好的，請問主鍵如何設計？", memory_ok: bool = False):
        self.response = response
        self.memory_ok = memory_ok
        self.chat_prompts: list[str] = []
        self.memory_calls: list[str] = []

    def chat(self, system_prompt: str, human_prompt: str):
        self.chat_prompts.append(system_prompt)
        return self.response

    def update_memory(self, content: str) -> bool:
        self.memory_calls.append(content)
        return self.memory_ok


def _make(monkeypatch, *, response="好的", memory_ok=False, memory_synced=False,
          existing=("users", "orders"), memory_text="現有DB結構XYZ") -> tuple[Interviewer, FakeAPI]:
    fake = FakeAPI(response=response, memory_ok=memory_ok)
    monkeypatch.setattr("agents.interviewer.get_api", lambda: fake)
    iv = Interviewer(existing_tables=list(existing), memory_text=memory_text,
                     memory_synced=memory_synced)
    return iv, fake


def _specs_response(table_name="user_logs", references=None) -> str:
    col = {"name": "id", "data_type": "UUID", "is_primary_key": True}
    if references:
        col = {"name": "user_id", "data_type": "UUID", "is_foreign_key": True,
               "references": references}
    spec = [{"table_name": table_name, "description": "x", "columns": [col],
             "constraints": [], "related_tables": []}]
    return f"完成\n<TABLE_SPECS>\n{json.dumps(spec)}\n</TABLE_SPECS>"


# ── trigger detection ────────────────────────────────────

def test_mentions_existing_word_boundary(monkeypatch):
    iv, _ = _make(monkeypatch)
    assert iv._mentions_existing("我要加一張連到 users 的明細表") is True
    assert iv._mentions_existing("加一張 products 表") is False
    assert iv._mentions_existing("usersxyz 不該命中") is False


def test_specs_reference_existing(monkeypatch):
    iv, _ = _make(monkeypatch)
    fk = [TableSpec("logs", "", [ColumnSpec("uid", "UUID", False, "", is_foreign_key=True,
                                            references="users.id")])]
    same = [TableSpec("orders", "", [ColumnSpec("id", "UUID", False, "")])]
    unrelated = [TableSpec("products", "", [ColumnSpec("id", "UUID", False, "")])]
    assert iv._specs_reference_existing(fk) is True
    assert iv._specs_reference_existing(same) is True
    assert iv._specs_reference_existing(unrelated) is False
    assert iv._specs_reference_existing(None) is False


# ── upload-once ──────────────────────────────────────────

def test_memory_uploaded_once(monkeypatch):
    iv, fake = _make(monkeypatch, memory_ok=True)
    iv.chat("我想加一張連到 users 的表")
    assert fake.memory_calls == ["現有DB結構XYZ"]
    assert iv.memory_synced is True
    iv.chat("再補一個欄位")  # still relevant (sticky) but already synced
    assert len(fake.memory_calls) == 1  # not re-uploaded


def test_specs_trigger_upload(monkeypatch):
    iv, fake = _make(monkeypatch, response=_specs_response(references="users.id"),
                     memory_ok=True)
    # User message alone doesn't mention an existing table, but the designed FK does
    iv.chat("幫我設計一張操作紀錄表")
    assert fake.memory_calls == ["現有DB結構XYZ"]
    assert iv.memory_synced is True


# ── fallback injection ───────────────────────────────────

def test_fallback_injects_when_not_synced(monkeypatch):
    iv, fake = _make(monkeypatch, memory_ok=False)
    iv.chat("我要連到 users")
    assert "現有DB結構XYZ" in fake.chat_prompts[0]
    assert iv.memory_synced is False  # upload failed → stays unsynced
    # sticky: next turn still injects even without mentioning the table again
    iv.chat("好")
    assert "現有DB結構XYZ" in fake.chat_prompts[1]


def test_no_injection_when_irrelevant(monkeypatch):
    iv, fake = _make(monkeypatch)
    iv.chat("我要做一個全新的 products 系統")
    assert "現有DB結構XYZ" not in fake.chat_prompts[0]
    assert fake.memory_calls == []
    assert iv.memory_synced is False


def test_synced_skips_injection_and_upload(monkeypatch):
    iv, fake = _make(monkeypatch, memory_synced=True)
    iv.chat("我要連到 users")
    assert "現有DB結構XYZ" not in fake.chat_prompts[0]  # already in API memory
    assert fake.memory_calls == []  # not re-uploaded
