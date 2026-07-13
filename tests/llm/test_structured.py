"""structured.py：response_format 組裝、markdown code fence 剝除、寬鬆解析。"""

from pydantic import BaseModel

from app.llm.structured import build_response_format, parse_lenient, strip_code_fence


class _Draft(BaseModel):
    sql: str
    explanation: str


def test_build_response_format_wraps_pydantic_json_schema():
    fmt = build_response_format(_Draft)
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["name"] == "_Draft"
    assert fmt["json_schema"]["schema"] == _Draft.model_json_schema()


def test_strip_code_fence_removes_json_fence():
    text = '```json\n{"a": 1}\n```'
    assert strip_code_fence(text) == '{"a": 1}'


def test_strip_code_fence_removes_plain_fence():
    text = '```\n{"a": 1}\n```'
    assert strip_code_fence(text) == '{"a": 1}'


def test_strip_code_fence_no_fence_unchanged():
    text = '{"a": 1}'
    assert strip_code_fence(text) == '{"a": 1}'


def test_parse_lenient_success():
    result = parse_lenient('{"sql": "SELECT 1", "explanation": "ok"}', _Draft)
    assert isinstance(result, _Draft)
    assert result.sql == "SELECT 1"


def test_parse_lenient_strips_fence_before_parsing():
    result = parse_lenient('```json\n{"sql": "SELECT 1", "explanation": "ok"}\n```', _Draft)
    assert isinstance(result, _Draft)


def test_parse_lenient_invalid_json_returns_none():
    assert parse_lenient("這不是 JSON", _Draft) is None


def test_parse_lenient_missing_field_returns_none():
    assert parse_lenient('{"sql": "SELECT 1"}', _Draft) is None


def test_parse_lenient_empty_text_returns_none():
    assert parse_lenient("", _Draft) is None
