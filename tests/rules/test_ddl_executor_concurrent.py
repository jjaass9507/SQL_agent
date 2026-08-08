"""`CREATE INDEX CONCURRENTLY` 必須跑在交易之外。

由來（`docs/feature_backlog.md` 2-1）：`execute_ddl` 把所有語句包在單一交易裡，
而 PostgreSQL 規定 `CREATE INDEX CONCURRENTLY` 不能在交易區塊內執行。也就是說
這個系統物理上只能用「會鎖表」的方式建索引——大表拿到 ACCESS EXCLUSIVE 鎖，
期間所有讀寫全部卡住。

這是三方討論裡唯一「照著 HITL 流程一步不差走完、沒有任何繞過或惡意行為，
依然會出事」的情境。

這裡用純函式層級的測試（不需要真的 PostgreSQL）：驗證語句怎麼被切分、
哪些會被送進交易、哪些會被單獨以 autocommit 執行。
"""

import pytest

from app.rules import ddl_executor


def test_plain_ddl_all_goes_into_one_transaction():
    ddl = "CREATE TABLE a (id int); ALTER TABLE b ADD COLUMN c int;"
    transactional, concurrent = ddl_executor.split_by_concurrency(ddl)
    assert len(transactional) == 2
    assert concurrent == []


def test_concurrent_index_is_separated_out():
    """CONCURRENTLY 的語句不能跟其他語句共用交易。"""
    ddl = (
        "ALTER TABLE orders ADD COLUMN note text;\n"
        "CREATE INDEX CONCURRENTLY idx_orders_note ON orders (note);"
    )
    transactional, concurrent = ddl_executor.split_by_concurrency(ddl)
    assert len(transactional) == 1 and "ADD COLUMN" in transactional[0]
    assert len(concurrent) == 1 and "CONCURRENTLY" in concurrent[0]


def test_concurrency_detection_is_case_insensitive():
    ddl = "create index concurrently idx ON t (c);"
    _, concurrent = ddl_executor.split_by_concurrency(ddl)
    assert len(concurrent) == 1


def test_the_word_in_a_string_literal_does_not_count():
    """字串裡的 concurrently 不是語法關鍵字，不該被誤判成需要獨立交易。"""
    ddl = "COMMENT ON TABLE orders IS 'built concurrently by the team';"
    transactional, concurrent = ddl_executor.split_by_concurrency(ddl)
    assert len(transactional) == 1
    assert concurrent == []


def test_the_word_in_a_comment_does_not_count():
    ddl = "-- create index concurrently later\nCREATE TABLE a (id int);"
    transactional, concurrent = ddl_executor.split_by_concurrency(ddl)
    assert len(transactional) == 1
    assert concurrent == []


# ── 產出給人看的風險提示 ────────────────────────────────────────────────


def test_non_concurrent_index_is_flagged_as_locking():
    """核准的人要知道這一句會鎖表——dry-run 跑在空的暫存 schema，秒過，測不到。"""
    warnings = ddl_executor.locking_warnings("CREATE INDEX idx_orders_note ON orders (note);")
    assert warnings
    assert "CONCURRENTLY" in warnings[0]


def test_concurrent_index_is_not_flagged():
    warnings = ddl_executor.locking_warnings(
        "CREATE INDEX CONCURRENTLY idx_orders_note ON orders (note);"
    )
    assert warnings == []


def test_plain_create_table_is_not_flagged():
    """新建的表沒有人在用，建索引不會擋到任何人。"""
    assert ddl_executor.locking_warnings("CREATE TABLE a (id int);") == []


@pytest.mark.parametrize(
    "ddl",
    [
        "CREATE UNIQUE INDEX idx ON orders (email);",
        "create index idx on orders (email);",
    ],
)
def test_all_non_concurrent_index_forms_are_flagged(ddl):
    assert ddl_executor.locking_warnings(ddl)
