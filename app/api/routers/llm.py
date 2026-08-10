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
from app.services import provider_factory

router = APIRouter(prefix="/llm", tags=["llm"])

DbDep = Annotated[AsyncSession, Depends(get_db)]

class HealthResponse(BaseModel):
    """GET /llm/health 回應：單次 ping 結果 + 目前持久化的能力檔。"""

    ok: bool
    backend: str
    model: str | None
    profile: CapabilityProfile


class DiagnoseResponse(BaseModel):
    """POST /llm/diagnose 回應：本次探測結果 + 實際生效的能力檔。

    `source="forced"` 時，`LLM_FORCE_PROFILE` 覆蓋了探測結果，app 實際採用
    `profile`（=forced）；`probed` 仍是本次探測的真實量測，供比對平台是否已改變。
    """

    profile: CapabilityProfile
    backend: str
    source: Literal["probe", "forced", "backend"]
    probed: CapabilityProfile


@router.get("/health", response_model=HealthResponse)
async def health(db: DbDep) -> HealthResponse:
    """單次 ping gateway，並回傳目前（上次探測留存的）CapabilityProfile。"""
    backend = await provider_factory.selected_backend(db)
    ok = True
    try:
        provider = await provider_factory.build_provider(db)
        await provider.chat([{"role": "user", "content": "ping"}])
    except LLMError:
        ok = False
        provider = None
    profile = await provider_factory.load_profile(db, backend) or CapabilityProfile()
    return HealthResponse(
        ok=ok,
        backend=backend,
        model=provider.model if provider else None,
        profile=profile,
    )


@router.post("/diagnose", response_model=DiagnoseResponse)
async def diagnose(db: DbDep) -> DiagnoseResponse:
    """執行五項能力探針，結果存回 app_settings 並回傳；標示實際生效的 profile 來源。"""
    settings = get_settings()
    backend = await provider_factory.selected_backend(db, settings)
    if backend == "pensieve":
        provider = await provider_factory.build_provider(db)
        await provider.chat([{"role": "user", "content": "ping"}])
        profile = await provider_factory.load_profile(db, backend) or CapabilityProfile()
        return DiagnoseResponse(
            backend=backend, profile=profile, source="backend", probed=profile
        )
    # 探針需要未套用降級轉接、亦不套用 force 覆蓋的 provider 才能量到 gateway 真實能力。
    provider = LLMProvider.from_settings(settings, apply_force_profile=False)
    probed = await capabilities.probe_all(provider)
    await provider_factory.save_profile(db, backend, probed)

    forced = forced_profile(settings)
    if forced is not None:
        return DiagnoseResponse(
            backend=backend, profile=forced, source="forced", probed=probed
        )
    return DiagnoseResponse(backend=backend, profile=probed, source="probe", probed=probed)
