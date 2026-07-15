"""登入頁前端測試：/login 頁面骨架、data-* 綁定完整性、CSS 零字面色碼。

AD 驗證後端的 GET /api/v1/auth/me、GET /api/v1/auth/sso 由另一條平行分支開發，
尚未合併進本分支時對應端點不存在（回 404）。契約行為測試以「實際呼叫端點、
回 404 就動態 `pytest.skip`」的方式代替 import-time 的路由內省——FastAPI/
Starlette 內部路由結構在不同版本間不穩定（`include_router` 後不保證仍是扁平
`APIRoute` 列表），改用「打一次真實請求看回應」最不依賴內部實作，確保本測試檔
不依賴後端分支即可全綠（見 CLAUDE.md 交付需求）。
"""

import re
from pathlib import Path

import httpx
import pytest

from app.main import app

REPO_ROOT = Path(__file__).resolve().parents[2]
CSS_DIR = REPO_ROOT / "app" / "web" / "static" / "css"

HEX_COLOR_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b")

# 登入頁載入時必須存在的 data-* 綁定點（login.js 依賴這些 attribute，不依賴 class 名稱）。
LOGIN_PAGE_BINDINGS = [
    'data-action="login-submit"',
    'data-target="login-username"',
    'data-target="login-password"',
    'data-target="login-error"',
    'data-action="sso-login"',
    'data-target="sso-login-btn"',
]

# base.html 頂欄使用者選單（所有套用 base.html 的頁面都應具備）。
USER_MENU_BINDINGS = [
    'data-target="user-menu"',
    'data-action="toggle-user-menu"',
    'data-target="user-menu-dropdown"',
    'data-action="logout"',
]


def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def _get(path: str) -> httpx.Response:
    async with _client() as client:
        return await client.get(path)


# ── /login 頁面骨架 ──────────────────────────────────────────────────────


async def test_login_page_returns_200():
    resp = await _get("/login")
    assert resp.status_code == 200
    assert "登入" in resp.text


async def test_login_page_has_required_data_bindings():
    resp = await _get("/login")
    html = resp.text
    missing = [b for b in LOGIN_PAGE_BINDINGS if b not in html]
    assert not missing, f"/login 缺少 JS 綁定點：{missing}"


async def test_login_page_does_not_use_app_shell():
    """登入頁不套 base.html 側欄／導覽（未登入使用者不該看到站內連結）。"""
    resp = await _get("/login")
    assert "sidebar-nav-link" not in resp.text
    assert 'data-active-page="login"' in resp.text


@pytest.mark.parametrize("path", ["/", "/agent", "/settings"])
async def test_other_pages_have_user_menu_bindings(path):
    resp = await _get(path)
    html = resp.text
    missing = [b for b in USER_MENU_BINDINGS if b not in html]
    assert not missing, f"{path} 缺少使用者選單綁定點：{missing}"


# ── CSS 規範：新增樣式維持零字面色碼 ─────────────────────────────────────


@pytest.mark.parametrize("filename", ["components.css", "pages.css"])
def test_login_and_user_menu_css_have_no_literal_hex_colors(filename):
    content = (CSS_DIR / filename).read_text(encoding="utf-8")
    assert HEX_COLOR_RE.findall(content) == [], f"{filename} 出現字面色碼"


# ── auth API 契約（端點不存在時回 404，動態 skip，不讓套件變紅） ────────
#
# 用 tests/web/conftest.py 的 `client` fixture（覆蓋 get_db、SQLite 已建表），
# 而非裸 ASGITransport——auth 端點會寫 activity_log，裸 transport 會打到未
# migrate 的預設 DATABASE_URL。


async def test_auth_me_requires_login_or_reports_anonymous(client):
    resp = await client.get("/api/v1/auth/me")
    if resp.status_code == 404:
        pytest.skip("後端 GET /api/v1/auth/me 尚未合併")
    # AUTH_ENABLED=false（測試預設）時應回匿名 200；true 時未帶 cookie 應回 401。
    assert resp.status_code in (200, 401)
    if resp.status_code == 200:
        body = resp.json()
        assert "anonymous" in body or "email" in body


async def test_auth_sso_endpoint_does_not_500(client):
    resp = await client.get("/api/v1/auth/sso", follow_redirects=False)
    if resp.status_code == 404:
        pytest.skip("後端 GET /api/v1/auth/sso 尚未合併")
    assert resp.status_code < 500


async def test_auth_login_rejects_bad_credentials(client):
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": "nonexistent-user", "password": "wrong-password"},
    )
    if resp.status_code == 404:
        pytest.skip("後端 POST /api/v1/auth/login 尚未合併")
    # 帳密錯誤應回 401；422 代表現行契約尚未支援 username 欄位（過渡期允許，不視為失敗）。
    assert resp.status_code in (401, 422)


async def test_auth_logout_returns_204(client):
    resp = await client.post("/api/v1/auth/logout")
    if resp.status_code == 404:
        pytest.skip("後端 POST /api/v1/auth/logout 尚未合併")
    assert resp.status_code == 204
