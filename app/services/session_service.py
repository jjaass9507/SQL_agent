"""Session 業務邏輯：建立（design/review）、confirm（原子防重複）、
版本 restore、匯入既有 DB 結構。純 Python，不 import FastAPI；
HTTP 例外轉換由 `app/api/routers/sessions.py` 負責（catch 這裡定義的例外）。
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.repos import activity as activity_repo
from app.repos import crypto
from app.repos import jobs as jobs_repo
from app.repos import sessions as sessions_repo
from app.repos import versions as versions_repo
from app.repos.models import Job, SchemaVersion, SessionRecord
from app.rules import db_introspect
from app.rules.spec_models import TableSpec
from app.services import dbops


class SessionNotFoundError(Exception):
    """指定的 session 不存在。"""


class DbConnectionError(Exception):
    """匯入既有 DB 結構時連線或查詢失敗（訊息不含 db_url 明文）。"""


class ConfirmConflictError(Exception):
    """confirm 時 session phase 不是 'confirming'（重複呼叫或尚未產出 tables）。"""


class VersionNotFoundError(Exception):
    """指定版本號不存在。"""


@dataclass
class SessionDetailData:
    """`get_session_detail()` 回傳的組裝結果，供 router 轉成 `SessionDetail` schema。"""

    session: SessionRecord
    latest_version: SchemaVersion | None
    jobs: list[Job]


async def list_sessions(db: AsyncSession) -> list[SessionRecord]:
    return await sessions_repo.list_sessions(db)


async def get_session_detail(db: AsyncSession, session_id: UUID) -> SessionDetailData | None:
    session = await sessions_repo.get_session(db, session_id)
    if session is None:
        return None
    latest_version = await versions_repo.get_latest_version(db, session_id)
    jobs = await jobs_repo.list_jobs(db, session_id)
    return SessionDetailData(session=session, latest_version=latest_version, jobs=jobs)


async def create_session(
    db: AsyncSession, *, title: str, mode: str, db_url: str | None
) -> SessionRecord:
    """建立新 session。review 模式：連線匯入既有結構、加密存放 db_url、建立 review job。"""
    if mode != "review":
        return await sessions_repo.create_session(db, title=title, mode="design")

    tables, err = await dbops.schema_tree(db_url)
    if err:
        raise DbConnectionError(err)

    session = await _persist_imported_schema(
        db, title=title, mode="review", db_url=db_url, tables=tables
    )
    session = await sessions_repo.update_session(db, session.id, phase="reviewing")
    await jobs_repo.create_job(
        db, session.id, kind="review", payload_json={"table_count": len(tables)}
    )
    await activity_repo.log_activity(
        db, "session.import_db", {"session_id": str(session.id), "table_count": len(tables)}
    )
    return session


async def _persist_imported_schema(
    db: AsyncSession, *, title: str, mode: str, db_url: str, tables: list[TableSpec]
) -> SessionRecord:
    context_text = db_introspect.format_context(tables)
    context_tables_json = [t.model_dump() for t in tables]
    encrypted = crypto.encrypt_db_url(db_url)
    return await sessions_repo.create_session(
        db,
        title=title,
        mode=mode,
        context_text=context_text,
        context_tables_json=context_tables_json,
        db_url_encrypted=encrypted,
    )


async def import_db(db: AsyncSession, session_id: UUID, db_url: str) -> SessionRecord:
    """既有 session 匯入（或重新匯入）業務資料庫結構，做為 Interviewer context。"""
    session = await sessions_repo.get_session(db, session_id)
    if session is None:
        raise SessionNotFoundError()

    tables, err = await dbops.schema_tree(db_url)
    if err:
        raise DbConnectionError(err)

    context_text = db_introspect.format_context(tables)
    context_tables_json = [t.model_dump() for t in tables]
    encrypted = crypto.encrypt_db_url(db_url)
    updated = await sessions_repo.update_session(
        db,
        session_id,
        context_text=context_text,
        context_tables_json=context_tables_json,
        db_url_encrypted=encrypted,
    )
    await activity_repo.log_activity(
        db, "session.import_db", {"session_id": str(session_id), "table_count": len(tables)}
    )
    return updated


async def confirm_session(db: AsyncSession, session_id: UUID) -> Job:
    """原子轉換 phase：confirming → generating，並建立 generate job。

    非 confirming 狀態（尚未產出 tables、或已 confirm 過）以帶 WHERE 條件的
    UPDATE 保證只有一個呼叫方能成功轉換；rowcount 為 0 時視為衝突（409）。
    """
    session = await sessions_repo.get_session(db, session_id)
    if session is None:
        raise SessionNotFoundError()

    latest_version = await versions_repo.get_latest_version(db, session_id)

    stmt = (
        update(SessionRecord)
        .where(SessionRecord.id == session_id, SessionRecord.phase == "confirming")
        .values(phase="generating")
    )
    result = await db.execute(stmt)
    if result.rowcount == 0:
        raise ConfirmConflictError()
    await db.flush()

    job = await jobs_repo.create_job(
        db,
        session_id,
        kind="generate",
        payload_json={
            "tables": latest_version.tables_json if latest_version else None,
            "key_points": latest_version.key_points_json if latest_version else None,
        },
    )
    await activity_repo.log_activity(db, "session.confirm", {"session_id": str(session_id)})
    return job


async def restore_version(db: AsyncSession, session_id: UUID, version_num: int) -> SchemaVersion:
    """把指定版本的內容複製成一個新版本（保留歷史），並把 session 轉回 confirming。"""
    session = await sessions_repo.get_session(db, session_id)
    if session is None:
        raise SessionNotFoundError()

    target = await versions_repo.get_version(db, session_id, version_num)
    if target is None:
        raise VersionNotFoundError()

    restored = await versions_repo.create_version(
        db, session_id, tables_json=target.tables_json, key_points_json=target.key_points_json
    )
    await sessions_repo.update_session(db, session_id, phase="confirming")
    await activity_repo.log_activity(
        db,
        "session.restore_version",
        {
            "session_id": str(session_id),
            "restored_from": version_num,
            "new_version": restored.version_num,
        },
    )
    return restored
