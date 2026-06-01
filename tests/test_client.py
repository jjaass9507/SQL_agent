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


# ── update_memory (uploadVector) ─────────────────────────

class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_update_memory_no_vector_id():
    # vector_id unset → don't call the API, signal fallback
    api = PensieveAPI(token="t", empno="e", url="http://mock", vector_id="")
    assert api.update_memory("some content") is False


def test_update_memory_success(monkeypatch):
    api = PensieveAPI(token="t", empno="e", url="http://mock", vector_id="vec1")
    captured = {}

    def fake_post(url, data=None, files=None, **kwargs):
        captured.update(url=url, data=data, files=files)
        return _FakeResp({"isSuccess": True, "SuccessFile": ["existing_schema.txt"], "FailFile": []})

    monkeypatch.setattr("utils.client.requests.post", fake_post)
    assert api.update_memory("現有結構") is True
    assert captured["data"] == {"vector_id": "vec1"}
    assert captured["files"]["file"][0] == "existing_schema.txt"  # fixed name → coverage


def test_update_memory_embedding_failed(monkeypatch):
    api = PensieveAPI(token="t", empno="e", url="http://mock", vector_id="vec1")
    monkeypatch.setattr("utils.client.requests.post",
                        lambda *a, **k: _FakeResp({"isSuccess": False, "Result": "boom"}))
    assert api.update_memory("x") is False
