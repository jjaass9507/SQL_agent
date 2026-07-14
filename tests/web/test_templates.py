"""模板 data-* 完整性：各頁 JS 啟動時靜態依賴的 data-action / data-target 必須存在。

pages/*.js 以 document.querySelector('[data-*="..."]') 綁定行為，模板若改壞
（換皮膚時誤刪 data-* attribute）行為會靜默失效——本檔把每頁「載入時就必須存在」
的綁定點固定下來（JS 動態插入的節點不在此列，如 tables-ready-banner、抽屜本體）。
"""

import httpx
import pytest

from app.main import app

# (頁面路徑, 頁面 HTML 必須包含的 data-* attribute)
PAGE_BINDINGS = [
    (
        "/",
        [
            'data-action="create-session"',
            'data-action="filter-sessions"',
            'data-target="session-list"',
            'data-action="submit-review-import"',
            'data-target="review-db-url"',
            'data-target="review-title"',
            'data-action="submit-ddl-import"',
            'data-target="ddl-import-text"',
            'data-target="ddl-import-title"',
            'data-action="cancel-form"',
        ],
    ),
    (
        "/chat/demo-session",
        [
            'data-session-id="demo-session"',
            'data-action="send-message"',
            'data-target="message-input"',
            'data-target="chat-messages"',
            'data-target="collection-progress-list"',
            'data-action="go-to-confirm"',
        ],
    ),
    (
        "/confirm/demo-session",
        [
            'data-session-id="demo-session"',
            'data-target="version-select"',
            'data-action="restore-version"',
            'data-action="confirm-generate"',
            'data-target="requirement-summary-list"',
            'data-target="schema-diff"',
            'data-target="schema-tables-container"',
        ],
    ),
    (
        "/docs/demo-session",
        [
            'data-session-id="demo-session"',
            'data-target="generation-status"',
            'data-target="progress-item"',
            'data-file="01_specification.md"',
            'data-file="02_er_diagram.md"',
            'data-file="03_ddl.sql"',
            'data-file="04_security_plan.md"',
            'data-action="switch-tab"',
            'data-target="doc-content-spec"',
            'data-target="doc-content-er_diagram"',
            'data-target="doc-content-ddl"',
            'data-target="doc-content-security_plan"',
            'data-action="download-single"',
            'data-action="download-all"',
            'data-action="generate-extra"',
            'data-kind="orm"',
            'data-kind="migration"',
            'data-kind="query"',
            'data-kind="incremental"',
            'data-kind="dbml"',
            'data-kind="plantuml"',
            'data-kind="jsonschema"',
            'data-kind="datadict"',
            'data-target="extras-list"',
        ],
    ),
    (
        "/review/demo-session",
        [
            'data-session-id="demo-session"',
            'data-target="review-score-value"',
            'data-target="review-summary"',
            'data-target="review-flags-consistency"',
            'data-target="review-flags-integrity"',
            'data-target="review-flags-performance"',
            'data-target="review-flags-security"',
            'data-target="review-red-flags-list"',
            'data-action="download-report"',
        ],
    ),
    (
        "/agent",
        [
            'data-action="agent-send-message"',
            'data-target="agent-message-input"',
            'data-target="agent-messages"',
            'data-target="agent-tool-trace-list"',
            'data-target="db-select"',
            'data-target="change-request-list"',
            'data-target="agent-admin-token"',
            'data-action="save-admin-token"',
        ],
    ),
    (
        "/settings",
        [
            'data-action="test-connection"',
            'data-target="llm-health-result"',
            'data-action="diagnose-llm"',
            'data-target="capability-multi_turn"',
            'data-target="capability-system_role"',
            'data-target="capability-native_tools"',
            'data-target="capability-json_schema"',
            'data-target="capability-streaming"',
            'data-target="capability-probed-at"',
            'data-target="memory-backend"',
            'data-target="business-db-list"',
            'data-action="add-business-db"',
            'data-target="business-db-name"',
            'data-target="business-db-url"',
            'data-target="admin-token"',
            'data-action="save-admin-token"',
            'data-target="activity-list"',
        ],
    ),
]


async def _get(path: str) -> str:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(path)
    assert resp.status_code == 200
    return resp.text


@pytest.mark.parametrize("path,bindings", PAGE_BINDINGS, ids=[p for p, _ in PAGE_BINDINGS])
async def test_page_has_required_data_bindings(path, bindings):
    html = await _get(path)
    missing = [b for b in bindings if b not in html]
    assert not missing, f"{path} 缺少 JS 綁定點：{missing}"


async def test_all_pages_include_agent_drawer_module_and_active_page_marker():
    """base.html 全站掛 DB Agent 抽屜 module；body 帶 data-active-page 供抽屜略過 /agent。"""
    for path, marker in [("/", "index"), ("/agent", "agent"), ("/settings", "settings")]:
        html = await _get(path)
        assert "js/lib/drawer.js" in html, f"{path} 未載入抽屜 module"
        assert f'data-active-page="{marker}"' in html
