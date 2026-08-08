"""Session 標籤與釘選（`docs/user_feature_backlog.md` 第二批）。

由來：後端工程師在驗收時點名這是「還沒做的裡面最痛的」——手上同時三四個案子，
首頁只能用名稱搜尋與狀態篩選，完全分不出哪個是哪個 PM 的需求，每天一開首頁
都要重新想「這個是哪個案子」。

儲存沿用 `AppSetting`（單一 JSON），不動 `models.py`——那個凍結公約尚未解除，
專案裡的 sticky 旗標與 agent session id 也都是這樣繞過的。標籤是每個 session
幾個字串的量級，不需要獨立資料表。
"""

import uuid

from app.repos import sessions as sessions_repo


async def _make_session(db_session, title="測試案子") -> uuid.UUID:
    record = await sessions_repo.create_session(db_session, title=title, mode="design")
    session_id = record.id
    await db_session.commit()
    return session_id


async def test_no_labels_by_default(client, session_factory):
    async with session_factory() as db:
        session_id = await _make_session(db)

    resp = await client.get(f"/api/v1/sessions/{session_id}")
    assert resp.status_code == 200
    assert resp.json()["tags"] == []
    assert resp.json()["pinned"] is False


async def test_tags_can_be_set_and_read_back(client, session_factory):
    async with session_factory() as db:
        session_id = await _make_session(db)

    resp = await client.put(
        f"/api/v1/sessions/{session_id}/labels", json={"tags": ["PM-陳", "急件"]}
    )
    assert resp.status_code == 200

    detail = (await client.get(f"/api/v1/sessions/{session_id}")).json()
    assert detail["tags"] == ["PM-陳", "急件"]


async def test_tags_appear_in_the_list_used_by_the_home_page(client, session_factory):
    """首頁清單要直接看得到標籤，否則還是得一個個點開。"""
    async with session_factory() as db:
        session_id = await _make_session(db, "訂單改版")

    await client.put(f"/api/v1/sessions/{session_id}/labels", json={"tags": ["PM-林"]})

    listed = (await client.get("/api/v1/sessions")).json()
    entry = next(s for s in listed if s["id"] == str(session_id))
    assert entry["tags"] == ["PM-林"]


async def test_pinning_moves_a_session_to_the_top(client, session_factory):
    """在忙的案子會被新建的其他案子擠到下面去。"""
    async with session_factory() as db:
        first = await _make_session(db, "舊案子")
        second = await _make_session(db, "新案子")

    await client.put(f"/api/v1/sessions/{first}/labels", json={"pinned": True})

    listed = (await client.get("/api/v1/sessions")).json()
    assert listed[0]["id"] == str(first), "釘選的案子要排在最前面"
    assert listed[0]["pinned"] is True
    assert any(s["id"] == str(second) for s in listed)


async def test_unpinning_restores_normal_order(client, session_factory):
    async with session_factory() as db:
        first = await _make_session(db, "舊案子")
        await _make_session(db, "新案子")

    await client.put(f"/api/v1/sessions/{first}/labels", json={"pinned": True})
    await client.put(f"/api/v1/sessions/{first}/labels", json={"pinned": False})

    listed = (await client.get("/api/v1/sessions")).json()
    assert listed[0]["id"] != str(first)


async def test_updating_tags_does_not_clear_pinned(client, session_factory):
    """只送 tags 時不該把釘選狀態一起洗掉。"""
    async with session_factory() as db:
        session_id = await _make_session(db)

    await client.put(f"/api/v1/sessions/{session_id}/labels", json={"pinned": True})
    await client.put(f"/api/v1/sessions/{session_id}/labels", json={"tags": ["PM-王"]})

    detail = (await client.get(f"/api/v1/sessions/{session_id}")).json()
    assert detail["pinned"] is True
    assert detail["tags"] == ["PM-王"]


async def test_tags_are_trimmed_and_deduplicated(client, session_factory):
    async with session_factory() as db:
        session_id = await _make_session(db)

    await client.put(
        f"/api/v1/sessions/{session_id}/labels",
        json={"tags": ["  PM-陳  ", "PM-陳", "", "急件"]},
    )

    detail = (await client.get(f"/api/v1/sessions/{session_id}")).json()
    assert detail["tags"] == ["PM-陳", "急件"]


async def test_labels_of_a_deleted_session_do_not_leak_into_the_list(client, session_factory):
    """刪除 session 後標籤不該留下孤兒資料。"""
    async with session_factory() as db:
        session_id = await _make_session(db)

    await client.put(f"/api/v1/sessions/{session_id}/labels", json={"tags": ["x"]})
    await client.delete(f"/api/v1/sessions/{session_id}")

    listed = (await client.get("/api/v1/sessions")).json()
    assert all(s["id"] != str(session_id) for s in listed)


async def test_labels_on_unknown_session_is_404(client):
    resp = await client.put(f"/api/v1/sessions/{uuid.uuid4()}/labels", json={"tags": ["x"]})
    assert resp.status_code == 404
