"""app/services/change_service.py 的單元測試：HITL 變更提案全生命週期。

psycopg2 一律以 `install_fake_psycopg2` 假模組替換（同 tests/rules/test_ddl_validator.py
的作法），不需要真實 PostgreSQL。
"""

import uuid

from app.repos import change_requests as change_requests_repo
from app.repos.crypto import encrypt_db_url
from app.services import change_service
from tests.agent.conftest import install_fake_psycopg2, seed_business_db

_DDL = "CREATE INDEX idx_users_name ON users(name);"


def _pid(result: dict) -> uuid.UUID:
    """`create_change_request()` 回傳的 proposal_id 是字串（JSON 友善給 LLM 看）；
    repo/service 的其他函式吃 uuid.UUID（FastAPI 路由層由 Pydantic 自動轉換，
    這裡是直接呼叫 service，需要手動轉回型別）。"""
    return uuid.UUID(result["proposal_id"])


# -- resolve_business_db ------------------------------------------------------


async def test_resolve_business_db_none_configured(db_session):
    name, url, err = await change_service.resolve_business_db(db_session, None)
    assert url is None
    assert err is not None


async def test_resolve_business_db_unknown_name(db_session):
    await seed_business_db(db_session, "shop", "postgresql://x/y")
    name, url, err = await change_service.resolve_business_db(db_session, "other")
    assert url is None
    assert err is not None


async def test_resolve_business_db_defaults_to_first(db_session):
    await seed_business_db(db_session, "shop", "postgresql://x/y")
    name, url, err = await change_service.resolve_business_db(db_session, None)
    assert err is None
    assert name == "shop"
    assert url == "postgresql://x/y"


# -- create_change_request -----------------------------------------------------


async def test_create_change_request_rejects_disallowed_ddl(db_session):
    result = await change_service.create_change_request(db_session, None, "DROP TABLE users;")
    assert "error" in result


async def test_create_change_request_no_business_db(db_session):
    result = await change_service.create_change_request(db_session, None, _DDL)
    assert "error" in result


async def test_create_change_request_dry_run_fails(db_session, monkeypatch):
    install_fake_psycopg2(monkeypatch, fail_on="CREATE INDEX")
    await seed_business_db(db_session, "shop", "postgresql://x/y")
    result = await change_service.create_change_request(db_session, "shop", _DDL)
    assert "error" in result
    assert "dry-run" in result["error"]


async def test_create_change_request_success(db_session, monkeypatch):
    install_fake_psycopg2(monkeypatch)
    await seed_business_db(db_session, "shop", "postgresql://x/y")
    result = await change_service.create_change_request(db_session, "shop", _DDL, "加速查詢")
    assert result["status"] == "pending"
    assert result["dry_run_ok"] is True
    assert result["db_name"] == "shop"

    record = await change_requests_repo.get_change_request(db_session, _pid(result))
    assert record is not None
    assert record.status == "pending"
    assert record.reason == "加速查詢"


# -- approve_change_request -----------------------------------------------------


async def test_approve_not_found(db_session):
    result = await change_service.approve_change_request(db_session, uuid.uuid4())
    assert result.get("not_found") is True


async def test_approve_success_executes_ddl(db_session, monkeypatch):
    conn = install_fake_psycopg2(monkeypatch)
    await seed_business_db(db_session, "shop", "postgresql://x/y")
    created = await change_service.create_change_request(db_session, "shop", _DDL)

    result = await change_service.approve_change_request(db_session, _pid(created))
    assert result["ok"] is True
    assert result["change_request"].status == "executed"
    assert conn.committed is True


async def test_approve_execution_failure_marks_failed(db_session, monkeypatch):
    await seed_business_db(db_session, "shop", "postgresql://x/y")
    # create 階段用一個不會失敗的假連線做 dry-run
    install_fake_psycopg2(monkeypatch)
    created = await change_service.create_change_request(db_session, "shop", _DDL)

    # approve 階段重新解析連線時，換一個「執行 DDL 會失敗」的假連線
    install_fake_psycopg2(monkeypatch, fail_on="CREATE INDEX")
    result = await change_service.approve_change_request(db_session, _pid(created))
    assert result["ok"] is False
    assert result["change_request"].status == "failed"
    assert result["change_request"].error


async def test_approve_non_pending_rejected(db_session, monkeypatch):
    install_fake_psycopg2(monkeypatch)
    await seed_business_db(db_session, "shop", "postgresql://x/y")
    created = await change_service.create_change_request(db_session, "shop", _DDL)
    await change_service.reject_change_request(db_session, _pid(created))

    result = await change_service.approve_change_request(db_session, _pid(created))
    assert "error" in result
    assert "change_request" not in result


# -- reject_change_request -------------------------------------------------------


async def test_reject_not_found(db_session):
    result = await change_service.reject_change_request(db_session, uuid.uuid4())
    assert result.get("not_found") is True


async def test_reject_success(db_session, monkeypatch):
    install_fake_psycopg2(monkeypatch)
    await seed_business_db(db_session, "shop", "postgresql://x/y")
    created = await change_service.create_change_request(db_session, "shop", _DDL)

    result = await change_service.reject_change_request(db_session, _pid(created))
    assert result["ok"] is True
    assert result["change_request"].status == "rejected"


async def test_reject_non_pending_rejected(db_session, monkeypatch):
    install_fake_psycopg2(monkeypatch)
    await seed_business_db(db_session, "shop", "postgresql://x/y")
    created = await change_service.create_change_request(db_session, "shop", _DDL)
    await change_service.reject_change_request(db_session, _pid(created))

    result = await change_service.reject_change_request(db_session, _pid(created))
    assert "error" in result


# -- serialize_change_request ----------------------------------------------------


async def test_serialize_change_request(db_session, monkeypatch):
    install_fake_psycopg2(monkeypatch)
    await seed_business_db(db_session, "shop", "postgresql://x/y")
    created = await change_service.create_change_request(db_session, "shop", _DDL)
    record = await change_requests_repo.get_change_request(db_session, _pid(created))

    data = change_service.serialize_change_request(record)
    assert data["id"] == str(record.id)
    assert data["status"] == "pending"
    assert data["ddl"] == _DDL


def test_encrypt_db_url_roundtrip_smoke():
    # 確認測試用的加密 helper 與 crypto 模組相容（非本模組邏輯，防止 fixture 本身出錯誤導測試）。
    from app.repos.crypto import decrypt_db_url

    token = encrypt_db_url("postgresql://user:pass@host/db")
    assert decrypt_db_url(token) == "postgresql://user:pass@host/db"
