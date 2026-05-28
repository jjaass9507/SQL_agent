"""Tests for PensieveAPI response parsing — no HTTP calls needed."""
from utils.client import PensieveAPI

_api = PensieveAPI(token="t", empno="e", url="http://mock")


def test_extract_text_result_string():
    assert _api._extract_text({"Result": "hello"}) == "hello"


def test_extract_text_result_with_escaped_newlines():
    result = _api._extract_text({"Result": "line1\\nline2"})
    assert result == "line1\nline2"


def test_extract_text_result_empty_string():
    assert _api._extract_text({"Result": "   "}) is None


def test_extract_text_list_response():
    result = _api._extract_text(["first item", "second"])
    assert result == "first item"


def test_extract_text_empty_list():
    assert _api._extract_text([]) is None


def test_extract_text_dict_no_result_key():
    result = _api._extract_text({"other": "value"})
    assert result is not None  # falls back to str(res_data)
