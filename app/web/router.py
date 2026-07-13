"""前端頁面路由：純畫面渲染（Jinja2），不含業務邏輯，不呼叫 API。

七頁：index / chat / confirm / docs / review / agent / settings。
資料區塊在樣板內留 data-* 佔位，實際資料由前端 JS 於 API 就緒後透過
app/web/static/js/lib/api.js 抓取並填入（見 docs/v2_rebuild_plan.md 第九章）。
"""

from pathlib import Path

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

_WEB_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(_WEB_DIR / "templates"))

router = APIRouter(tags=["web"])


@router.get("/", response_class=HTMLResponse, name="web_index")
async def index_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "index.html", {"active_page": "index"}
    )


@router.get("/chat/{session_id}", response_class=HTMLResponse, name="web_chat")
async def chat_page(request: Request, session_id: str) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "chat.html", {"active_page": "chat", "session_id": session_id}
    )


@router.get("/confirm/{session_id}", response_class=HTMLResponse, name="web_confirm")
async def confirm_page(request: Request, session_id: str) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "confirm.html", {"active_page": "confirm", "session_id": session_id}
    )


@router.get("/docs/{session_id}", response_class=HTMLResponse, name="web_docs")
async def docs_page(request: Request, session_id: str) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "docs.html", {"active_page": "docs", "session_id": session_id}
    )


@router.get("/review/{session_id}", response_class=HTMLResponse, name="web_review")
async def review_page(request: Request, session_id: str) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "review.html", {"active_page": "review", "session_id": session_id}
    )


@router.get("/agent", response_class=HTMLResponse, name="web_agent")
async def agent_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "agent.html", {"active_page": "agent"}
    )


@router.get("/settings", response_class=HTMLResponse, name="web_settings")
async def settings_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "settings.html", {"active_page": "settings"}
    )


def mount_static(app: FastAPI) -> None:
    """掛載 app/web/static 為 /static。供 app/main.py 呼叫。"""
    app.mount("/static", StaticFiles(directory=str(_WEB_DIR / "static")), name="static")
