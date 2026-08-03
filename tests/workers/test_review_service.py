"""review_service：context_tables → Reviewer（LLM）全流程 + 規則式紅旗/修復 SQL + phase 轉移。"""

import json
import uuid

import pytest
import respx

from app.repos import jobs as jobs_repo
from app.repos import outputs as outputs_repo
from app.repos import sessions as sessions_repo
from app.rules.spec_models import TableSpec
from app.services import review_service
from tests.specs import col
from tests.workers.conftest import BASE_URL, chat_completion_response, make_provider


def _existing_tables() -> list[TableSpec]:
    return [
        TableSpec(
            table_name="users",
            description="使用者",
            columns=[
                col("id", "uuid", False, "主鍵", is_primary_key=True),
                col("password", "varchar", False, "密碼", length=100),
            ],
        )
    ]


async def test_run_review_writes_report_and_fix_sql_and_updates_phase(session_factory):
    """降級路徑：gateway 回不出合法 JSON 時，退回純文字單發仍要產出報告。"""
    tables = _existing_tables()
    async with session_factory() as db:
        session = await sessions_repo.create_session(
            db,
            mode="review",
            context_tables_json=[t.model_dump() for t in tables],
        )
        job = await jobs_repo.create_job(db, session.id, kind="review")
        await db.commit()
        session_id, job_id = session.id, job.id

    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/chat/completions").mock(
            return_value=chat_completion_response(
                "## 1. 設計一致性\n- **users**：命名一致。\n\n**整體評分：7/10**"
            )
        )
        provider = make_provider()
        async with session_factory() as db:
            job = await jobs_repo.get_job(db, job_id)
            await review_service.run_review(db, job, provider=provider)
            await db.commit()

    async with session_factory() as db:
        outputs = {o.filename: o.content for o in await outputs_repo.list_outputs(db, session_id)}
        session = await sessions_repo.get_session(db, session_id)

    assert "整體評分：7/10" in outputs["05_review_report.md"]
    # password 欄位命中 schema_advisor 的敏感欄位紅旗，remediation 產出對應的 TODO 註解
    assert "敏感資料" in outputs["06_review_fix.sql"]
    assert session.phase == "review_done"


async def test_run_review_raises_when_session_has_no_context_tables(session_factory):
    async with session_factory() as db:
        session = await sessions_repo.create_session(db, mode="review", context_tables_json=[])
        job = await jobs_repo.create_job(db, session.id, kind="review")
        await db.commit()
        job_id = job.id

    async with session_factory() as db:
        job = await jobs_repo.get_job(db, job_id)
        with pytest.raises(ValueError):
            await review_service.run_review(db, job)


async def test_run_review_raises_when_session_missing(session_factory):
    async with session_factory() as db:
        job = await jobs_repo.create_job(db, uuid.uuid4(), kind="review")
        await db.commit()
        job_id = job.id

    async with session_factory() as db:
        job = await jobs_repo.get_job(db, job_id)
        with pytest.raises(ValueError):
            await review_service.run_review(db, job)


async def test_review_report_markdown_is_rendered_by_us_not_the_llm(session_factory):
    """結構化路徑：LLM 只回結構化資料，Markdown 由我們自己排。

    這是重點——原本評分與分段全靠正則去撈 LLM 自由書寫的文字，模型某次沒照
    措辭寫就會靜默失效（審查頁評分空白、內容落到錯誤的區塊）。格式改由我們
    決定之後，前端的解析不會再因為模型措辭而壞掉。
    """
    import json

    tables = _existing_tables()
    async with session_factory() as db:
        session = await sessions_repo.create_session(
            db, mode="review", context_tables_json=[t.model_dump() for t in tables]
        )
        job = await jobs_repo.create_job(db, session.id, kind="review")
        await db.commit()
        session_id, job_id = session.id, job.id

    payload = json.dumps(
        {
            "score": 7.5,
            "summary": "整體結構堪用，但密碼欄位需要處理。",
            "consistency": ["users：命名一致 → 維持現狀"],
            "integrity": ["users（id）：缺少 NOT NULL → 補上約束"],
            "performance": [],
            "security": ["users（password）：疑似明文密碼 → 改存雜湊"],
        },
        ensure_ascii=False,
    )

    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.post("/chat/completions").mock(
            return_value=chat_completion_response(payload)
        )
        provider = make_provider()
        async with session_factory() as db:
            job = await jobs_repo.get_job(db, job_id)
            await review_service.run_review(db, job, provider=provider)
            await db.commit()

    assert route.call_count == 1, "結構化成功時不該有重試或降級的額外呼叫"

    async with session_factory() as db:
        report = (await outputs_repo.get_output(db, session_id, "05_review_report.md")).content

    # 前端 review.js 靠這兩件事分段與取分數
    assert "**整體評分：7.5/10**" in report
    for heading in ("## 1. 設計一致性", "## 2. 資料完整性", "## 3. 效能考量", "## 4. 安全性"):
        assert heading in report
    assert "- users（password）：疑似明文密碼 → 改存雜湊" in report
    # 空的維度要補一句，不能留白讓使用者以為漏掉了
    assert "未發現明顯問題。" in report


async def test_reviewer_instructions_travel_in_the_single_user_message():
    """reviewer.txt 同時是角色設定與四面向的內容規範，必須跟資料一起送在
    那唯一一則 user 訊息裡——放 system 的話 gateway 一忽略就整份規範消失。"""
    from app.services import writers

    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.post("/chat/completions").mock(
            return_value=chat_completion_response("## 1. 設計一致性\n- users：命名一致。")
        )
        await writers.review(make_provider(), _existing_tables())

    messages = json.loads(route.calls[0].request.content)["messages"]
    assert [m["role"] for m in messages] == ["user"]
    content = messages[0]["content"]
    assert "你是資深 PostgreSQL 資料庫架構師" in content
    assert "設計一致性" in content  # 四面向規範
    assert "資料表的規格（JSON）" in content
    assert "password" in content  # 受審的結構本身
