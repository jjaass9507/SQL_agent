"""Tests for LLMClient — OpenAI-compatible Chat Completions client."""
import json

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
        self.text = json.dumps(self._payload)

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


# ── base_url normalization（完整 completions 端點 vs v1 base）──────────

def test_base_url_full_completions_endpoint_not_duplicated(monkeypatch):
    captured = {}

    def fake_post(url, json=None, headers=None, **kwargs):
        captured.update(url=url)
        return _FakeResp(200, {"choices": [{"message": {"content": "hi"}}]})

    monkeypatch.setattr("utils.client.requests.post", fake_post)
    client = _client(base_url="https://10.10.23.120:4231/public/kits/openai/v1/chat/completions")
    client.chat_messages([{"role": "user", "content": [{"type": "text", "text": "x"}]}])

    assert captured["url"] == "https://10.10.23.120:4231/public/kits/openai/v1/chat/completions"
    assert "chat/completions/chat/completions" not in captured["url"]


def test_base_url_v1_base_still_works(monkeypatch):
    captured = {}

    def fake_post(url, json=None, headers=None, **kwargs):
        captured.update(url=url)
        return _FakeResp(200, {"choices": [{"message": {"content": "hi"}}]})

    monkeypatch.setattr("utils.client.requests.post", fake_post)
    client = _client(base_url="https://10.10.23.120:4231/public/kits/openai/v1")
    client.chat_messages([{"role": "user", "content": [{"type": "text", "text": "x"}]}])

    assert captured["url"] == "https://10.10.23.120:4231/public/kits/openai/v1/chat/completions"


def test_base_url_full_completions_endpoint_with_trailing_slash(monkeypatch):
    captured = {}

    def fake_post(url, json=None, headers=None, **kwargs):
        captured.update(url=url)
        return _FakeResp(200, {"choices": [{"message": {"content": "hi"}}]})

    monkeypatch.setattr("utils.client.requests.post", fake_post)
    client = _client(base_url="https://10.10.23.120:4231/public/kits/openai/v1/chat/completions/")
    client.chat_messages([{"role": "user", "content": [{"type": "text", "text": "x"}]}])

    assert captured["url"] == "https://10.10.23.120:4231/public/kits/openai/v1/chat/completions"


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


# ── LLM_TIMEOUT env var ──────────────────────────────────

def test_get_api_uses_llm_timeout_env(monkeypatch):
    _reset_singleton(monkeypatch)
    monkeypatch.setenv("LLM_BASE_URL", "https://x/v1")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_MODEL", "m")
    monkeypatch.setenv("LLM_TIMEOUT", "45")
    assert get_api().timeout == (10, 45)


def test_get_api_defaults_timeout_when_unset(monkeypatch):
    _reset_singleton(monkeypatch)
    monkeypatch.setenv("LLM_BASE_URL", "https://x/v1")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_MODEL", "m")
    monkeypatch.delenv("LLM_TIMEOUT", raising=False)
    assert get_api().timeout == (10, 120)


def test_get_api_falls_back_to_default_on_invalid_llm_timeout(monkeypatch):
    _reset_singleton(monkeypatch)
    monkeypatch.setenv("LLM_BASE_URL", "https://x/v1")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_MODEL", "m")
    monkeypatch.setenv("LLM_TIMEOUT", "abc")
    assert get_api().timeout == (10, 120)


# ── LLM_SYSTEM_MODE env var ──────────────────────────────

def test_get_api_defaults_system_mode_when_unset(monkeypatch):
    _reset_singleton(monkeypatch)
    monkeypatch.setenv("LLM_BASE_URL", "https://x/v1")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_MODEL", "m")
    monkeypatch.delenv("LLM_SYSTEM_MODE", raising=False)
    assert get_api().system_mode == "system"


def test_get_api_uses_llm_system_mode_env(monkeypatch):
    _reset_singleton(monkeypatch)
    monkeypatch.setenv("LLM_BASE_URL", "https://x/v1")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_MODEL", "m")
    monkeypatch.setenv("LLM_SYSTEM_MODE", "inline")
    assert get_api().system_mode == "inline"


def test_get_api_falls_back_to_default_on_invalid_llm_system_mode(monkeypatch):
    _reset_singleton(monkeypatch)
    monkeypatch.setenv("LLM_BASE_URL", "https://x/v1")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_MODEL", "m")
    monkeypatch.setenv("LLM_SYSTEM_MODE", "bogus")
    assert get_api().system_mode == "system"


