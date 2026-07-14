"""on-demand extras：orm/migration/query（LLM 單發）、incremental（diff 短路或 LLM）、
dbml/plantuml/jsonschema/datadict（純模板，零 API）。"""

import pytest
import respx

from app.rules.spec_models import ColumnSpec, TableSpec
from app.services import generation_service
from tests.workers.conftest import BASE_URL, chat_completion_response, make_provider


def _tables() -> list[TableSpec]:
    return [
        TableSpec(
            table_name="users",
            description="使用者",
            columns=[ColumnSpec("id", "uuid", False, "主鍵", is_primary_key=True)],
        )
    ]


@pytest.mark.parametrize(
    "kind,expected_filename",
    [
        ("orm", "orm_models.py"),
        ("migration", "alembic_migration.py"),
        ("query", "sample_queries.sql"),
    ],
)
async def test_llm_extra_kinds_call_provider_and_return_content(kind, expected_filename):
    assert generation_service.EXTRA_FILENAMES[kind] == expected_filename
    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.post("/chat/completions").mock(
            return_value=chat_completion_response("產出內容")
        )
        provider = make_provider()
        content = await generation_service.generate_extra(kind, _tables(), provider=provider)

    assert route.called
    assert content == "產出內容"


@pytest.mark.parametrize(
    "kind,expected_filename,expect_snippet",
    [
        ("dbml", "schema.dbml", "Table users"),
        ("plantuml", "schema.puml", "@startuml"),
        ("jsonschema", "schema_jsonschema.json", '"users"'),
        ("datadict", "data_dictionary.csv", "table,column"),
    ],
)
async def test_template_extra_kinds_make_zero_http_calls(kind, expected_filename, expect_snippet):
    assert generation_service.EXTRA_FILENAMES[kind] == expected_filename
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as mock:
        route = mock.post("/chat/completions").mock(
            return_value=chat_completion_response("不該被呼叫")
        )
        content = await generation_service.generate_extra(kind, _tables())

    assert route.call_count == 0
    assert expect_snippet in content


async def test_incremental_short_circuits_without_context_tables():
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as mock:
        route = mock.post("/chat/completions").mock(
            return_value=chat_completion_response("不該被呼叫")
        )
        content = await generation_service.generate_extra(
            "incremental", _tables(), context_tables=[]
        )

    assert route.call_count == 0
    assert "沒有匯入現有資料庫" in content


async def test_incremental_short_circuits_when_no_diff():
    tables = _tables()
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as mock:
        route = mock.post("/chat/completions").mock(
            return_value=chat_completion_response("不該被呼叫")
        )
        content = await generation_service.generate_extra(
            "incremental", tables, context_tables=tables
        )

    assert route.call_count == 0
    assert "一致" in content


async def test_incremental_calls_llm_when_diff_exists():
    designed = _tables()
    existing = [
        TableSpec(
            table_name="users",
            description="使用者",
            columns=[
                ColumnSpec("id", "uuid", False, "主鍵", is_primary_key=True),
                ColumnSpec("legacy_col", "text", True, "舊欄位（設計已移除）"),
            ],
        )
    ]
    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.post("/chat/completions").mock(
            return_value=chat_completion_response("ALTER TABLE users DROP COLUMN legacy_col;")
        )
        provider = make_provider()
        content = await generation_service.generate_extra(
            "incremental", designed, context_tables=existing, provider=provider
        )

    assert route.called
    assert content == "ALTER TABLE users DROP COLUMN legacy_col;"


async def test_unsupported_extra_kind_raises_value_error():
    with pytest.raises(ValueError):
        await generation_service.generate_extra("bogus", _tables())
