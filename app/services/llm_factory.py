"""依 app_settings 留存的能力探測結果（llm_capability_profile）建構 LLMProvider。

`POST /api/v1/llm/diagnose` 探測後把 CapabilityProfile 存進 app_settings；
所有**業務**呼叫點都應經由本模組取得 provider，探測到的降級轉接
（`app/llm/adapters.py`）才會實際生效。直接 `LLMProvider.from_settings()`
只適用於探針本身與 health ping——那兩處需要量測 gateway 的原始能力。
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import get_settings
from app.llm.capabilities import CapabilityProfile
from app.llm.provider import LLMProvider
from app.repos import settings as settings_repo

CAPABILITY_SETTING_KEY = "llm_capability_profile"


async def provider_from_db(db: AsyncSession) -> LLMProvider:
    """讀取上次探測留存的能力檔建構 provider；從未探測過則用預設（全 True）。

    `.env` 設了 `LLM_FORCE_PROFILE`（JSON）時優先於探測結果——除錯用，
    未列出的欄位視為 true。只影響業務 provider，`/diagnose` 探測不受影響。
    """
    force = get_settings().llm_force_profile
    if force:
        return LLMProvider.from_settings(profile=CapabilityProfile.model_validate_json(force))
    setting = await settings_repo.get_setting(db, CAPABILITY_SETTING_KEY)
    profile = CapabilityProfile(**setting.value_json) if setting and setting.value_json else None
    return LLMProvider.from_settings(profile=profile)


async def provider_from_factory(
    session_factory: async_sessionmaker[AsyncSession],
) -> LLMProvider:
    """同 `provider_from_db`，但自行開一個短命 session 讀能力檔（給 worker 場景）。"""
    async with session_factory() as db:
        return await provider_from_db(db)
