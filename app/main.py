"""FastAPI app factory。路由掛載點集中於此，本身不含業務邏輯。"""

import logging

from fastapi import FastAPI

from app.config import get_settings
from app.web.router import mount_static
from app.web.router import router as web_router

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title="SQL Agent v2",
        version="2.0.0a0",
        debug=settings.debug,
    )

    @application.get("/healthz", tags=["ops"])
    async def healthz() -> dict:
        return {"status": "ok"}

    application.include_router(web_router)
    mount_static(application)

    return application


app = create_app()
