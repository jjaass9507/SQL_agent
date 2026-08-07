"""依「探測到的 gateway 能力檔」建立 LLMProvider——所有 LLM 呼叫點的唯一入口。

為什麼要有這個模組：`POST /llm/diagnose` 會探測 gateway 支不支援 system role、
原生 tool_calls、json_schema、多輪歷史，結果存進 `app_settings`。但
`LLMProvider.from_settings()` 不會自己去讀那份結果，預設 `CapabilityProfile()`
是「全部支援」。

原本只有 DB Agent 記得帶入探測結果，訪談、文件產出、審查、NL2SQL 都沒有——
面對不支援 system role 的 gateway，DB Agent 正常運作，其餘路徑一律 500。
把讀取集中在這裡，新增呼叫點時就不會再漏。
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.capabilities import CapabilityProfile
from app.llm.provider import LLMProvider
from app.repos import settings as settings_repo

CAPABILITY_SETTING_KEY = "llm_capability_profile"


async def load_profile(db: AsyncSession) -> CapabilityProfile | None:
    """讀取上次探測留存的能力檔；沒探測過回 None（由 provider 用預設值）。"""
    setting = await settings_repo.get_setting(db, CAPABILITY_SETTING_KEY)
    if setting is None or not setting.value_json:
        return None
    return CapabilityProfile(**setting.value_json)


async def build_provider(db: AsyncSession) -> LLMProvider:
    """建立帶有實測能力檔的 provider。所有需要呼叫 LLM 的地方都應該用這個。"""
    return LLMProvider.from_settings(profile=await load_profile(db))
