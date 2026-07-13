"""app/repos/crypto.py 的測試：AES-256-GCM round-trip 與遮罩。"""

import pytest

from app.config import get_settings
from app.repos import crypto


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """`get_settings()` 有 lru_cache，測試前後清除以確保讀到 monkeypatch 的環境變數。"""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_encrypt_decrypt_round_trip(monkeypatch):
    monkeypatch.setenv("DB_ENCRYPTION_KEY", "00" * 32)
    plaintext = "postgresql://user:secret@host:5432/db"

    token = crypto.encrypt_db_url(plaintext)

    assert token != plaintext
    assert crypto.decrypt_db_url(token) == plaintext


def test_encrypt_produces_different_ciphertext_each_time(monkeypatch):
    """nonce 隨機產生，同樣明文兩次加密結果應不同。"""
    monkeypatch.setenv("DB_ENCRYPTION_KEY", "11" * 32)
    plaintext = "postgresql://user:secret@host:5432/db"

    token1 = crypto.encrypt_db_url(plaintext)
    token2 = crypto.encrypt_db_url(plaintext)

    assert token1 != token2
    assert crypto.decrypt_db_url(token1) == plaintext
    assert crypto.decrypt_db_url(token2) == plaintext


def test_encrypt_raises_when_key_missing(monkeypatch):
    monkeypatch.delenv("DB_ENCRYPTION_KEY", raising=False)

    with pytest.raises(crypto.CryptoConfigError):
        crypto.encrypt_db_url("x")


def test_encrypt_raises_when_key_wrong_length(monkeypatch):
    monkeypatch.setenv("DB_ENCRYPTION_KEY", "00" * 10)  # 只有 10 bytes，非 32

    with pytest.raises(crypto.CryptoConfigError):
        crypto.encrypt_db_url("x")


def test_encrypt_raises_when_key_not_hex(monkeypatch):
    monkeypatch.setenv("DB_ENCRYPTION_KEY", "not-a-hex-string")

    with pytest.raises(crypto.CryptoConfigError):
        crypto.encrypt_db_url("x")


def test_mask_db_url_masks_password():
    url = "postgresql://user:secret@host:5432/db"

    masked = crypto.mask_db_url(url)

    assert masked == "postgresql://user:***@host:5432/db"
    assert "secret" not in masked


def test_mask_db_url_noop_when_no_password():
    url = "sqlite+aiosqlite:///./data/app.db"

    assert crypto.mask_db_url(url) == url
