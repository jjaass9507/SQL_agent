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
        """LLM 相關失敗回 502 並帶上訊息。

        沒有這個 handler 時一律是沒有內容的 `500 Internal Server Error`，
        「gateway 連不上」「LLM_FORCE_PROFILE 格式錯」「模型回不出合法 JSON」
        在前端與 curl 看起來一模一樣，只能翻 server log 才知道發生什麼事。
        """
        logger.warning("llm_error_response", extra={"error": str(exc)})
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    for api_router in all_routers():
        application.include_router(api_router, prefix="/api/v1")

    application.include_router(web_router)
    mount_static(application)

    return application


app = create_app()
