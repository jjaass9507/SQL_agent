"""常用問題清單與「已核可」標記（`docs/user_feature_backlog.md` 第二批）。

由來：業務單位每個月要交固定幾份報表，每次都得重新描述一次同樣的問題。
DBA 在驗收時明確要求這一項「不得無限期擱置」——否則結果旁固定顯示的
「尚未經人工覆核」警示會變成大家視而不見的裝飾。

討論中長出來的第二個要求：**核可會過期**。問題今天核可了，底層結構或口徑
之後被改掉，提問的人完全不會發現，還會繼續拿舊口徑的數字去交報表。
因此每項要記「上次確認口徑的日期」，過久要變警示色。
"""

import pytest

from app.repos import settings as settings_repo
from app.repos.crypto import encrypt_db_url
from app.services.change_service import BUSINESS_DATABASES_KEY


@pytest.fixture
def register_db(db_session):
    async def _register(name="shop", url="sqlite://"):
        await settings_repo.set_setting(
            db_session,
            BUSINESS_DATABASES_KEY,
            [{"name": name, "db_url_encrypted": encrypt_db_url(url)}],
        )
        await db_session.commit()

    return _register


async def test_empty_by_default(client, register_db):
    await register_db()
    resp = await client.get("/api/v1/workbench/saved-questions?db_name=shop")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_save_and_list(client, register_db):
    await register_db()
    resp = await client.post(
        "/api/v1/workbench/saved-questions",
        json={"db_name": "shop", "question": "上月退貨率", "sql": "SELECT 1"},
    )
    assert resp.status_code == 200

    saved = (await client.get("/api/v1/workbench/saved-questions?db_name=shop")).json()
    assert len(saved) == 1
    assert saved[0]["question"] == "上月退貨率"
    assert saved[0]["sql"] == "SELECT 1"


async def test_newly_saved_question_is_not_approved(client, register_db):
    """第一次存下來時還沒有人覆核過口徑——預設必須是未核可。"""
    await register_db()
    await client.post(
        "/api/v1/workbench/saved-questions",
        json={"db_name": "shop", "question": "上月退貨率", "sql": "SELECT 1"},
    )
    saved = (await client.get("/api/v1/workbench/saved-questions?db_name=shop")).json()
    assert saved[0]["approved"] is False
    assert saved[0]["approved_by"] is None


async def test_approving_records_who_and_when(client, register_db):
    """「已核可」要看得出是誰確認的、什麼時候確認的——否則無從判斷還算不算數。"""
    await register_db()
    created = (
        await client.post(
            "/api/v1/workbench/saved-questions",
            json={"db_name": "shop", "question": "上月退貨率", "sql": "SELECT 1"},
        )
    ).json()

    resp = await client.post(
        f"/api/v1/workbench/saved-questions/{created['id']}/approve",
        json={"db_name": "shop"},
        headers={"X-Admin-Token": "test-admin-token"},
    )
    assert resp.status_code == 200

    saved = (await client.get("/api/v1/workbench/saved-questions?db_name=shop")).json()
    assert saved[0]["approved"] is True
    assert saved[0]["approved_by"]
    assert saved[0]["approved_at"]


async def test_editing_the_sql_revokes_approval(client, register_db):
    """改了語法就等於換了口徑，先前的核可不能繼續掛著。"""
    await register_db()
    created = (
        await client.post(
            "/api/v1/workbench/saved-questions",
            json={"db_name": "shop", "question": "上月退貨率", "sql": "SELECT 1"},
        )
    ).json()
    await client.post(
        f"/api/v1/workbench/saved-questions/{created['id']}/approve",
        json={"db_name": "shop"},
        headers={"X-Admin-Token": "test-admin-token"},
    )

    await client.post(
        "/api/v1/workbench/saved-questions",
        json={
            "id": created["id"],
            "db_name": "shop",
            "question": "上月退貨率",
            "sql": "SELECT 2",
        },
    )

    saved = (await client.get("/api/v1/workbench/saved-questions?db_name=shop")).json()
    assert saved[0]["sql"] == "SELECT 2"
    assert saved[0]["approved"] is False, "口徑改了，核可必須失效"


async def test_approval_goes_stale_after_a_while(client, register_db, monkeypatch):
    """核可會過期：底層結構或口徑之後被改掉，提問的人不會自己發現。"""
    from app.services import saved_questions

    await register_db()
    created = (
        await client.post(
            "/api/v1/workbench/saved-questions",
            json={"db_name": "shop", "question": "上月退貨率", "sql": "SELECT 1"},
        )
    ).json()
    await client.post(
        f"/api/v1/workbench/saved-questions/{created['id']}/approve",
        json={"db_name": "shop"},
        headers={"X-Admin-Token": "test-admin-token"},
    )

    saved = (await client.get("/api/v1/workbench/saved-questions?db_name=shop")).json()
    assert saved[0]["stale"] is False, "剛核可完不該是過期的"

    monkeypatch.setattr(saved_questions, "APPROVAL_VALID_DAYS", 0)
    saved = (await client.get("/api/v1/workbench/saved-questions?db_name=shop")).json()
    assert saved[0]["stale"] is True, "超過有效期要標成需要重新確認"


async def test_delete(client, register_db):
    await register_db()
    created = (
        await client.post(
            "/api/v1/workbench/saved-questions",
            json={"db_name": "shop", "question": "上月退貨率", "sql": "SELECT 1"},
        )
    ).json()

    resp = await client.delete(
        f"/api/v1/workbench/saved-questions/{created['id']}?db_name=shop"
    )
    assert resp.status_code == 200
    assert (await client.get("/api/v1/workbench/saved-questions?db_name=shop")).json() == []


async def test_questions_are_scoped_per_database(client, register_db):
    await register_db()
    await client.post(
        "/api/v1/workbench/saved-questions",
        json={"db_name": "shop", "question": "商城退貨率", "sql": "SELECT 1"},
    )
    await client.post(
        "/api/v1/workbench/saved-questions",
        json={"db_name": "erp", "question": "ERP 退貨率", "sql": "SELECT 1"},
    )

    shop = (await client.get("/api/v1/workbench/saved-questions?db_name=shop")).json()
    erp = (await client.get("/api/v1/workbench/saved-questions?db_name=erp")).json()
    assert [q["question"] for q in shop] == ["商城退貨率"]
    assert [q["question"] for q in erp] == ["ERP 退貨率"]


async def test_approve_requires_admin(client, register_db, monkeypatch):
    """核可是「我確認過這個口徑」的背書，不能人人都能按。"""
    await register_db()
    created = (
        await client.post(
            "/api/v1/workbench/saved-questions",
            json={"db_name": "shop", "question": "上月退貨率", "sql": "SELECT 1"},
        )
    ).json()

    resp = await client.post(
        f"/api/v1/workbench/saved-questions/{created['id']}/approve",
        json={"db_name": "shop"},
    )
    assert resp.status_code in (401, 403)
