"""Tests for Orchestrator helper logic — no API calls needed."""
from agents.orchestrator import _is_confirmation


def test_confirmation_ok():
    assert _is_confirmation("OK") is True
    assert _is_confirmation("ok") is True
    assert _is_confirmation("Ok") is True


def test_confirmation_chinese():
    assert _is_confirmation("確認") is True
    assert _is_confirmation("好") is True
    assert _is_confirmation("可以") is True
    assert _is_confirmation("沒問題") is True


def test_confirmation_english():
    assert _is_confirmation("yes") is True
    assert _is_confirmation("YES") is True
    assert _is_confirmation("confirm") is True


def test_not_confirmation():
    assert _is_confirmation("我想修改一下") is False
    assert _is_confirmation("不對") is False
    assert _is_confirmation("") is False
    assert _is_confirmation("ok吧") is False


def test_confirmation_with_whitespace():
    assert _is_confirmation("  ok  ") is True
