"""app/services/dbops.py 的單元測試（SQLite，不需外部資料庫）。"""

import pytest

from app.services import dbops


async def test_execute_query_returns_rows():
    result = await dbops.execute_query("sqlite://", "SELECT 1 AS a, 'x' AS b")
    assert result["columns"] == ["a", "b"]
    assert result["rows"] == [[1, "x"]]
    assert result["truncated"] is False


async def test_execute_query_rejects_dml():
    with pytest.raises(dbops.QueryRejected):
        await dbops.execute_query("sqlite://", "DELETE FROM t")


async def test_execute_query_rejects_stacked_statements():
    with pytest.raises(dbops.QueryRejected):
        await dbops.execute_query("sqlite://", "SELECT 1; SELECT 2")


async def test_execute_query_truncates_rows():
    sql = (
        "WITH RECURSIVE c(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM c WHERE n < 10) "
        "SELECT n FROM c"
    )
    result = await dbops.execute_query("sqlite://", sql, max_rows=5)
    assert len(result["rows"]) == 5
    assert result["truncated"] is True


async def test_explain_query():
    result = await dbops.explain_query("sqlite://", "SELECT 1")
    assert result["rows"]  # SQLite 的 EXPLAIN 至少回一列


def test_router_autodiscovery_finds_routers():
    """all_routers() 自動探索 routers/ 下每個模組的 module-level APIRouter。"""
    from fastapi import APIRouter

    from app.api import all_routers

    found = all_routers()
    assert found, "routers/ 已有路由模組，自動探索不應回空清單"
    assert all(isinstance(r, APIRouter) for r in found)
