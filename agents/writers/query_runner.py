"""QueryRunner writer: generates runnable example SELECT queries for the designed schema."""
import logging
from models.schema import TableSpec
from utils.client import get_api

logger = logging.getLogger(__name__)

PROMPT_TEMPLATE = """You are a PostgreSQL expert. Given the following database schema, generate 8–12 practical SELECT queries covering:
- Single-table lookups with WHERE filters
- JOINs between related tables
- Aggregations (COUNT, SUM, AVG, GROUP BY)
- Pagination (LIMIT/OFFSET)
- Date-range filtering

Schema:
{schema}

Output only valid SQL, each query preceded by a -- comment describing its purpose.
Separate queries with a blank line.
"""


class QueryRunner:
    def generate(self, tables: list[TableSpec]) -> str:
        schema_lines = []
        for t in tables:
            cols = ", ".join(
                f"{c.name} {c.data_type}{'(PK)' if c.is_primary_key else ''}{'(FK)' if c.is_foreign_key else ''}"
                for c in t.columns
            )
            schema_lines.append(f"- {t.table_name}: {cols}")
        schema_text = "\n".join(schema_lines)
        prompt = PROMPT_TEMPLATE.format(schema=schema_text)
        api = get_api()
        response = api.chat(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2000,
        )
        return response.strip()
