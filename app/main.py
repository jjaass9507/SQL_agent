"""FastAPI app factory。路由掛載點集中於此，本身不含業務邏輯。"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import all_routers
from app.config import get_settings
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

    for api_router in all_routers():
        application.include_router(api_router, prefix="/api/v1")

    application.include_router(web_router)
    mount_static(application)

    return application


app = create_app()
