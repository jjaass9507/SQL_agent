"""job handler：依 `job.kind` 分派給對應 service 執行；job 狀態機（claim/finish）交給 runner.py。

handler 內若拋出例外，代表整個 job 失敗（例如 payload 缺欄位），由 runner 捕捉、
寫入 `job.error`。單一文件產出失敗（`generate` job 內某份文件的 LLM 呼叫失敗）
由 `generation_service` 內部吸收，不會傳到這裡、不影響整個 job 的成敗。

`session_factory` 由 runner 傳入：`generate` handler 需要它交給
`generation_service.generate_documents()` 開平行的獨立 AsyncSession
（見該檔 docstring）；review/extra 只用單一 `db` 寫入，用不到但仍需接受
這個參數以維持三個 handler 一致的呼叫介面（`_HANDLERS` dict 統一分派）。
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.repos import outputs as outputs_repo
from app.repos.models import Job
from app.rules.spec_models import tables_from_json
from app.services import generation_service, review_service


async def handle_generate_job(
    db: AsyncSession, job: Job, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """kind="generate"：payload 含 `tables`（confirm 時的 TableSpec JSON），
    並行產出四份核心文件。"""
    payload = job.payload_json or {}
    tables_raw = payload.get("tables")
    if not tables_raw:
        raise ValueError("generate job payload 缺少 tables")
    tables = tables_from_json(tables_raw)
    await generation_service.generate_documents(
        job.id, job.session_id, tables, session_factory=session_factory
    )


async def handle_review_job(
    db: AsyncSession, job: Job, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """kind="review"：context_tables 取自 session，交由 review_service 處理。"""
    await review_service.run_review(db, job)


async def handle_extra_job(
    db: AsyncSession, job: Job, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """kind="extra"：payload 含 `kind`（延伸產出種類）與 `tables`；
    incremental 另需 `context_tables`。"""
    payload = job.payload_json or {}
    kind = payload.get("kind")
    if not kind:
        raise ValueError("extra job payload 缺少 kind")
    tables_raw = payload.get("tables")
    if not tables_raw:
        raise ValueError("extra job payload 缺少 tables")
    tables = tables_from_json(tables_raw)
    context_tables = tables_from_json(payload.get("context_tables") or [])

    content = await generation_service.generate_extra(kind, tables, context_tables=context_tables)
    filename = generation_service.EXTRA_FILENAMES[kind]
    await outputs_repo.upsert_output(db, job.session_id, filename, content)
