"""Tests for review-mode generation: rule-based flags + remediation are attached."""
import dataclasses

import pytest

from models.schema import ColumnSpec, TableSpec


@pytest.fixture(autouse=True)
def _isolate_data(tmp_path, monkeypatch):
    import web.session_store as ss
    monkeypatch.setattr(ss, "DATA_DIR", tmp_path)


def _table_with_issues():
    return TableSpec("orders", "", [
        ColumnSpec("user_id", "UUID", False, "", is_foreign_key=True, references="users.id"),
    ])


def test_review_attaches_warnings_and_fix(monkeypatch):
    import web.generation_worker as gw
    from web.session_store import create_session, get_session

    # Avoid real LLM call
    class FakeReviewer:
        def review(self, tables):
            return "# 報告\n整體評分：7/10"
    monkeypatch.setattr("agents.reviewer.Reviewer", FakeReviewer)

    ctx = [dataclasses.asdict(_table_with_issues())]
    session = create_session("review", context_tables=ctx, mode="review")

    gw._review(session["id"])  # run synchronously

    out = get_session(session["id"])
    assert out["phase"] == "review_done"
    assert "05_review_report.md" in out["outputs"]
    # FK without index + no PK + missing audit are flagged
    codes = {w["code"] for w in out["review_warnings"]}
    assert "fk_no_index" in codes
    # Remediation SQL produced for the runnable fixes
    assert "06_review_fix.sql" in out["outputs"]
    assert "CREATE INDEX" in out["outputs"]["06_review_fix.sql"]


def test_review_no_context_tables(monkeypatch):
    import web.generation_worker as gw
    from web.session_store import create_session, get_session

    session = create_session("empty", context_tables=[], mode="review")
    gw._review(session["id"])
    out = get_session(session["id"])
    assert out["phase"] == "review_done"
    assert "未匯入" in out["outputs"]["05_review_report.md"]
