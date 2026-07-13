import json
import logging
import os
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_RETRY_DELAYS = (2, 4, 8)  # seconds between attempts
_DEFAULT_READ_TIMEOUT = 120  # seconds; overridable via LLM_TIMEOUT env var
_VALID_SYSTEM_MODES = ("system", "inline", "single_turn")
_VALID_CONTENT_FORMATS = ("parts", "string")
_SYSMARK_PROBE = "SYSMARK_OK"
_HISTORY_PROBE_CODE = "SYNC42"


class LLMClient:
    """OpenAI 相容 Chat Completions API 客戶端。"""

    def __init__(self, base_url: str, api_key: str, model: str,
                 verify: bool = False, timeout: int = 120, system_mode: str = "system",
                 content_format: str = "parts"):
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
        self.system_mode = system_mode
        self.content_format = content_format

    def chat_messages(self, messages: list[dict], system_prompt: Optional[str] = None) -> Optional[str]:
        """送出 messages（可選加入 system_prompt），回傳 AI 純文字回應。
        遇到 HTTP 429 時自動重試（最多 3 次，指數退避 2/4/8 秒）。

        system_mode == "inline" 時（部分內網 gateway 會忽略獨立的 system
        role 訊息），改把 system_prompt 併入第一則 user 訊息文字開頭；
        system_mode == "single_turn" 時（gateway 完全不支援多輪，只轉發最後一則
        訊息），把角色指令＋對話歷史＋本次輸入攤平成恰好一則 user 訊息；
        system_mode == "system"（預設）則沿用獨立 system 訊息。"""
        full_messages = list(messages)
        if system_prompt and self.system_mode == "single_turn":
            full_messages = self._flatten_single_turn(full_messages, system_prompt)
        elif system_prompt and self.system_mode == "inline":
            full_messages = self._inline_system_prompt(full_messages, system_prompt)
        elif system_prompt:
            full_messages = [
                {"role": "system", "content": self._make_content(system_prompt)},
                *full_messages,
            ]

        full_messages = self._normalize_content_format(full_messages)
        payload = {"model": self.model, "messages": full_messages}
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self.base_url}/chat/completions"

        payload_size = len(json.dumps(payload))

        last_error: Optional[str] = None
        for attempt, delay in enumerate((*_RETRY_DELAYS, None), start=1):
            logger.info("LLM request begin: attempt=%d payload_chars=%d model=%s",
                        attempt, payload_size, self.model)
            start = time.monotonic()
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
                elapsed = time.monotonic() - start
                logger.info("LLM request done: elapsed=%.1fs status=%d response_chars=%d",
                            elapsed, response.status_code, len(response.text))

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
                elapsed = time.monotonic() - start
                logger.error("LLM API request error (%s → %s) after %.1fs: %s",
                             type(e).__name__, url, elapsed, e)
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
            "messages": self._normalize_content_format(
                [{"role": "user", "content": self._make_content("ping")}]
            ),
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

    def probe_system_prompt(self) -> dict:
        """探測目前 system_mode 下，system prompt 是否真的被 gateway/模型遵循。
        送出固定回覆指令，檢查回覆是否包含 SYSMARK_OK；不重試，timeout (10, 60)。"""
        probe_system = "你是測試機器人。無論使用者說什麼，你只能回覆這個詞：SYSMARK_OK"
        messages = [{"role": "user", "content": self._make_content("你好")}]
        if self.system_mode == "single_turn":
            full_messages = self._flatten_single_turn(messages, probe_system)
        elif self.system_mode == "inline":
            full_messages = self._inline_system_prompt(messages, probe_system)
        else:
            full_messages = [
                {"role": "system", "content": self._make_content(probe_system)},
                *messages,
            ]
        full_messages = self._normalize_content_format(full_messages)
        payload = {"model": self.model, "messages": full_messages}
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self.base_url}/chat/completions"
        try:
            response = requests.post(
                url, json=payload, headers=headers, verify=self.verify,
                proxies={"http": None, "https": None}, timeout=(10, 60),
            )
        except requests.exceptions.RequestException as e:
            return {"honored": None, "error": f"{type(e).__name__}: {e}"}
        try:
            content = self._extract_content(response.json())
        except ValueError as e:
            return {"honored": None, "error": f"回應不是 JSON：{e}"}
        reply = content or ""
        return {"honored": _SYSMARK_PROBE in reply, "reply": reply[:100]}

    def probe_history(self) -> dict:
        """探測目前設定下，gateway 是否真的把完整對話歷史轉發給模型（而非只送
        最後一則訊息）。送出三則訊息：user 交代暗號 → assistant 確認 → user
        追問暗號是什麼；回覆含暗號視為 honored。不重試，timeout (10, 60)。"""
        messages = [
            {"role": "user", "content": self._make_content(f"請記住我的暗號是 {_HISTORY_PROBE_CODE}")},
            {"role": "assistant", "content": self._make_content("好的")},
            {"role": "user", "content": self._make_content("我的暗號是什麼？")},
        ]
        if self.system_mode == "single_turn":
            full_messages = self._flatten_single_turn(messages)
        else:
            full_messages = messages
        full_messages = self._normalize_content_format(full_messages)
        payload = {"model": self.model, "messages": full_messages}
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self.base_url}/chat/completions"
        try:
            response = requests.post(
                url, json=payload, headers=headers, verify=self.verify,
                proxies={"http": None, "https": None}, timeout=(10, 60),
            )
        except requests.exceptions.RequestException as e:
            return {"honored": None, "error": f"{type(e).__name__}: {e}"}
        try:
            content = self._extract_content(response.json())
        except ValueError as e:
            return {"honored": None, "error": f"回應不是 JSON：{e}"}
        reply = content or ""
        return {"honored": _HISTORY_PROBE_CODE in reply, "reply": reply[:100]}

    def chat(self, system_prompt: str, human_prompt: str) -> Optional[str]:
        """相容介面：包成單則 user 訊息 + system_prompt 呼叫 chat_messages。"""
        return self.chat_messages(
            [{"role": "user", "content": self._make_content(human_prompt)}],
            system_prompt=system_prompt,
        )

    @staticmethod
    def _inline_system_prompt(messages: list[dict], system_prompt: str) -> list[dict]:
        """system_mode == "inline" 時，將 system_prompt 併入第一則 user 訊息的
        第一個 text part 開頭（格式沿用舊 Pensieve client 慣例），避免送出獨立
        system role 訊息。只淺拷貝要修改的那層，不動呼叫端傳入的原始物件。"""
        result = list(messages)
        for i, msg in enumerate(result):
            if msg.get("role") != "user":
                continue
            content = msg.get("content")
            new_msg = dict(msg)
            if isinstance(content, str):
                new_msg["content"] = f"【角色指令】\n{system_prompt}\n\n【輸入】\n{content}"
            elif isinstance(content, list):
                new_content = list(content)
                for j, part in enumerate(new_content):
                    if isinstance(part, dict) and part.get("type") == "text":
                        new_part = dict(part)
                        new_part["text"] = f"【角色指令】\n{system_prompt}\n\n【輸入】\n{part.get('text', '')}"
                        new_content[j] = new_part
                        break
                new_msg["content"] = new_content
            else:
                continue
            result[i] = new_msg
            return result
        # 理論上不會發生：整串沒有 user 訊息，退回獨立 system message
        return [
            {"role": "system", "content": [{"type": "text", "text": system_prompt}]},
            *messages,
        ]

    @staticmethod
    def _flatten_single_turn(messages: list[dict], system_prompt: Optional[str] = None) -> list[dict]:
        """system_mode == "single_turn" 時（gateway 完全不支援多輪，只轉發最後
        一則訊息），把「角色指令＋對話歷史＋本次輸入」攤平成恰好一則 user 訊息。
        單則輸入（無歷史）時省略【對話歷史】段；<TOOL>/<OBSERVATION> 標籤保留
        原文。只讀取傳入的 messages，不修改呼叫端物件。"""
        def _text(msg: dict) -> str:
            content = msg.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return "".join(
                    p.get("text", "") for p in content
                    if isinstance(p, dict) and p.get("type") == "text"
                )
            return ""

        if not messages:
            body = f"【角色指令】\n{system_prompt}" if system_prompt else ""
            return [{"role": "user", "content": [{"type": "text", "text": body}]}]

        *history, last = messages
        last_text = _text(last)

        sections = []
        if system_prompt:
            sections.append(f"【角色指令】\n{system_prompt}")
        if history:
            lines = [
                f"{'[使用者]' if m.get('role') == 'user' else '[助手]'}: {_text(m)}"
                for m in history
            ]
            sections.append("【對話歷史】\n" + "\n".join(lines))
        sections.append(f"【本次輸入】\n{last_text}")

        flattened_text = "\n\n".join(sections)
        return [{"role": "user", "content": [{"type": "text", "text": flattened_text}]}]

    def _make_content(self, text: str):
        """依 self.content_format 組出單則訊息的 content：string 模式回純字串、
        parts 模式（預設）回 [{"type": "text", "text": ...}]。"""
        if self.content_format == "string":
            return text
        return [{"type": "text", "text": text}]

    def _normalize_content_format(self, messages: list[dict]) -> list[dict]:
        """送出前把整串 messages 的 content 形態正規化成 self.content_format
        （string 或 parts），不論呼叫端組訊息時用的是哪一種形態。新建
        list/dict，不修改呼叫端傳入的原始物件。"""
        result = []
        for msg in messages:
            content = msg.get("content")
            new_msg = dict(msg)
            if self.content_format == "string" and isinstance(content, list):
                new_msg["content"] = "".join(
                    part.get("text", "") for part in content
                    if isinstance(part, dict) and part.get("type") == "text"
                )
            elif self.content_format == "parts" and isinstance(content, str):
                new_msg["content"] = [{"type": "text", "text": content}]
            result.append(new_msg)
        return result

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
        raw_timeout = os.environ.get("LLM_TIMEOUT")
        timeout = _DEFAULT_READ_TIMEOUT
        if raw_timeout:
            try:
                timeout = int(raw_timeout)
            except ValueError:
                logger.warning("LLM_TIMEOUT=%r 不是有效整數，改用預設值 %ds",
                                raw_timeout, _DEFAULT_READ_TIMEOUT)
        raw_system_mode = os.environ.get("LLM_SYSTEM_MODE", "system")
        system_mode = raw_system_mode
        if system_mode not in _VALID_SYSTEM_MODES:
            logger.warning("LLM_SYSTEM_MODE=%r 不是有效值（%s），改用預設值 %r",
                            raw_system_mode, "/".join(_VALID_SYSTEM_MODES), "system")
            system_mode = "system"
        raw_content_format = os.environ.get("LLM_CONTENT_FORMAT", "parts")
        content_format = raw_content_format
        if content_format not in _VALID_CONTENT_FORMATS:
            logger.warning("LLM_CONTENT_FORMAT=%r 不是有效值（%s），改用預設值 %r",
                            raw_content_format, "/".join(_VALID_CONTENT_FORMATS), "parts")
            content_format = "parts"
        _client = LLMClient(
            base_url=base_url,
            api_key=api_key,
            model=model,
            verify=os.environ.get("LLM_VERIFY", "false").lower() == "true",
            timeout=timeout,
            system_mode=system_mode,
            content_format=content_format,
        )
    return _client


