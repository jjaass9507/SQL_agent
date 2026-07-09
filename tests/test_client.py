"""Tests for LLMClient — OpenAI-compatible Chat Completions client."""
import pytest

from utils.client import LLMClient, get_api


def _client(**kw):
    defaults = dict(base_url="https://mock.example.com/v1", api_key="k", model="m")
    defaults.update(kw)
    return LLMClient(**defaults)


class _FakeResp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def raise_for_status(self):
        if self.status_code >= 400 and self.status_code != 429:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


# ── request format ───────────────────────────────────────

def test_chat_messages_request_format(monkeypatch):
    captured = {}

    def fake_post(url, json=None, headers=None, **kwargs):
        captured.update(url=url, json=json, headers=headers)
        return _FakeResp(200, {"choices": [{"message": {"content": "hi"}}]})

    monkeypatch.setattr("utils.client.requests.post", fake_post)
    client = _client()
    result = client.chat_messages(
        [{"role": "user", "content": [{"type": "text", "text": "問題"}]}],
        system_prompt="你是助手",
    )

    assert result == "hi"
    assert captured["url"] == "https://mock.example.com/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer k"
    assert captured["headers"]["Content-Type"] == "application/json"
    body = captured["json"]
    assert body["model"] == "m"
    assert body["messages"][0] == {
        "role": "system",
        "content": [{"type": "text", "text": "你是助手"}],
    }
    assert body["messages"][1] == {
        "role": "user",
        "content": [{"type": "text", "text": "問題"}],
    }


def test_chat_messages_without_system_prompt(monkeypatch):
    captured = {}

    def fake_post(url, json=None, headers=None, **kwargs):
        captured.update(json=json)
        return _FakeResp(200, {"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr("utils.client.requests.post", fake_post)
    _client().chat_messages([{"role": "user", "content": [{"type": "text", "text": "x"}]}])
    assert captured["json"]["messages"][0]["role"] == "user"


def test_chat_compat_wraps_chat_messages(monkeypatch):
    captured = {}

    def fake_post(url, json=None, headers=None, **kwargs):
        captured.update(json=json)
        return _FakeResp(200, {"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr("utils.client.requests.post", fake_post)
    result = _client().chat(system_prompt="角色", human_prompt="輸入")
    assert result == "ok"
    body = captured["json"]
    assert body["messages"][0]["content"] == [{"type": "text", "text": "角色"}]
    assert body["messages"][1]["content"] == [{"type": "text", "text": "輸入"}]


# ── response parsing: content as string or parts array ──

def test_response_content_as_string(monkeypatch):
    monkeypatch.setattr(
        "utils.client.requests.post",
        lambda *a, **k: _FakeResp(200, {"choices": [{"message": {"content": "純文字回應"}}]}),
    )
    assert _client().chat_messages([{"role": "user", "content": "x"}]) == "純文字回應"


def test_response_content_as_parts_array(monkeypatch):
    monkeypatch.setattr(
        "utils.client.requests.post",
        lambda *a, **k: _FakeResp(200, {"choices": [{"message": {"content": [
            {"type": "text", "text": "第一段"},
            {"type": "text", "text": "第二段"},
        ]}}]}),
    )
    assert _client().chat_messages([{"role": "user", "content": "x"}]) == "第一段第二段"


def test_response_missing_choices_returns_none(monkeypatch):
    monkeypatch.setattr("utils.client.requests.post", lambda *a, **k: _FakeResp(200, {}))
    assert _client().chat_messages([{"role": "user", "content": "x"}]) is None


# ── 429 retry ─────────────────────────────────────────────

def test_retries_on_429_then_succeeds(monkeypatch):
    calls = []

    def fake_post(url, json=None, headers=None, **kwargs):
        calls.append(1)
        if len(calls) < 3:
            return _FakeResp(429)
        return _FakeResp(200, {"choices": [{"message": {"content": "終於成功"}}]})

    sleeps = []
    monkeypatch.setattr("utils.client.requests.post", fake_post)
    monkeypatch.setattr("utils.client.time.sleep", lambda s: sleeps.append(s))

    result = _client().chat_messages([{"role": "user", "content": "x"}])
    assert result == "終於成功"
    assert len(calls) == 3
    assert sleeps == [2, 4]


def test_retries_exhausted_returns_none(monkeypatch):
    monkeypatch.setattr("utils.client.requests.post", lambda *a, **k: _FakeResp(429))
    monkeypatch.setattr("utils.client.time.sleep", lambda s: None)
    assert _client().chat_messages([{"role": "user", "content": "x"}]) is None


def test_request_exception_returns_none(monkeypatch):
    import requests as real_requests

    def raise_err(*a, **k):
        raise real_requests.exceptions.ConnectionError("boom")

    monkeypatch.setattr("utils.client.requests.post", raise_err)
    assert _client().chat_messages([{"role": "user", "content": "x"}]) is None


# ── get_api() / missing env vars ─────────────────────────

def _reset_singleton(monkeypatch):
    import utils.client as client_mod
    monkeypatch.setattr(client_mod, "_client", None)


def test_get_api_raises_when_base_url_missing(monkeypatch):
    _reset_singleton(monkeypatch)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_MODEL", "m")
    with pytest.raises(RuntimeError, match="LLM_BASE_URL"):
        get_api()


def test_get_api_raises_when_api_key_missing(monkeypatch):
    _reset_singleton(monkeypatch)
    monkeypatch.setenv("LLM_BASE_URL", "https://x/v1")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("LLM_MODEL", "m")
    with pytest.raises(RuntimeError, match="LLM_API_KEY"):
        get_api()


def test_get_api_raises_when_model_missing(monkeypatch):
    _reset_singleton(monkeypatch)
    monkeypatch.setenv("LLM_BASE_URL", "https://x/v1")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    with pytest.raises(RuntimeError, match="LLM_MODEL"):
        get_api()


def test_get_api_returns_singleton(monkeypatch):
    _reset_singleton(monkeypatch)
    monkeypatch.setenv("LLM_BASE_URL", "https://x/v1")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_MODEL", "m")
    first = get_api()
    second = get_api()
    assert first is second
    assert isinstance(first, LLMClient)
