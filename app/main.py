"""FastAPI app factory。路由掛載點集中於此，本身不含業務邏輯。"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api import all_routers
from app.config import get_settings
from app.llm.errors import LLMError
from app.web.router import mount_static
from app.web.router import router as web_router
from app.workers.runner import start_worker, stop_worker

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(_: FastAPI):
    # 程序內 job worker（生成/審查/extras）。多 worker 部署的單一啟用限制
    # 見 docs/deployment.md。
    start_worker()
    try:
        yield
    finally:
        await stop_worker()


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title="SQL Agent v2",
        version="2.0.0a0",
        debug=settings.debug,
        lifespan=_lifespan,
    )

    @application.get("/healthz", tags=["ops"])
    async def healthz() -> dict:
        return {"status": "ok"}

    @application.exception_handler(LLMError)
    async def _llm_error_handler(_: Request, exc: LLMError) -> JSONResponse:
        """LLM gateway 掛掉或回不出合法內容時，不要讓使用者看到裸的 500。

        前端會直接顯示後端的 detail（見 lib/api.js 的 friendlyMessage），
        而使用者裡有完全不懂技術的人——「Internal Server Error」會被理解成
        整個平台壞了。技術細節留在 log。
        """
        logger.warning("llm_error", extra={"detail": str(exc)[:300]})
        return JSONResponse(
            status_code=503,
            content={
                "detail": "AI 服務目前無法回應，請稍後再試；"
                "持續發生請聯絡 IT 檢查 LLM 連線設定。"
            },
        )

    for api_router in all_routers():
        application.include_router(api_router, prefix="/api/v1")

    application.include_router(web_router)
    mount_static(application)

    return application


app = create_app()
