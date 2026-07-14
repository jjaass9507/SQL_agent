"""生成服務：設計模式四份核心文件並行產出 + on-demand 延伸產出。

四份核心文件（`generate_documents`）各自獨立更新 `jobs.progress_json`
（waiting → loading → done/failed），單檔失敗不影響其他文件；每份文件
產生後立即寫入 outputs repo。由於四份文件是用 `asyncio.gather` 平行執行，
DB 寫入一律各自開自己的 `AsyncSession`（`session_factory()`），
不共用同一個 AsyncSession（SQLAlchemy AsyncSession 不支援跨 coroutine 併發使用）。
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.llm.provider import LLMProvider
from app.repos import jobs as jobs_repo
from app.repos import outputs as outputs_repo
from app.repos.db import get_session_factory
from app.rules.spec_models import TableSpec
from app.rules.writers.data_dict_writer import DataDictWriter
from app.rules.writers.dbml_writer import DBMLWriter
from app.rules.writers.json_schema_writer import JSONSchemaWriter
from app.rules.writers.plantuml_writer import PlantUMLWriter
from app.rules.writers.spec_writer import SpecWriter
from app.services.writers.ddl_writer import DDLWriter
from app.services.writers.diagram_writer import DiagramWriter
from app.services.writers.incremental_migration_writer import IncrementalMigrationWriter
from app.services.writers.migration_writer import MigrationWriter
from app.services.writers.orm_writer import ORMWriter
from app.services.writers.query_writer import QueryWriter
from app.services.writers.security_writer import SecurityWriter

logger = logging.getLogger(__name__)

# 四份核心文件的固定檔名（設計模式）。
FILENAMES = ["01_specification.md", "02_er_diagram.md", "03_ddl.sql", "04_security_plan.md"]

# on-demand 延伸產出的 kind → 檔名對照。
EXTRA_FILENAMES = {
    "orm": "orm_models.py",
    "migration": "alembic_migration.py",
    "query": "sample_queries.sql",
    "incremental": "incremental_migration.sql",
    "dbml": "schema.dbml",
    "plantuml": "schema.puml",
    "jsonschema": "schema_jsonschema.json",
    "datadict": "data_dictionary.csv",
}

_TEMPLATE_WRITERS = {
    "dbml": DBMLWriter,
    "plantuml": PlantUMLWriter,
    "jsonschema": JSONSchemaWriter,
    "datadict": DataDictWriter,
}


async def _set_progress(
    session_factory: async_sessionmaker[AsyncSession],
    job_id: UUID,
    progress: dict[str, str],
    lock: asyncio.Lock,
) -> None:
    """把目前的 `progress` 快照寫入 DB。

    四份文件平行執行，`progress` 是它們共用的同一個 dict；`async with
    session_factory()` 這行本身就是一個 await 點，若不加鎖，甲文件在此
    讓出控制權的空檔，乙文件可能已經把自己的狀態改成 done 並先完成寫入，
    但甲稍後才真正送出的寫入仍是「乙尚未更新」的舊快照，會覆蓋掉乙剛寫入
    的最新狀態。用鎖把「取快照 → 寫 DB」序列化，確保寫入順序等於加鎖順序，
    後寫入的一定包含更新的資料，不會被較舊的快照覆蓋。
    """
    async with lock, session_factory() as db:
        await jobs_repo.update_job_progress(db, job_id, dict(progress))
        await db.commit()


async def _write_output(
    session_factory: async_sessionmaker[AsyncSession],
    session_id: UUID,
    filename: str,
    content: str,
) -> None:
    async with session_factory() as db:
        await outputs_repo.upsert_output(db, session_id, filename, content)
        await db.commit()


async def _run_document(
    filename: str,
    generate_fn: Callable[[], Awaitable[str]],
    *,
    session_id: UUID,
    job_id: UUID,
    session_factory: async_sessionmaker[AsyncSession],
    progress: dict[str, str],
    progress_lock: asyncio.Lock,
) -> str | None:
    """執行單一文件產出：waiting → loading → done/failed；失敗只記錄、不拋出（不影響其他文件）。"""
    progress[filename] = "loading"
    await _set_progress(session_factory, job_id, progress, progress_lock)
    try:
        content = await generate_fn()
    except Exception:
        # 注意：extra 的 key 不可用 "filename"（logging.LogRecord 內建屬性名衝突，
        # 會觸發 KeyError: Attempt to overwrite 'filename'）。
        logger.exception("generation_document_failed", extra={"output_filename": filename})
        progress[filename] = "failed"
        await _set_progress(session_factory, job_id, progress, progress_lock)
        return None

    await _write_output(session_factory, session_id, filename, content)
    progress[filename] = "done"
    await _set_progress(session_factory, job_id, progress, progress_lock)
    return content


async def _generate_spec(tables: list[TableSpec]) -> str:
    return SpecWriter().generate(tables)


async def generate_documents(
    job_id: UUID,
    session_id: UUID,
    tables: list[TableSpec],
    *,
    provider: LLMProvider | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> dict[str, str | None]:
    """並行產出四份核心文件（01 規格書零 API、02 ER 圖 Mermaid 確定性產生 + LLM 關聯說明、
    03 DDL、04 安全規劃），成果寫入 outputs repo，progress_json 逐步更新。"""
    provider = provider or LLMProvider.from_settings()
    session_factory = session_factory or get_session_factory()

    progress: dict[str, str] = dict.fromkeys(FILENAMES, "waiting")
    progress_lock = asyncio.Lock()
    await _set_progress(session_factory, job_id, progress, progress_lock)

    ddl_writer = DDLWriter(provider)
    diagram_writer = DiagramWriter(provider)
    security_writer = SecurityWriter(provider)

    generators: dict[str, Callable[[], Awaitable[str]]] = {
        "01_specification.md": lambda: _generate_spec(tables),
        "02_er_diagram.md": lambda: diagram_writer.generate(tables),
        "03_ddl.sql": lambda: ddl_writer.generate(tables),
        "04_security_plan.md": lambda: security_writer.generate(tables),
    }

    results = await asyncio.gather(
        *(
            _run_document(
                filename,
                generate_fn,
                session_id=session_id,
                job_id=job_id,
                session_factory=session_factory,
                progress=progress,
                progress_lock=progress_lock,
            )
            for filename, generate_fn in generators.items()
        )
    )
    return dict(zip(generators.keys(), results, strict=True))


async def generate_extra(
    kind: str,
    tables: list[TableSpec],
    *,
    context_tables: list[TableSpec] | None = None,
    provider: LLMProvider | None = None,
) -> str:
    """on-demand 延伸產出：
    - dbml/plantuml/jsonschema/datadict：純模板（零 API），不建立 LLMProvider。
    - orm/migration/query：LLM 單發。
    - incremental：需要 `context_tables`；`schema_diff` 算出差異後才呼叫 LLM 產 ALTER；
      沒有差異或沒有 context_tables 時短路，不呼叫 LLM。
    """
    if kind not in EXTRA_FILENAMES:
        raise ValueError(f"不支援的 extra kind：{kind}")

    template_cls = _TEMPLATE_WRITERS.get(kind)
    if template_cls is not None:
        return template_cls().generate(tables)

    provider = provider or LLMProvider.from_settings()
    context_tables = context_tables or []

    if kind == "orm":
        return await ORMWriter(provider).generate(tables)
    if kind == "migration":
        return await MigrationWriter(provider).generate(tables)
    if kind == "query":
        return await QueryWriter(provider).generate(tables)
    return await IncrementalMigrationWriter(provider).generate(tables, context_tables)
