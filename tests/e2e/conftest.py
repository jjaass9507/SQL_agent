"""瀏覽器煙霧測試的 fixtures。

為什麼需要這一層：ER 關聯圖的 bug（在 display:none 的分頁裡渲染，mermaid 量不到
尺寸而產生 NaN 座標）發生在瀏覽器的版面計算階段。當時 572 個測試全綠，
`tests/web/test_pages.py` 也確認了 HTML 裡有正確的 data-action 接點——但圖對每個
使用者、每份文件都是壞的。只有真的把畫面畫出來才看得到。

這一層刻意只做煙霧測試：跑得起來、畫得出來、不出現「看起來像故障」的字樣。
商業邏輯留給快得多的 API 測試，不要用瀏覽器測那些。

預設不執行（需要 `-m e2e`），因為它慢且依賴瀏覽器。
"""

import asyncio
import os
import pathlib
import socket
import threading
import time

import pytest

pytest.importorskip("playwright.sync_api", reason="e2e 需要 playwright")

import uvicorn  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

# 本機/容器可能有預先安裝的 chromium；CI 則由 `playwright install` 自行管理，
# 指定不存在的路徑會直接啟動失敗，因此只有檔案真的存在時才覆寫。
_PREINSTALLED = os.environ.get("E2E_CHROMIUM", "/opt/pw-browsers/chromium")
CHROMIUM = _PREINSTALLED if pathlib.Path(_PREINSTALLED).exists() else None


def run_async(coro):
    """在獨立執行緒跑一段 coroutine。

    pytest-asyncio 的 auto 模式會讓 fixture 跑在既有的事件迴圈裡，而 playwright
    用的是同步 API——兩者不能共存於同一條執行緒，因此非同步的準備工作丟出去跑。
    """
    box = {}

    def runner():
        box["value"] = asyncio.run(coro)

    thread = threading.Thread(target=runner)
    thread.start()
    thread.join()
    return box.get("value")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def live_server(tmp_path_factory):
    """在背景執行緒跑真的 app（SQLite 落在暫存目錄，不碰任何正式資料）。"""
    data_dir = tmp_path_factory.mktemp("e2e")
    os.environ.update(
        {
            "DATABASE_URL": f"sqlite+aiosqlite:///{data_dir / 'e2e.db'}",
            "DB_ENCRYPTION_KEY": "ab" * 32,
            "ADMIN_TOKEN": "e2e-token",
        }
    )
    from app.config import get_settings
    from app.main import create_app
    from app.repos.db import get_engine
    from app.repos.models import Base

    get_settings.cache_clear()

    async def _create_tables():
        async with get_engine().begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    run_async(_create_tables())

    port = _free_port()
    config = uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)
    else:
        pytest.fail("app 沒有在時限內啟動")

    yield base
    server.should_exit = True
    thread.join(timeout=10)


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        launched = p.chromium.launch(executable_path=CHROMIUM, args=["--no-sandbox"])
        # executable_path=None 時 playwright 用自己安裝的版本
        yield launched
        launched.close()


@pytest.fixture
def page(browser):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    created = context.new_page()
    yield created
    context.close()