def run_capability_matrix() -> dict:
    """探測不同 (content_format, system_mode) 組合的多輪相容性，供
    GET /api/llm/diagnose 使用。依序建構臨時 LLMClient（不影響全域 _client
    單例）探測：string+system →（若 system prompt 未被遵循）string+inline →
    parts+inline（目前組合）。回傳 {"matrix": [...], "recommendation": ...}；
    每格矩陣為 {content_format, system_mode, system_prompt_honored,
    history_honored}。全部組合皆未同時通過兩項探測時，建議 single_turn 並附
    向 gateway 管理者確認原生模型 model id 的說明。約 4-6 次 LLM 呼叫。"""
    base_url = os.environ.get("LLM_BASE_URL")
    api_key = os.environ.get("LLM_API_KEY")
    model = os.environ.get("LLM_MODEL")
    if not base_url:
        raise RuntimeError("請設定環境變數 LLM_BASE_URL（參考 .env.example）")
    if not api_key:
        raise RuntimeError("請設定環境變數 LLM_API_KEY（參考 .env.example）")
    if not model:
        raise RuntimeError("請設定環境變數 LLM_MODEL（參考 .env.example）")

    raw_timeout = os.environ.get("LLM_TIMEOUT")
    timeout = _DEFAULT_READ_TIMEOUT
    if raw_timeout:
        try:
            timeout = int(raw_timeout)
        except ValueError:
            pass
    verify = os.environ.get("LLM_VERIFY", "false").lower() == "true"

    def _probe(content_format: str, system_mode: str) -> dict:
        client = LLMClient(
            base_url=base_url, api_key=api_key, model=model,
            verify=verify, timeout=timeout,
            system_mode=system_mode, content_format=content_format,
        )
        sys_probe = client.probe_system_prompt()
        hist_probe = client.probe_history()
        return {
            "content_format": content_format,
            "system_mode": system_mode,
            "system_prompt_honored": sys_probe.get("honored"),
            "history_honored": hist_probe.get("honored"),
        }

    matrix = [_probe("string", "system")]
    if not matrix[0]["system_prompt_honored"]:
        matrix.append(_probe("string", "inline"))
    matrix.append(_probe("parts", "inline"))

    best = next(
        (e for e in matrix if e["system_prompt_honored"] and e["history_honored"]),
        None,
    )
    if best is not None:
        recommendation = {
            "LLM_CONTENT_FORMAT": best["content_format"],
            "LLM_SYSTEM_MODE": best["system_mode"],
        }
    else:
        recommendation = {
            "LLM_CONTENT_FORMAT": "string",
            "LLM_SYSTEM_MODE": "single_turn",
            "note": (
                "多輪對話在目前所有標準組合下皆失敗，建議先設定 LLM_SYSTEM_MODE=single_turn"
                "（每次攤平成單一訊息送出）作為後備方案。同時建議向 gateway 管理者確認 "
                "LLM_MODEL 目前的值是否為原生模型的 model id——若是 gateway 上包裝的 "
                "flow/應用 id，改用原生模型通常能取得完整多輪支援。"
            ),
        }
    return {"matrix": matrix, "recommendation": recommendation}
