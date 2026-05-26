import json
from pathlib import Path
from models.schema import ColumnSpec, TableSpec
from utils.client import get_client, MODEL, MAX_TOKENS

SYSTEM_PROMPT = (Path(__file__).parent.parent / "prompts" / "interviewer.txt").read_text()

EXTRACT_TOOL = {
    "name": "extract_table_specs",
    "description": "當所有資料表的需求已完整收集，呼叫此工具輸出結構化規格。",
    "input_schema": {
        "type": "object",
        "properties": {
            "tables": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "table_name": {"type": "string"},
                        "description": {"type": "string"},
                        "columns": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "data_type": {"type": "string"},
                                    "length": {"type": ["integer", "null"]},
                                    "nullable": {"type": "boolean"},
                                    "default": {"type": ["string", "null"]},
                                    "description": {"type": "string"},
                                    "is_primary_key": {"type": "boolean"},
                                    "is_foreign_key": {"type": "boolean"},
                                    "references": {"type": ["string", "null"]},
                                    "is_unique": {"type": "boolean"},
                                    "is_indexed": {"type": "boolean"},
                                },
                                "required": ["name", "data_type", "nullable", "description",
                                             "is_primary_key", "is_foreign_key", "is_unique", "is_indexed"],
                            },
                        },
                        "constraints": {"type": "array", "items": {"type": "string"}},
                        "related_tables": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["table_name", "description", "columns"],
                },
            }
        },
        "required": ["tables"],
    },
}


def _parse_tables(raw: dict) -> list[TableSpec]:
    result = []
    for t in raw["tables"]:
        columns = [
            ColumnSpec(
                name=c["name"],
                data_type=c["data_type"],
                length=c.get("length"),
                nullable=c["nullable"],
                default=c.get("default"),
                description=c["description"],
                is_primary_key=c["is_primary_key"],
                is_foreign_key=c["is_foreign_key"],
                references=c.get("references"),
                is_unique=c["is_unique"],
                is_indexed=c["is_indexed"],
            )
            for c in t["columns"]
        ]
        result.append(TableSpec(
            table_name=t["table_name"],
            description=t["description"],
            columns=columns,
            constraints=t.get("constraints", []),
            related_tables=t.get("related_tables", []),
        ))
    return result


class Interviewer:
    def __init__(self):
        self._client = get_client()
        self._history: list[dict] = []

    def chat(self, user_message: str) -> tuple[str, list[TableSpec] | None]:
        """Send a message and get a response. Returns (text, tables) where tables
        is non-None only when requirements are complete."""
        self._history.append({"role": "user", "content": user_message})

        response = self._client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            tools=[EXTRACT_TOOL],
            messages=self._history,
        )

        # Handle tool_use block
        tables = None
        text_parts = []
        tool_use_block = None

        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use" and block.name == "extract_table_specs":
                tool_use_block = block
                tables = _parse_tables(block.input)

        text = "\n".join(text_parts).strip()

        # Add assistant turn to history
        self._history.append({"role": "assistant", "content": response.content})

        # If tool was used, add tool_result so the conversation can continue
        if tool_use_block:
            self._history.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": tool_use_block.id,
                    "content": json.dumps({"status": "ok"}, ensure_ascii=False),
                }],
            })

        return text, tables
