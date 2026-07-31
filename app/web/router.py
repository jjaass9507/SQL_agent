"""前端頁面路由：純畫面渲染（Jinja2），不含業務邏輯，不呼叫 API。

八頁：index / chat / confirm / docs / review / agent / settings / login。
八個頁面的 handler 只差樣板名與路徑，統一由 `_PAGES` 表註冊；帶
`{session_id}` 的頁面會把該參數一併傳進樣板。login 是唯一不套 base.html
側欄的頁面（登入邏輯全在 static/js/login.js），路由行為與其他頁一致。

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

# (路徑, active_page)；active_page 同時是樣板檔名與路由名（web_{active_page}），
# 樣板以 url_for("web_chat", ...) 之類的名稱互相連結，改名要一起改樣板。
_PAGES = [
    ("/", "index"),
    ("/chat/{session_id}", "chat"),
    ("/confirm/{session_id}", "confirm"),
    ("/docs/{session_id}", "docs"),
    ("/review/{session_id}", "review"),
    ("/agent", "agent"),
    ("/settings", "settings"),
    ("/login", "login"),
]


def _register(path: str, page: str) -> None:
    if "{session_id}" in path:

        async def render(request: Request, session_id: str) -> HTMLResponse:
            return templates.TemplateResponse(
                request, f"{page}.html", {"active_page": page, "session_id": session_id}
            )
    else:

        async def render(request: Request) -> HTMLResponse:
            return templates.TemplateResponse(request, f"{page}.html", {"active_page": page})

    router.get(path, response_class=HTMLResponse, name=f"web_{page}")(render)


for _path, _page in _PAGES:
    _register(_path, _page)


def mount_static(app: FastAPI) -> None:
    """掛載 app/web/static 為 /static。供 app/main.py 呼叫。"""
    app.mount("/static", StaticFiles(directory=str(_WEB_DIR / "static")), name="static")
