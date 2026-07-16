"""LLM 健康檢查 + 能力探測 API。

`/api/v1` 前綴由 `app/main.py` 掛載時統一加上，本檔路徑不自帶前綴。
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.config import get_settings
from app.llm import capabilities
from app.llm.capabilities import CapabilityProfile
from app.llm.errors import LLMError
from app.llm.provider import LLMProvider, forced_profile
from app.repos import settings as settings_repo

router = APIRouter(prefix="/llm", tags=["llm"])

DbDep = Annotated[AsyncSession, Depends(get_db)]

# 探測結果持久化的 app_settings key（見 docs/v2_rebuild_plan.md §4-3）。
_PROFILE_KEY = "llm_capability_profile"


class HealthResponse(BaseModel):
    """GET /llm/health 回應：單次 ping 結果 + 目前持久化的能力檔。"""

    ok: bool
    model: str | None
    profile: CapabilityProfile


class DiagnoseResponse(BaseModel):
    """POST /llm/diagnose 回應：本次探測結果 + 實際生效的能力檔。

    `source="forced"` 時，`LLM_FORCE_PROFILE` 覆蓋了探測結果，app 實際採用
    `profile`（=forced）；`probed` 仍是本次探測的真實量測，供比對平台是否已改變。
    """

    profile: CapabilityProfile
    source: Literal["probe", "forced"]
    probed: CapabilityProfile


async def _load_profile(db: AsyncSession) -> CapabilityProfile:
    record = await settings_repo.get_setting(db, _PROFILE_KEY)
    if record is None or not record.value_json:
        return CapabilityProfile()
    return CapabilityProfile.model_validate(record.value_json)


@router.get("/health", response_model=HealthResponse)
async def health(db: DbDep) -> HealthResponse:
    """單次 ping gateway，並回傳目前（上次探測留存的）CapabilityProfile。"""
    provider = LLMProvider.from_settings()
    ok = True
    try:
        await provider.chat([{"role": "user", "content": "ping"}])
    except LLMError:
        ok = False
    profile = await _load_profile(db)
    return HealthResponse(ok=ok, model=provider.model, profile=profile)


@router.post("/diagnose", response_model=DiagnoseResponse)
async def diagnose(db: DbDep) -> DiagnoseResponse:
    """執行五項能力探針，結果存回 app_settings 並回傳；標示實際生效的 profile 來源。"""
    settings = get_settings()
    # 探針需要未套用降級轉接、亦不套用 force 覆蓋的 provider 才能量到 gateway 真實能力。
    provider = LLMProvider.from_settings(settings, apply_force_profile=False)
    probed = await capabilities.probe_all(provider)
    await settings_repo.set_setting(db, _PROFILE_KEY, probed.model_dump())

    forced = forced_profile(settings)
    if forced is not None:
        return DiagnoseResponse(profile=forced, source="forced", probed=probed)
    return DiagnoseResponse(profile=probed, source="probe", probed=probed)
