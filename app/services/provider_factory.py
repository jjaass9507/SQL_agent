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

from app.config import Settings, get_settings
from app.llm.capabilities import CapabilityProfile
from app.llm.errors import LLMError
from app.llm.pensieve import PENSIEVE_PROFILE, PensieveProvider
from app.llm.provider import LLMProvider
from app.repos import settings as settings_repo

CAPABILITY_SETTING_KEY = "llm_capability_profile"
BACKEND_SETTING_KEY = "llm_backend"
_BACKENDS = {"openai", "pensieve"}


def available_backends(settings: Settings | None = None) -> list[dict]:
    """列出固定兩種後端及其設定狀態，供設定頁顯示與服務端驗證。"""
    settings = settings or get_settings()
    return [
        {
            "id": "openai",
            "label": settings.llm_label,
            "configured": bool(settings.llm_base_url and settings.llm_model),
        },
        {
            "id": "pensieve",
            "label": settings.pensieve_label,
            "configured": bool(
                settings.pensieve_url and settings.pensieve_token and settings.pensieve_empno
            ),
        },
    ]


async def selected_backend(db: AsyncSession, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    record = await settings_repo.get_setting(db, BACKEND_SETTING_KEY)
    selected = record.value_json if record and record.value_json else settings.llm_backend
    return selected if selected in _BACKENDS else "openai"


def _ensure_configured(backend: str, settings: Settings) -> None:
    info = next(item for item in available_backends(settings) if item["id"] == backend)
    if not info["configured"]:
        raise LLMError(f"LLM 後端「{info['label']}」尚未完成連線設定")


async def load_profile(db: AsyncSession, backend: str = "openai") -> CapabilityProfile | None:
    """讀取上次探測留存的能力檔；沒探測過回 None（由 provider 用預設值）。"""
    if backend == "pensieve":
        return PENSIEVE_PROFILE.model_copy()
    setting = await settings_repo.get_setting(db, f"{CAPABILITY_SETTING_KEY}:{backend}")
    if setting is None:
        # 相容既有部署：切換功能上線前的 OpenAI 探測結果使用舊 key。
        setting = await settings_repo.get_setting(db, CAPABILITY_SETTING_KEY)
    if setting is None or not setting.value_json:
        return None
    return CapabilityProfile(**setting.value_json)


async def build_provider(db: AsyncSession) -> LLMProvider:
    """建立帶有實測能力檔的 provider。所有需要呼叫 LLM 的地方都應該用這個。"""
    settings = get_settings()
    backend = await selected_backend(db, settings)
    if backend == "pensieve":
        _ensure_configured(backend, settings)
        return PensieveProvider(
            url=settings.pensieve_url or "",
            token=settings.pensieve_token or "",
            empno=settings.pensieve_empno or "",
            building=settings.pensieve_building,
            verify=settings.pensieve_verify,
            trust_env=settings.llm_trust_env,
            timeout=settings.pensieve_timeout,
            label=settings.pensieve_label,
        )
    return LLMProvider.from_settings(settings, profile=await load_profile(db, backend))


async def save_profile(db: AsyncSession, backend: str, profile: CapabilityProfile) -> None:
    await settings_repo.set_setting(
        db, f"{CAPABILITY_SETTING_KEY}:{backend}", profile.model_dump()
    )
