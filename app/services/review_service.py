"""審查服務：context_tables → Reviewer（LLM 四維度報告 + 評分）+ 規則式紅旗與修復 SQL。"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.provider import LLMProvider
from app.repos import outputs as outputs_repo
from app.repos import sessions as sessions_repo
from app.repos.models import Job
from app.rules import schema_advisor, schema_remediation
from app.rules.spec_models import tables_from_json
from app.services.writers.reviewer import Reviewer

logger = logging.getLogger(__name__)


async def run_review(db: AsyncSession, job: Job, *, provider: LLMProvider | None = None) -> None:
    """執行審查 job：
    - context_tables 取自 session.context_tables_json（匯入現有 DB 時已存入）。
    - Reviewer（LLM 單發）寫 05_review_report.md。
    - schema_advisor 紅旗 + schema_remediation 產 06_review_fix.sql（零 API）。
    - 完成後 session.phase → review_done。
    """
    session = await sessions_repo.get_session(db, job.session_id)
    if session is None:
        raise ValueError("session 不存在，無法執行審查")

    context_tables = tables_from_json(session.context_tables_json or [])
    if not context_tables:
        raise ValueError("session 缺少 context_tables，無法執行審查")

    provider = provider or LLMProvider.from_settings()
    report = await Reviewer(provider).review(context_tables)
    await outputs_repo.upsert_output(db, job.session_id, "05_review_report.md", report)

    warnings = schema_advisor.analyze(context_tables)
    fix_sql = schema_remediation.build_remediation_sql(warnings)
    await outputs_repo.upsert_output(db, job.session_id, "06_review_fix.sql", fix_sql)

    await sessions_repo.update_session(db, job.session_id, phase="review_done")
