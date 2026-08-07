"""GET/PUT /workbench/dictionary：表與欄位的白話說明與負責人。

存在 app_settings 的單一 JSON，不新增資料表——這份內容是人工少量維護的註記。
"""

import pytest

from app.repos import settings as settings_repo
from app.repos.crypto import encrypt_db_url
from app.services.change_service import BUSINESS_DATABASES_KEY


@pytest.fixture
def register_business_db(db_session):
    async def _register(name="shop", url="sqlite://"):
        await settings_repo.set_setting(
            db_session,
            BUSINESS_DATABASES_KEY,
            [{"name": name, "db_url_encrypted": encrypt_db_url(url)}],
        )
        await db_session.commit()

    return _register


async def test_empty_dictionary(client, register_business_db):
    await register_business_db()
    resp = await client.get("/api/v1/workbench/dictionary?db_name=shop")
    assert resp.status_code == 200
    assert resp.json() == {}


async def test_upsert_and_read_back_table_note(client, register_business_db):
    await register_business_db()
    resp = await client.put(
        "/api/v1/workbench/dictionary",
        json={"db_name": "shop", "table": "orders", "note": "記錄每一筆訂單", "owner": "王"},
    )
    assert resp.status_code == 200

    entries = (await client.get("/api/v1/workbench/dictionary?db_name=shop")).json()
    assert entries["orders"]["note"] == "記錄每一筆訂單"
    assert entries["orders"]["owner"] == "王"
    assert entries["orders"]["updated_at"]


async def test_column_note_uses_separate_key(client, register_business_db):
    await register_business_db()
    await client.put(
        "/api/v1/workbench/dictionary",
        json={"db_name": "shop", "table": "orders", "note": "訂單主表"},
    )
    await client.put(
        "/api/v1/workbench/dictionary",
        json={"db_name": "shop", "table": "orders", "column": "通路", "note": "下單來源"},
    )
    entries = (await client.get("/api/v1/workbench/dictionary?db_name=shop")).json()
    assert entries["orders"]["note"] == "訂單主表"
    assert entries["orders|通路"]["note"] == "下單來源"


async def test_clearing_note_and_owner_deletes_entry(client, register_business_db):
    await register_business_db()
    await client.put(
        "/api/v1/workbench/dictionary",
        json={"db_name": "shop", "table": "orders", "note": "暫時的說明"},
    )
    await client.put(
        "/api/v1/workbench/dictionary",
        json={"db_name": "shop", "table": "orders", "note": "", "owner": ""},
    )
    assert (await client.get("/api/v1/workbench/dictionary?db_name=shop")).json() == {}


async def test_entries_are_scoped_per_database(client, register_business_db):
    """同名的表在不同資料庫是不同的東西，註記不能互相污染。"""
    await register_business_db()
    await client.put(
        "/api/v1/workbench/dictionary",
        json={"db_name": "shop", "table": "orders", "note": "商城訂單"},
    )
    await client.put(
        "/api/v1/workbench/dictionary",
        json={"db_name": "erp", "table": "orders", "note": "ERP 訂單"},
    )
    shop = (await client.get("/api/v1/workbench/dictionary?db_name=shop")).json()
    erp = (await client.get("/api/v1/workbench/dictionary?db_name=erp")).json()
    assert shop["orders"]["note"] == "商城訂單"
    assert erp["orders"]["note"] == "ERP 訂單"
