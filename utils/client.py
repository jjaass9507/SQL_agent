import logging
import os
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_RETRY_DELAYS = (2, 4, 8)  # seconds between attempts


class LLMClient:
    """OpenAI 相容 Chat Completions API 客戶端。"""

    def __init__(self, base_url: str, api_key: str, model: str,
                 verify: bool = False, timeout: int = 300):
        base_url = base_url.rstrip("/")
        # 使用者可能貼「v1 base」（OpenAI 慣例）或整段「完整 completions 端點」
        # （部分內網 gateway 的原生格式）。兩者皆須支援：若已包含
        # /chat/completions 尾巴，先砍掉，避免下方組 URL 時重複。
        if base_url.endswith("/chat/completions"):
            base_url = base_url[: -len("/chat/completions")].rstrip("/")
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.verify = verify
        # (connect, read) — fail fast when the gateway host is unreachable
        # instead of hanging for the full read timeout with no log output.
        self.timeout = (10, timeout)

    def chat_messages(self, messages: list[dict], system_prompt: Optional[str] = None) -> Optional[str]:
        """送出 messages（可選在最前插入 system_prompt），回傳 AI 純文字回應。
        遇到 HTTP 429 時自動重試（最多 3 次，指數退避 2/4/8 秒）。"""
        full_messages = list(messages)
        if system_prompt:
            full_messages = [
                {"role": "system", "content": [{"type": "text", "text": system_prompt}]},
                *full_messages,
            ]

        payload = {"model": self.model, "messages": full_messages}
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self.base_url}/chat/completions"

        last_error: Optional[str] = None
        for attempt, delay in enumerate((*_RETRY_DELAYS, None), start=1):
            try:
                response = requests.post(
                    url,
                    json=payload,
                    headers=headers,
                    verify=self.verify,
                    # Internal gateway — never route through system HTTP(S) proxies.
                    proxies={"http": None, "https": None},
                    timeout=self.timeout,
                )

                if response.status_code == 429:
                    last_error = f"HTTP 429 Too Many Requests (attempt {attempt})"
                    logger.warning("rate limited (429), attempt %d", attempt)
                    if delay is not None:
                        time.sleep(delay)
                    continue

                response.raise_for_status()
                data = response.json()
                return self._extract_content(data)

            except requests.exceptions.RequestException as e:
                logger.error("LLM API request error (%s → %s): %s",
                             type(e).__name__, url, e)
                return None

        logger.error("LLM API rate limit retries exhausted after %d attempts: %s",
                     len(_RETRY_DELAYS) + 1, last_error)
        return None

    def ping(self) -> dict:
        """單次連線診斷（不重試）：送最小 prompt，回傳 {ok, error?, status_code?}。
        錯誤內容原樣回傳給呼叫端，供 /api/llm/health 顯示連線失敗原因。"""
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": [{"type": "text", "text": "ping"}]}],
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            response = requests.post(
                url, json=payload, headers=headers, verify=self.verify,
                proxies={"http": None, "https": None}, timeout=(10, 60),
            )
        except requests.exceptions.RequestException as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}", "url": url}
        if response.status_code != 200:
            return {"ok": False, "status_code": response.status_code,
                    "error": response.text[:300], "url": url}
        try:
            content = self._extract_content(response.json())
        except ValueError:
            return {"ok": False, "status_code": 200,
                    "error": f"回應不是 JSON：{response.text[:200]}", "url": url}
        if content is None:
            return {"ok": False, "status_code": 200,
                    "error": "回應格式非預期（缺 choices[0].message.content）", "url": url}
        return {"ok": True, "model": self.model}

    def chat(self, system_prompt: str, human_prompt: str) -> Optional[str]:
        """相容介面：包成單則 user 訊息 + system_prompt 呼叫 chat_messages。"""
        return self.chat_messages(
            [{"role": "user", "content": [{"type": "text", "text": human_prompt}]}],
            system_prompt=system_prompt,
        )

    @staticmethod
    def _extract_content(data: dict) -> Optional[str]:
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            logger.warning("unexpected LLM API response shape: %s", str(data)[:200])
            return None

        if isinstance(content, str):
            return content or None
        if isinstance(content, list):
            text = "".join(
                part.get("text", "") for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
            return text or None
        return None


_client: LLMClient | None = None


def get_api() -> LLMClient:
    global _client
    if _client is None:
        base_url = os.environ.get("LLM_BASE_URL")
        api_key = os.environ.get("LLM_API_KEY")
        model = os.environ.get("LLM_MODEL")
        if not base_url:
            raise RuntimeError("請設定環境變數 LLM_BASE_URL（參考 .env.example）")
        if not api_key:
            raise RuntimeError("請設定環境變數 LLM_API_KEY（參考 .env.example）")
        if not model:
            raise RuntimeError("請設定環境變數 LLM_MODEL（參考 .env.example）")
        _client = LLMClient(
            base_url=base_url,
            api_key=api_key,
            model=model,
            verify=os.environ.get("LLM_VERIFY", "false").lower() == "true",
        )
    return _client
