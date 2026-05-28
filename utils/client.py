import json
import os
from typing import Any, Optional

import requests


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
        other_system_prompt 不一定會被 API 套用為 system role，
        因此將指令合併至 other_human_prompt 確保模型一定看到。"""
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

        try:
            response = requests.post(
                self.url,
                json=payload,
                verify=self.verify,
                proxies={"http": None, "https": None},
                timeout=300,
            )
            response.raise_for_status()

            raw_text = response.text
            if not raw_text or not raw_text.strip():
                print("[API] 警告：回傳空白內容。")
                return None

            try:
                res_data = json.loads(raw_text)
            except json.JSONDecodeError:
                print("[API] JSON 解析失敗。")
                return None

            return self._extract_text(res_data)

        except Exception as e:
            print(f"[API 錯誤] {e}")
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
