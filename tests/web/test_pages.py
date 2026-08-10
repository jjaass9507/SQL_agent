"""前端骨架煙霧測試：七頁 GET 200 + 內容斷言，以及 token 分層規範檢查。"""

import re
import uuid
from pathlib import Path

import httpx
import pytest

from app.main import app

REPO_ROOT = Path(__file__).resolve().parents[2]
CSS_DIR = REPO_ROOT / "app" / "web" / "static" / "css"

# (路徑, 頁面應包含的煙霧字串)
PAGES = [
    ("/", "首頁"),
    ("/chat/demo-session", "需求收集對話"),
    ("/confirm/demo-session", "需求確認"),
    ("/docs/demo-session", "產出進度"),
    ("/review/demo-session", "審查模式報告"),
    ("/agent", "DB Agent"),
    ("/settings", "LLM 連線設定"),
]


@pytest.mark.parametrize("path,expected_text", PAGES)
async def test_page_returns_200_with_expected_content(path, expected_text):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(path)
    assert resp.status_code == 200
    assert expected_text in resp.text
    assert "data-action" in resp.text


async def test_static_css_is_served():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/static/css/tokens.css")
    assert resp.status_code == 200
    assert "--color-primary-600" in resp.text


HEX_COLOR_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b")


@pytest.mark.parametrize("filename", ["components.css", "pages.css"])
def test_components_and_pages_css_have_no_literal_hex_colors(filename):
    """9-1 規範：components.css / pages.css 禁止任何字面色碼，一律 var(--token)。"""
    content = (CSS_DIR / filename).read_text(encoding="utf-8")
    matches = HEX_COLOR_RE.findall(content)
    assert matches == [], f"{filename} 出現字面色碼：{matches}"


def test_tokens_css_defines_the_color_scale():
    """tokens.css 是唯一允許出現色碼的檔案。"""
    content = (CSS_DIR / "tokens.css").read_text(encoding="utf-8")
    assert HEX_COLOR_RE.search(content), "tokens.css 應定義色板"


async def test_agent_page_renders_query_workbench():
    """工作台的 data-action / data-target 是 agent.js 唯一的接點（無 JS 測試框架），
    樣板改名會讓按鈕靜默失效，因此在此固定住。"""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        html = (await client.get("/agent")).text

    for hook in [
        'data-action="switch-agent-tab"',
        'data-panel="query"',
        'data-action="switch-query-mode"',
        'data-target="workbench-question"',
        'data-target="workbench-sql"',
        'data-target="workbench-result"',
    ]:
        assert hook in html, f"agent.js 依賴的接點消失了：{hook}"

    # 丙（不會寫 SQL 的使用者）要求的兩件事：預設是中文提問、查詢前看得到唯讀保證。
    assert "唯讀，不會修改任何資料" in html
    assert html.index('data-target="ask"') < html.index('data-target="sql"')


async def test_confirm_page_has_ddl_validate_button():
    """confirm.js 依賴的接點；驗證結果區塊不存在時按鈕會靜默失效。"""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        html = (await client.get(f"/confirm/{uuid.uuid4()}")).text
    assert 'data-action="validate-ddl-editor"' in html
    assert 'data-target="ddl-validate-result"' in html


async def test_agent_page_has_explain_plan_mode():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        html = (await client.get("/agent")).text
    assert 'data-target="plan"' in html
    assert 'data-target="workbench-plan-sql"' in html


async def test_agent_page_has_schema_browser_mode():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        html = (await client.get("/agent")).text
    assert 'data-target="browse"' in html
    assert 'data-target="schema-browser"' in html


async def test_docs_page_has_diagram_download_and_term_tooltips():
    """丙（不會寫 SQL 的使用者）要求的兩件事：ER 圖能直接下載、術語有白話解釋。"""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        html = (await client.get(f"/docs/{uuid.uuid4()}")).text
    assert 'data-action="download-diagram"' in html
    # 四個分頁標題都要有 title 屬性，滑過去看得到白話解釋
    assert html.count('data-action="switch-tab"') == 4
    for term in ["DDL：", "規格書：", "關聯圖（ER Diagram）：", "安全規劃："]:
        assert term in html, f"缺少術語說明：{term}"
