"""writers 的產出防護：模型複述輸入 JSON 時不把它寫進文件，以及 prompt 排版。"""

import json

import respx

from app.rules.spec_models import TableSpec
from app.services.writers import write
from app.services.writers._common import tables_prompt, task_instructions
from app.services.writers.diagram_writer import DiagramWriter
from app.services.writers.security_writer import SecurityWriter
from tests.specs import col
from tests.workers.conftest import BASE_URL, chat_completion_response, make_provider

_ECHOED_JSON = json.dumps(
    [{"table_name": "equipment", "description": "設備主檔", "columns": []}], ensure_ascii=False
)


def _tables() -> list[TableSpec]:
    return [
        TableSpec(
            table_name="equipment",
            description="設備主檔",
            columns=[col("id", "varchar", False, "設備編號", is_primary_key=True)],
        )
    ]


async def test_diagram_drops_echoed_json_and_keeps_the_deterministic_diagram():
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/chat/completions").mock(return_value=chat_completion_response(_ECHOED_JSON))
        content = await DiagramWriter(make_provider()).generate(_tables())

    assert "table_name" not in content  # 複述的 JSON 不進文件
    assert "```mermaid" in content  # 圖是確定性產生的，照樣留著
    assert "equipment" in content


async def test_security_plan_says_it_failed_instead_of_dumping_json():
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/chat/completions").mock(return_value=chat_completion_response(_ECHOED_JSON))
        content = await SecurityWriter(make_provider()).generate(_tables())

    assert "table_name" not in content
    assert "產出失敗" in content


async def test_ddl_falls_back_when_model_echoes_json_in_a_code_fence():
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/chat/completions").mock(
            return_value=chat_completion_response(f"```json\n{_ECHOED_JSON}\n```")
        )
        content = await write(make_provider(), "ddl", _tables())

    assert "table_name" not in content


async def test_normal_prose_response_is_not_mistaken_for_echoed_json():
    with respx.mock(base_url=BASE_URL) as mock:
        prose = "設備主檔以 equipment_id 為主鍵，目前沒有外鍵關聯。"
        mock.post("/chat/completions").mock(return_value=chat_completion_response(prose))
        content = await DiagramWriter(make_provider()).generate(_tables())

    assert "設備主檔以 equipment_id 為主鍵" in content


def test_prompt_puts_instructions_first_and_reminds_at_the_end():
    prompt = tables_prompt(task_instructions("diagram"), _tables())

    assert prompt.startswith("你是資料庫架構師")
    assert prompt.index("關聯設計決策") < prompt.index("以下是 1 張資料表的規格")
    assert prompt.rstrip().endswith("不要複製、改寫或輸出上面那份 JSON 資料本身。")
