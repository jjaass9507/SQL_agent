"""能力降級契約：平台探測出 gateway 缺什麼之後，每條 LLM 路徑都要照著降級。

這組測試的由來：實測發現 `POST /llm/diagnose` 正確測出 `system_role=False`、
DB Agent 也正確降級，但同一時間 NL2SQL 與訪談（平台的核心流程）直接回 HTTP 500
——因為只有 `agent_service` 記得把探測結果傳進 provider。

單元測試抓不到這件事：每個模組單獨看都沒錯，錯的是「有沒有人記得帶入」。
"""

import pytest

from app.repos import settings as settings_repo
from app.repos.crypto import encrypt_db_url
from app.services.change_service import BUSINESS_DATABASES_KEY


async def _register_db(db_session, name="shop", url="sqlite://"):
    await settings_repo.set_setting(
        db_session,
        BUSINESS_DATABASES_KEY,
        [{"name": name, "db_url_encrypted": encrypt_db_url(url)}],
    )
    await db_session.commit()


# ── 探針本身要測得準 ────────────────────────────────────────────────────


@pytest.mark.parametrize("gateway", ["full"], indirect=True)
async def test_probe_reports_all_capabilities_on_a_capable_gateway(client):
    probed = (await client.post("/api/v1/llm/diagnose")).json()["probed"]
    assert probed["multi_turn"] and probed["system_role"]
    assert probed["native_tools"] and probed["json_schema"]


@pytest.mark.parametrize("gateway", ["no_system"], indirect=True)
async def test_probe_detects_missing_system_role(client):
    probed = (await client.post("/api/v1/llm/diagnose")).json()["probed"]
    assert probed["system_role"] is False, "gateway 明確拒絕 system role，探針必須測得出來"


# ── 探測之後，每條路徑都要真的降級 ──────────────────────────────────────


@pytest.mark.parametrize("gateway", ["no_system"], indirect=True)
async def test_every_llm_path_survives_a_gateway_without_system_role(
    client, db_session, llm_env
):
    """這正是回歸的重點：不能只有 DB Agent 活著。"""
    await _register_db(db_session)
    await client.post("/api/v1/llm/diagnose")

    session_id = (
        await client.post("/api/v1/sessions", json={"mode": "design", "title": "降級測試"})
    ).json()["id"]

    paths = {
        "訪談": await client.post(
            f"/api/v1/sessions/{session_id}/messages", json={"content": "我要一張會員表"}
        ),
        "DB Agent": await client.post("/api/v1/agent/chat", json={"message": "哈囉"}),
        "NL2SQL": await client.post(
            "/api/v1/workbench/nl2sql", json={"question": "幾筆訂單", "db_name": "shop"}
        ),
    }
    broken = {name: r.status_code for name, r in paths.items() if r.status_code >= 500}
    assert not broken, f"以下路徑沒有依探測結果降級：{broken}"


@pytest.mark.parametrize("gateway", ["no_system"], indirect=True)
async def test_no_system_role_is_actually_sent_after_probing(client, db_session, llm_env):
    """降級的定義是「真的不再送 system role」，不是「送了但錯誤被吞掉」。"""
    await _register_db(db_session)
    await client.post("/api/v1/llm/diagnose")
    sent_before = llm_env.sent_system_role

    await client.post("/api/v1/workbench/nl2sql", json={"question": "幾筆訂單", "db_name": "shop"})

    assert llm_env.sent_system_role == sent_before, "探測後仍送出帶 system role 的請求"


@pytest.mark.parametrize("gateway", ["no_json"], indirect=True)
async def test_dirty_json_is_recovered(client, db_session, llm_env):
    """弱 gateway 常在 JSON 前後夾解釋文字，平台要能把它救回來。"""
    await _register_db(db_session)
    await client.post("/api/v1/llm/diagnose")

    resp = await client.post(
        "/api/v1/workbench/nl2sql", json={"question": "幾筆訂單", "db_name": "shop"}
    )
    assert resp.status_code < 500, "JSON 前後夾雜文字時不該炸掉"


@pytest.mark.parametrize("gateway", ["no_tools"], indirect=True)
async def test_agent_survives_a_gateway_without_native_tool_calls(client, llm_env):
    """免費模型常不支援原生 function calling，agent 迴圈不能因此掛掉。"""
    await client.post("/api/v1/llm/diagnose")
    resp = await client.post("/api/v1/agent/chat", json={"message": "有哪些資料庫"})
    assert resp.status_code < 500


# ── LLM 掛掉時給使用者看的東西 ──────────────────────────────────────────


@pytest.mark.parametrize("gateway", ["full"], indirect=True)
async def test_gateway_outage_is_not_a_bare_500(client, db_session, llm_env):
    """gateway 掛掉是常態（免費方案更是）。使用者不該看到 Internal Server Error。"""
    await _register_db(db_session)
    llm_env.stop()  # 模擬 gateway 中途掛掉

    resp = await client.post(
        "/api/v1/workbench/nl2sql", json={"question": "幾筆訂單", "db_name": "shop"}
    )
    assert resp.status_code == 503
    assert "Internal Server Error" not in resp.text
