"""compute_diff：設計 vs 現有 DB 的欄位級差異，特別是「欄位名沒變但會出事」的變更。"""

from app.rules.schema_diff import compute_diff
from tests.specs import col, table


def _users(*, length=20, nullable=False):
    return table("users", "使用者", [col("email", "varchar", nullable, "信箱", length=length)])


def test_no_existing_tables_returns_none():
    assert compute_diff([_users()], []) is None


def test_identical_schema_is_unchanged():
    diff = compute_diff([_users()], [_users()])
    assert diff["has_changes"] is False
    assert diff["unchanged_tables"] == ["users"]


def test_length_shrink_is_flagged():
    """varchar(20) → varchar(10)：data_type 兩邊都是 varchar，只比型態會漏判。"""
    diff = compute_diff([_users(length=10)], [_users(length=20)])
    assert diff["has_changes"] is True
    assert "users" not in diff["unchanged_tables"]
    changed = diff["modified_tables"]["users"]["changed_columns"]
    assert changed[0]["name"] == "email"
    assert any("長度" in d for d in changed[0]["diffs"])


def test_nullable_tightening_is_flagged():
    diff = compute_diff([_users(nullable=False)], [_users(nullable=True)])
    changed = diff["modified_tables"]["users"]["changed_columns"]
    assert any("NOT NULL" in d for d in changed[0]["diffs"])


def test_new_and_dropped_tables():
    diff = compute_diff([table("orders", "", [])], [table("legacy", "", [])])
    assert diff["new_tables"] == ["orders"]
    assert diff["dropped_tables"] == ["legacy"]