# ── connection diagnostics（proxy 繞過 / connect timeout / ping）──────────

def test_post_bypasses_proxies_and_uses_connect_timeout(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured.update(kwargs)
        return _FakeResp(200, {"choices": [{"message": {"content": "hi"}}]})

    monkeypatch.setattr("utils.client.requests.post", fake_post)
    _client().chat_messages([{"role": "user", "content": [{"type": "text", "text": "x"}]}])
    assert captured["proxies"] == {"http": None, "https": None}
    assert captured["timeout"] == (10, 120)  # (connect, read)


def test_ping_success(monkeypatch):
    monkeypatch.setattr(
        "utils.client.requests.post",
        lambda url, **kw: _FakeResp(200, {"choices": [{"message": {"content": "pong"}}]}),
    )
    result = _client().ping()
    assert result == {"ok": True, "model": "m"}


def test_ping_connection_error(monkeypatch):
    import requests

    def fake_post(url, **kwargs):
        raise requests.exceptions.ConnectionError("connection refused")

    monkeypatch.setattr("utils.client.requests.post", fake_post)
    result = _client().ping()
    assert result["ok"] is False
    assert "ConnectionError" in result["error"]
    assert result["url"].endswith("/chat/completions")


def test_ping_http_error_includes_body(monkeypatch):
    resp = _FakeResp(401, {})
    resp.text = '{"error": "invalid api key"}'
    monkeypatch.setattr("utils.client.requests.post", lambda url, **kw: resp)
    result = _client().ping()
    assert result["ok"] is False
    assert result["status_code"] == 401
    assert "invalid api key" in result["error"]


def test_ping_unexpected_shape(monkeypatch):
    monkeypatch.setattr(
        "utils.client.requests.post",
        lambda url, **kw: _FakeResp(200, {"unexpected": True}),
    )
    result = _client().ping()
    assert result["ok"] is False
    assert "choices" in result["error"]


# ── system_mode="inline" (system prompt merged into first user message) ──

def test_inline_mode_no_system_message_and_prefixes_user_text(monkeypatch):
    captured = {}

    def fake_post(url, json=None, headers=None, **kwargs):
        captured.update(json=json)
        return _FakeResp(200, {"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr("utils.client.requests.post", fake_post)
    client = _client(system_mode="inline")
    client.chat_messages(
        [{"role": "user", "content": [{"type": "text", "text": "問題"}]}],
        system_prompt="你是助手",
    )

    messages = captured["json"]["messages"]
    assert all(m["role"] != "system" for m in messages)
    assert len(messages) == 1
    text = messages[0]["content"][0]["text"]
    assert text.startswith("【角色指令】\n你是助手")
    assert "【輸入】\n問題" in text


def test_inline_mode_does_not_mutate_caller_messages(monkeypatch):
    monkeypatch.setattr(
        "utils.client.requests.post",
        lambda *a, **k: _FakeResp(200, {"choices": [{"message": {"content": "ok"}}]}),
    )
    original = [{"role": "user", "content": [{"type": "text", "text": "問題"}]}]
    client = _client(system_mode="inline")
    client.chat_messages(original, system_prompt="你是助手")

    assert original[0]["content"][0]["text"] == "問題"


def test_inline_mode_without_system_prompt_unchanged(monkeypatch):
    captured = {}

    def fake_post(url, json=None, headers=None, **kwargs):
        captured.update(json=json)
        return _FakeResp(200, {"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr("utils.client.requests.post", fake_post)
    client = _client(system_mode="inline")
    client.chat_messages([{"role": "user", "content": [{"type": "text", "text": "x"}]}])
    assert captured["json"]["messages"][0]["content"][0]["text"] == "x"


# ── probe_system_prompt ───────────────────────────────────

def test_probe_system_prompt_honored(monkeypatch):
    monkeypatch.setattr(
        "utils.client.requests.post",
        lambda url, **kw: _FakeResp(200, {"choices": [{"message": {"content": "SYSMARK_OK"}}]}),
    )
    result = _client().probe_system_prompt()
    assert result == {"honored": True, "reply": "SYSMARK_OK"}


def test_probe_system_prompt_not_honored(monkeypatch):
    monkeypatch.setattr(
        "utils.client.requests.post",
        lambda url, **kw: _FakeResp(200, {"choices": [{"message": {"content": "我不知道你在說什麼"}}]}),
    )
    result = _client().probe_system_prompt()
    assert result["honored"] is False
    assert result["reply"] == "我不知道你在說什麼"


def test_probe_system_prompt_request_exception(monkeypatch):
    import requests as real_requests

    def raise_err(*a, **k):
        raise real_requests.exceptions.ConnectionError("boom")

    monkeypatch.setattr("utils.client.requests.post", raise_err)
    result = _client().probe_system_prompt()
    assert result["honored"] is None
    assert "ConnectionError" in result["error"]


def test_probe_system_prompt_uses_inline_mode_when_configured(monkeypatch):
    captured = {}

    def fake_post(url, json=None, headers=None, **kwargs):
        captured.update(json=json)
        return _FakeResp(200, {"choices": [{"message": {"content": "SYSMARK_OK"}}]})

    monkeypatch.setattr("utils.client.requests.post", fake_post)
    _client(system_mode="inline").probe_system_prompt()
    messages = captured["json"]["messages"]
    assert all(m["role"] != "system" for m in messages)
    assert "SYSMARK_OK" in messages[0]["content"][0]["text"]


# ── LLM_CONTENT_FORMAT: string vs parts ──────────────────

def test_content_format_string_sends_plain_string_content(monkeypatch):
    captured = {}

    def fake_post(url, json=None, headers=None, **kwargs):
        captured.update(json=json)
        return _FakeResp(200, {"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr("utils.client.requests.post", fake_post)
    client = _client(content_format="string")
    client.chat_messages(
        [{"role": "user", "content": [{"type": "text", "text": "問題"}]}],
        system_prompt="你是助手",
    )
    messages = captured["json"]["messages"]
    assert messages[0] == {"role": "system", "content": "你是助手"}
    assert messages[1] == {"role": "user", "content": "問題"}


def test_content_format_parts_default_sends_parts_array(monkeypatch):
    captured = {}

    def fake_post(url, json=None, headers=None, **kwargs):
        captured.update(json=json)
        return _FakeResp(200, {"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr("utils.client.requests.post", fake_post)
    client = _client()
    client.chat_messages(
        [{"role": "user", "content": "問題"}],
        system_prompt="你是助手",
    )
    messages = captured["json"]["messages"]
    assert messages[0] == {"role": "system", "content": [{"type": "text", "text": "你是助手"}]}
    assert messages[1] == {"role": "user", "content": [{"type": "text", "text": "問題"}]}


def test_content_format_string_does_not_mutate_caller_messages(monkeypatch):
    monkeypatch.setattr(
        "utils.client.requests.post",
        lambda *a, **k: _FakeResp(200, {"choices": [{"message": {"content": "ok"}}]}),
    )
    original = [{"role": "user", "content": [{"type": "text", "text": "問題"}]}]
    client = _client(content_format="string")
    client.chat_messages(original)
    assert original[0]["content"] == [{"type": "text", "text": "問題"}]


# ── LLM_SYSTEM_MODE="single_turn" (flatten to one user message) ──

def test_single_turn_flattens_multi_message_history(monkeypatch):
    captured = {}

    def fake_post(url, json=None, headers=None, **kwargs):
        captured.update(json=json)
        return _FakeResp(200, {"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr("utils.client.requests.post", fake_post)
    client = _client(system_mode="single_turn")
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "第一句"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "回覆"}]},
        {"role": "assistant", "content": [{"type": "text", "text": '<TOOL name="x">{}</TOOL>'}]},
        {"role": "user", "content": [{"type": "text", "text": '<OBSERVATION tool="x">ok</OBSERVATION>'}]},
        {"role": "user", "content": [{"type": "text", "text": "本次輸入"}]},
    ]
    client.chat_messages(messages, system_prompt="你是助手")

    sent = captured["json"]["messages"]
    assert len(sent) == 1
    assert sent[0]["role"] == "user"
    text = sent[0]["content"][0]["text"]
    assert "【角色指令】\n你是助手" in text
    assert "【對話歷史】" in text
    assert "【本次輸入】\n本次輸入" in text
    assert "[使用者]: 第一句" in text
    assert "[助手]: 回覆" in text
    assert '<TOOL name="x">{}</TOOL>' in text
    assert '<OBSERVATION tool="x">ok</OBSERVATION>' in text


def test_single_turn_single_message_omits_history_section(monkeypatch):
    captured = {}

    def fake_post(url, json=None, headers=None, **kwargs):
        captured.update(json=json)
        return _FakeResp(200, {"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr("utils.client.requests.post", fake_post)
    client = _client(system_mode="single_turn")
    client.chat(system_prompt="角色", human_prompt="輸入")

    sent = captured["json"]["messages"]
    assert len(sent) == 1
    text = sent[0]["content"][0]["text"]
    assert "【對話歷史】" not in text
    assert "【角色指令】\n角色" in text
    assert "【本次輸入】\n輸入" in text


def test_single_turn_does_not_mutate_caller_messages(monkeypatch):
    monkeypatch.setattr(
        "utils.client.requests.post",
        lambda *a, **k: _FakeResp(200, {"choices": [{"message": {"content": "ok"}}]}),
    )
    original = [
        {"role": "user", "content": [{"type": "text", "text": "第一句"}]},
        {"role": "user", "content": [{"type": "text", "text": "本次輸入"}]},
    ]
    client = _client(system_mode="single_turn")
    client.chat_messages(original, system_prompt="角色")
    assert original[0]["content"][0]["text"] == "第一句"
    assert original[1]["content"][0]["text"] == "本次輸入"


# ── probe_history ─────────────────────────────────────────

def test_probe_history_honored(monkeypatch):
    monkeypatch.setattr(
        "utils.client.requests.post",
        lambda url, **kw: _FakeResp(200, {"choices": [{"message": {"content": "你的暗號是 SYNC42"}}]}),
    )
    result = _client().probe_history()
    assert result["honored"] is True


def test_probe_history_not_honored(monkeypatch):
    monkeypatch.setattr(
        "utils.client.requests.post",
        lambda url, **kw: _FakeResp(200, {"choices": [{"message": {"content": "我不知道"}}]}),
    )
    result = _client().probe_history()
    assert result["honored"] is False


def test_probe_history_request_exception(monkeypatch):
    import requests as real_requests

    def raise_err(*a, **k):
        raise real_requests.exceptions.ConnectionError("boom")

    monkeypatch.setattr("utils.client.requests.post", raise_err)
    result = _client().probe_history()
    assert result["honored"] is None
    assert "ConnectionError" in result["error"]


def test_probe_history_sends_three_messages(monkeypatch):
    captured = {}

    def fake_post(url, json=None, headers=None, **kwargs):
        captured.update(json=json)
        return _FakeResp(200, {"choices": [{"message": {"content": "SYNC42"}}]})

    monkeypatch.setattr("utils.client.requests.post", fake_post)
    _client().probe_history()
    messages = captured["json"]["messages"]
    assert len(messages) == 3
    assert messages[0]["role"] == "user"
    assert messages[1]["role"] == "assistant"
    assert messages[2]["role"] == "user"


def test_probe_history_single_turn_flattens_to_one_message(monkeypatch):
    captured = {}

    def fake_post(url, json=None, headers=None, **kwargs):
        captured.update(json=json)
        return _FakeResp(200, {"choices": [{"message": {"content": "SYNC42"}}]})

    monkeypatch.setattr("utils.client.requests.post", fake_post)
    _client(system_mode="single_turn").probe_history()
    messages = captured["json"]["messages"]
    assert len(messages) == 1
    assert messages[0]["role"] == "user"


# ── get_api() / LLM_CONTENT_FORMAT env var ───────────────

def test_get_api_defaults_content_format_when_unset(monkeypatch):
    _reset_singleton(monkeypatch)
    monkeypatch.setenv("LLM_BASE_URL", "https://x/v1")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_MODEL", "m")
    monkeypatch.delenv("LLM_CONTENT_FORMAT", raising=False)
    assert get_api().content_format == "parts"


def test_get_api_uses_llm_content_format_env(monkeypatch):
    _reset_singleton(monkeypatch)
    monkeypatch.setenv("LLM_BASE_URL", "https://x/v1")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_MODEL", "m")
    monkeypatch.setenv("LLM_CONTENT_FORMAT", "string")
    assert get_api().content_format == "string"


def test_get_api_falls_back_to_default_on_invalid_llm_content_format(monkeypatch):
    _reset_singleton(monkeypatch)
    monkeypatch.setenv("LLM_BASE_URL", "https://x/v1")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_MODEL", "m")
    monkeypatch.setenv("LLM_CONTENT_FORMAT", "bogus")
    assert get_api().content_format == "parts"


def test_get_api_accepts_single_turn_system_mode(monkeypatch):
    _reset_singleton(monkeypatch)
    monkeypatch.setenv("LLM_BASE_URL", "https://x/v1")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_MODEL", "m")
    monkeypatch.setenv("LLM_SYSTEM_MODE", "single_turn")
    assert get_api().system_mode == "single_turn"


# ── run_capability_matrix ─────────────────────────────────

def test_run_capability_matrix_missing_env_raises(monkeypatch):
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_MODEL", "m")
    from utils.client import run_capability_matrix
    with pytest.raises(RuntimeError, match="LLM_BASE_URL"):
        run_capability_matrix()


def test_run_capability_matrix_does_not_touch_global_singleton(monkeypatch):
    _reset_singleton(monkeypatch)
    monkeypatch.setenv("LLM_BASE_URL", "https://x/v1")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_MODEL", "m")
    monkeypatch.setattr(
        "utils.client.requests.post",
        lambda url, **kw: _FakeResp(200, {"choices": [{"message": {"content": "SYSMARK_OK SYNC42"}}]}),
    )
    from utils.client import run_capability_matrix
    import utils.client as client_mod
    run_capability_matrix()
    assert client_mod._client is None


def test_run_capability_matrix_best_case_recommends_string_system(monkeypatch):
    """string+system 一次過（system prompt + history 皆 honored）→ 只探測一格，建議該組合。"""
    monkeypatch.setenv("LLM_BASE_URL", "https://x/v1")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_MODEL", "m")
    calls = []

    def fake_post(url, json=None, headers=None, **kwargs):
        calls.append(json)
        return _FakeResp(200, {"choices": [{"message": {"content": "SYSMARK_OK SYNC42"}}]})

    monkeypatch.setattr("utils.client.requests.post", fake_post)
    from utils.client import run_capability_matrix
    result = run_capability_matrix()

    assert len(result["matrix"]) == 2  # string+system, parts+inline (re-tested)
    first = result["matrix"][0]
    assert first == {"content_format": "string", "system_mode": "system",
                      "system_prompt_honored": True, "history_honored": True}
    assert result["recommendation"] == {"LLM_CONTENT_FORMAT": "string", "LLM_SYSTEM_MODE": "system"}


def test_run_capability_matrix_falls_back_to_string_inline(monkeypatch):
    """string+system 的 system prompt 未被遵循 → 補測 string+inline，該組合全過 → 建議它。"""
    monkeypatch.setenv("LLM_BASE_URL", "https://x/v1")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_MODEL", "m")

    def fake_post(url, json=None, headers=None, **kwargs):
        messages = json["messages"]
        has_system_role = any(m["role"] == "system" for m in messages)
        if has_system_role:
            # string+system: system role 訊息被忽略
            return _FakeResp(200, {"choices": [{"message": {"content": "我不知道"}}]})
        return _FakeResp(200, {"choices": [{"message": {"content": "SYSMARK_OK SYNC42"}}]})

    monkeypatch.setattr("utils.client.requests.post", fake_post)
    from utils.client import run_capability_matrix
    result = run_capability_matrix()

    assert len(result["matrix"]) == 3  # string+system (fail) → string+inline → parts+inline
    assert result["matrix"][0]["system_prompt_honored"] is False
    assert result["matrix"][1] == {"content_format": "string", "system_mode": "inline",
                                    "system_prompt_honored": True, "history_honored": True}
    assert result["recommendation"] == {"LLM_CONTENT_FORMAT": "string", "LLM_SYSTEM_MODE": "inline"}


def test_run_capability_matrix_all_fail_recommends_single_turn(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://x/v1")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_MODEL", "m")
    monkeypatch.setattr(
        "utils.client.requests.post",
        lambda url, **kw: _FakeResp(200, {"choices": [{"message": {"content": "我不知道"}}]}),
    )
    from utils.client import run_capability_matrix
    result = run_capability_matrix()

    assert len(result["matrix"]) == 3
    assert result["recommendation"]["LLM_SYSTEM_MODE"] == "single_turn"
    assert "note" in result["recommendation"]
    assert "model id" in result["recommendation"]["note"]
