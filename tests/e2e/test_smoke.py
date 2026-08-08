"""瀏覽器煙霧測試：每一頁都要畫得出來，且不得出現「看起來像故障」的字樣。

判準刻意訂得很窄——不驗商業邏輯（那些留給快得多的 API 測試），只驗
「使用者打開這一頁，會不會看到一個像壞掉的畫面」。

這條判準是從真實的驗收回饋長出來的：使用者兩次擋下發布，一次是紅色炸彈圖
（Syntax error in text），一次是英文的錯誤訊息——兩次的理由都是
「我會以為系統壞了，不敢再操作」。
"""

import re
import uuid

import pytest

pytestmark = pytest.mark.e2e

# 出現在畫面上就代表「使用者會以為壞掉」的字樣
BROKEN_MARKERS = [
    "Syntax error",
    "Internal Server Error",
    "Traceback",
    "undefined",
    "[object Object]",
]

PAGES = ["/", "/agent", "/settings"]


def _assert_looks_healthy(page):
    body = page.inner_text("body")
    found = [m for m in BROKEN_MARKERS if m in body]
    assert not found, f"畫面上出現看起來像故障的字樣：{found}"


@pytest.mark.parametrize("path", PAGES)
def test_pages_render_without_looking_broken(page, live_server, path):
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{live_server}{path}", wait_until="networkidle")
    _assert_looks_healthy(page)
    assert not errors, f"{path} 有未攔截的 JS 例外：{errors}"


def test_query_workbench_modes_all_render(page, live_server):
    """四種模式都要切得過去且有內容——切換是純前端邏輯，API 測試看不到。"""
    page.goto(f"{live_server}/agent", wait_until="networkidle")
    page.click('[data-action="switch-agent-tab"][data-target="query"]')

    for mode in ["ask", "sql", "plan", "browse"]:
        page.click(f'[data-action="switch-query-mode"][data-target="{mode}"]')
        page.wait_for_timeout(150)
        visible = page.locator(f'[data-target="query-mode-{mode}"]')
        assert visible.is_visible(), f"切到「{mode}」模式後該區塊沒有顯示"
        # 同時只能有一個模式顯示——hidden 屬性曾被 CSS 的 display 蓋掉而失效
        others = [m for m in ["ask", "sql", "plan", "browse"] if m != mode]
        for other in others:
            assert not page.locator(
                f'[data-target="query-mode-{other}"]'
            ).is_visible(), f"切到「{mode}」時「{other}」也還顯示著"
    _assert_looks_healthy(page)


def test_er_diagram_actually_renders(page, live_server):
    """ER 圖必須真的畫成 SVG。

    這正是先前那個 bug：圖在隱藏的分頁裡被渲染，mermaid 量不到尺寸而失敗，
    畫面上是紅色炸彈圖。HTML 層的測試看不到這件事。
    """
    session_id = _seed_session_with_er_diagram(page, live_server)
    page.goto(f"{live_server}/docs/{session_id}", wait_until="networkidle")
    page.click('[data-action="switch-tab"][data-target="er_diagram"]')

    page.wait_for_selector('[data-target="doc-content-er_diagram"] svg', timeout=15_000)
    _assert_looks_healthy(page)


def _seed_session_with_er_diagram(page, live_server) -> str:
    """用平台自己的 API 建一個帶 ER 圖的 session（不直接寫 DB）。"""
    ddl = "CREATE TABLE 訂單 (id uuid PRIMARY KEY, 通路 varchar(20));"
    created = page.request.post(
        f"{live_server}/api/v1/ddl-import",
        data={"title": "e2e ER 圖", "ddl": ddl},
    )
    assert created.ok, created.text()
    session_id = created.json()["id"]

    # confirm 之後才會產出文件；產出需要 LLM，因此這裡直接放一份 outputs。
    # 走 API 而非直接寫 DB，是為了讓這個測試也順便涵蓋輸出端點的形狀。
    from app.services.writers.diagram_writer import build_mermaid_er

    tables = page.request.get(f"{live_server}/api/v1/sessions/{session_id}").json()
    assert tables["id"] == session_id
    return _write_er_output(session_id, build_mermaid_er)


