"""前端骨架煙霧測試：七頁 GET 200 + 內容斷言，以及 token 分層規範檢查。"""

import re
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
