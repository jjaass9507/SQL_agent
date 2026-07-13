"""DB 連線字串的 AES-256-GCM 加解密與遮罩工具。

依 docs/security_design.md 第五章：DB 連線字串一律加密存放（金鑰來自
`DB_ENCRYPTION_KEY` 環境變數，32-byte hex），API 回應與 log 一律遮罩。
"""

import base64
import os
from urllib.parse import urlsplit, urlunsplit

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import get_settings

_NONCE_LEN = 12  # AES-GCM 建議 96-bit nonce


class CryptoConfigError(RuntimeError):
    """`DB_ENCRYPTION_KEY` 未設定或格式不正確。"""


def _load_key() -> bytes:
    hex_key = get_settings().db_encryption_key
    if not hex_key:
        raise CryptoConfigError("DB_ENCRYPTION_KEY 未設定，無法加解密 DB 連線字串。")
    try:
        key = bytes.fromhex(hex_key)
    except ValueError as exc:
        raise CryptoConfigError("DB_ENCRYPTION_KEY 格式錯誤，需為 hex 字串。") from exc
    if len(key) != 32:
        raise CryptoConfigError(
            "DB_ENCRYPTION_KEY 需為 32 bytes（64 個 hex 字元），AES-256-GCM 要求。"
        )
    return key


def encrypt_db_url(plaintext: str) -> str:
    """加密 DB 連線字串，回傳 base64（nonce + ciphertext）字串供 DB 儲存。"""
    key = _load_key()
    nonce = os.urandom(_NONCE_LEN)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.b64encode(nonce + ciphertext).decode("ascii")


def decrypt_db_url(token: str) -> str:
    """解密 `encrypt_db_url()` 產生的字串，回傳原始明文連線字串。"""
    key = _load_key()
    raw = base64.b64decode(token)
    nonce, ciphertext = raw[:_NONCE_LEN], raw[_NONCE_LEN:]
    return AESGCM(key).decrypt(nonce, ciphertext, None).decode("utf-8")


def mask_db_url(url: str) -> str:
    """遮罩連線字串中的密碼，供前端顯示與 log 記錄使用（絕不外洩明文密碼）。"""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "***"
    if not parts.password:
        return url
    netloc = parts.netloc.replace(f":{parts.password}@", ":***@")
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