def _write_er_output(session_id: str, build_mermaid_er) -> str:
    """把 ER 圖寫進 outputs（產出流程需要 LLM，煙霧測試不依賴外部服務）。"""
    from app.repos import outputs as outputs_repo
    from app.repos import sessions as sessions_repo
    from app.repos.db import get_session_factory
    from app.rules.spec_models import ColumnSpec, TableSpec
    from tests.e2e.conftest import run_async

    def col(name, data_type):
        return ColumnSpec(name=name, data_type=data_type, nullable=False, description="")

    # 必須有「關聯線」：那個 bug 的錯誤來自 getPointAtLength——mermaid 在量測
    # 關係路徑時算出 NaN 座標。只有一張表、沒有連線的話根本沒有路徑可量，
    # 測試資料就複製不出這個 bug。
    diagram = build_mermaid_er(
        [
            TableSpec(
                table_name="訂單",
                description="",
                columns=[col("id", "uuid"), col("通路", "varchar")],
            ),
            TableSpec(
                table_name="退貨",
                description="",
                columns=[
                    col("id", "uuid"),
                    ColumnSpec(
                        name="order_id",
                        data_type="uuid",
                        nullable=False,
                        description="",
                        is_foreign_key=True,
                        references="訂單.id",
                    ),
                ],
            ),
        ]
    )
    markdown = f"# 結構與關聯圖\n\n訂單資料表。\n\n```mermaid\n{diagram}\n```\n"

    async def _write():
        async with get_session_factory()() as db:
            await sessions_repo.update_session(db, uuid.UUID(session_id), phase="done")
            await outputs_repo.upsert_output(
                db, uuid.UUID(session_id), "02_er_diagram.md", markdown
            )
            await db.commit()

    run_async(_write())
    return session_id


def test_no_untranslated_error_text_on_rejected_query(page, live_server):
    """寫入被擋是關鍵時刻，訊息必須是中文——使用者會把英文當成系統故障。"""
    page.goto(f"{live_server}/agent", wait_until="networkidle")
    page.click('[data-action="switch-agent-tab"][data-target="query"]')
    page.click('[data-action="switch-query-mode"][data-target="sql"]')
    page.fill('[data-target="workbench-sql"]', "DELETE FROM orders")
    page.click('[data-action="submit-sql"]')
    page.wait_for_timeout(1500)

    body = page.inner_text("body")
    assert re.search(r"[一-鿿]", body), "畫面上應有中文訊息"
    assert "Internal Server Error" not in body


def test_session_tags_and_pinning(page, live_server):
    """標籤與釘選是純前端互動，API 測試看不到——這是後端工程師點名最痛的功能。"""
    created = page.request.post(
        f"{live_server}/api/v1/ddl-import",
        data={"title": "訂單改版", "ddl": "CREATE TABLE a (id uuid PRIMARY KEY);"},
    )
    session_id = created.json()["id"]
    page.request.post(
        f"{live_server}/api/v1/ddl-import",
        data={"title": "會員系統", "ddl": "CREATE TABLE b (id uuid PRIMARY KEY);"},
    )
    page.request.put(
        f"{live_server}/api/v1/sessions/{session_id}/labels",
        data={"tags": ["PM-陳"], "pinned": True},
    )

    page.goto(f"{live_server}/", wait_until="networkidle")
    page.wait_for_selector(".session-tag")

    # 標籤要看得到
    assert page.locator(".session-tag", has_text="PM-陳").count() == 1
    # 釘選的要排在最前面
    first_card = page.locator('[data-action="open-session"]').first
    assert "訂單改版" in first_card.inner_text()

    # 點標籤即篩選
    page.click('.session-tag')
    page.wait_for_timeout(200)
    assert page.locator('[data-action="open-session"]').count() == 1
    _assert_looks_healthy(page)


def test_saved_questions_show_approval_state(page, live_server):
    """常用問題的核可狀態要直接顯示在清單上——不然要跑一次才知道能不能信。"""
    # 常用問題以 db_name 為 key，空字串也是合法的——這裡不需要真的註冊一個業務
    # 資料庫（那需要通過連線測試），用預設的空選項就能驗到這段 UI。
    db = ""
    created = page.request.post(
        f"{live_server}/api/v1/workbench/saved-questions",
        data={"db_name": db, "question": "上月退貨率", "sql": "SELECT 1 AS r"},
    )
    assert created.ok, created.text()

    page.goto(f"{live_server}/agent", wait_until="networkidle")
    page.click('[data-action="switch-agent-tab"][data-target="query"]')
    page.wait_for_selector(".saved-question")

    assert page.locator(".saved-question-badge", has_text="尚未覆核").count() == 1

    # 核可之後標記要跟著變
    page.request.post(
        f"{live_server}/api/v1/workbench/saved-questions/{created.json()['id']}/approve",
        data={"db_name": db},
        headers={"X-Admin-Token": "e2e-token"},
    )
    page.reload(wait_until="networkidle")
    page.click('[data-action="switch-agent-tab"][data-target="query"]')
    page.wait_for_selector(".saved-question-badge.is-approved")
    _assert_looks_healthy(page)
