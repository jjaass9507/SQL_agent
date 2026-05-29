import json
import logging
import os
import time
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

_RATE_LIMIT_PHRASES = ("too many requests", "rate limit", "too many")
_RETRY_DELAYS = (2, 4, 8)  # seconds between attempts


class PensieveAPI:
    """與 Pensieve 系統互動，呼叫 AI 進行對話。"""

    def __init__(self, token: str, empno: str, url: str,
                 building: str = "question", verify: bool = False):
        self.token = token
        self.empno = empno
        self.url = url
        self.building = building
        self.verify = verify

    def chat(self, system_prompt: str, human_prompt: str) -> Optional[str]:
        """送出 system_prompt + human_prompt，回傳 AI 純文字回應。
        遇到 rate-limit 錯誤時自動重試（最多 3 次，指數退避 2/4/8 秒）。"""
        effective_human = (
            f"【角色指令】\n{system_prompt}\n\n【輸入】\n{human_prompt}"
            if system_prompt else human_prompt
        )
        payload = {
            "token": self.token,
            "empno": self.empno,
            "variables": {
                "building": self.building,
                "other_system_prompt": system_prompt,
                "other_human_prompt": effective_human,
            },
        }

        last_error: Optional[str] = None
        for attempt, delay in enumerate((*_RETRY_DELAYS, None), start=1):
            try:
                response = requests.post(
                    self.url,
                    json=payload,
                    verify=self.verify,
                    proxies={"http": None, "https": None},
                    timeout=300,
                )

                # Handle HTTP-level rate limiting (429)
                if response.status_code == 429:
                    last_error = f"HTTP 429 Too Many Requests (attempt {attempt})"
                    logger.warning("rate limited (429), attempt %d", attempt)
                    if delay is not None:
                        time.sleep(delay)
                    continue

                response.raise_for_status()

                raw_text = response.text
                if not raw_text or not raw_text.strip():
                    logger.warning("empty API response")
                    return None

                try:
                    res_data = json.loads(raw_text)
                except json.JSONDecodeError:
                    logger.warning("API JSON parse failed")
                    return None

                # Handle response-body rate limiting ("The token has too many requests")
                extracted = self._extract_text(res_data)
                if extracted and any(p in extracted.lower() for p in _RATE_LIMIT_PHRASES):
                    last_error = f"rate limit in response (attempt {attempt}): {extracted[:80]}"
                    logger.warning("rate limit in response body, attempt %d", attempt)
                    if delay is not None:
                        time.sleep(delay)
                    continue

                return extracted

            except requests.exceptions.RequestException as e:
                logger.error("API request error: %s", e)
                return None

        # All retries exhausted
        logger.error("API rate limit retries exhausted after %d attempts: %s",
                     len(_RETRY_DELAYS) + 1, last_error)
        return None

    def _extract_text(self, res_data: Any) -> Optional[str]:
        if isinstance(res_data, dict):
            if "Result" in res_data and isinstance(res_data["Result"], str):
                text = res_data["Result"].replace("\\n", "\n").strip()
                return text or None
            return str(res_data)
        if isinstance(res_data, list) and res_data:
            return str(res_data[0])
        return None


_api: PensieveAPI | None = None


def get_api() -> PensieveAPI:
    global _api
    if _api is None:
        _api = PensieveAPI(
            token=os.environ["PENSIEVE_TOKEN"],
            empno=os.environ["PENSIEVE_EMPNO"],
            url=os.environ.get("PENSIEVE_URL", "https://pensieve.kh.asegroup.com/api/flow_chat/"),
            building=os.environ.get("PENSIEVE_BUILDING", "question"),
            verify=os.environ.get("PENSIEVE_VERIFY", "false").lower() == "true",
        )
    return _api
