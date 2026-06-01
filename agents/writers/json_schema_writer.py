"""Export the designed schema to JSON Schema (draft-07). Pure template, no LLM."""
import json

from models.schema import TableSpec

# Coarse PostgreSQL type → JSON Schema type mapping
_INT = ("int", "integer", "bigint", "smallint", "serial", "bigserial")
_NUM = ("decimal", "numeric", "real", "double", "float", "money")
_BOOL = ("bool", "boolean")


def _json_type(data_type: str) -> str:
    t = (data_type or "").lower()
    if any(k in t for k in _INT):
        return "integer"
    if any(k in t for k in _NUM):
        return "number"
    if any(k in t for k in _BOOL):
        return "boolean"
    return "string"


class JSONSchemaWriter:
    def generate(self, tables: list[TableSpec]) -> str:
        definitions = {}
        for t in tables:
            props = {}
            required = []
            for c in t.columns:
                prop = {"type": _json_type(c.data_type)}
                if c.description:
                    prop["description"] = c.description
                if c.length and prop["type"] == "string":
                    prop["maxLength"] = c.length
                props[c.name] = prop
                if not c.nullable:
                    required.append(c.name)
            schema = {"type": "object", "properties": props}
            if required:
                schema["required"] = required
            if t.description:
                schema["description"] = t.description
            definitions[t.table_name] = schema
        doc = {"$schema": "http://json-schema.org/draft-07/schema#", "definitions": definitions}
        return json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
