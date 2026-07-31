"""Pydantic v2 rewrite of v0.5's ``models/schema.py`` dataclasses.

Field names, ordering, and defaults are unchanged from the original
``ColumnSpec`` / ``TableSpec`` dataclasses so every module ported into
``app/rules/`` can keep using them without any logic changes.
"""
from pydantic import BaseModel, Field


class ColumnSpec(BaseModel):
    name: str
    data_type: str
    nullable: bool
    description: str
    is_primary_key: bool = False
    is_foreign_key: bool = False
    references: str | None = None  # "other_table.column"
    is_unique: bool = False
    is_indexed: bool = False
    length: int | None = None
    default: str | None = None


class TableSpec(BaseModel):
    table_name: str
    description: str
    columns: list[ColumnSpec]
    constraints: list[str] = Field(default_factory=list)  # extra CHECK constraints
    related_tables: list[str] = Field(default_factory=list)


def asdict(obj: BaseModel) -> dict:
    """Equivalent to ``dataclasses.asdict()`` for spec_models — recursively
    converts a ColumnSpec/TableSpec (or list thereof) into plain dict/list."""
    return obj.model_dump()


def tables_from_json(raw: list[dict]) -> list[TableSpec]:
    """Build a list of TableSpec from parsed JSON matching the shape produced
    by ``asdict()`` / the old ``dataclasses.asdict()`` (used by ddl_parser
    output, db_introspect output, and confirm-page JSON payloads)."""
    return [TableSpec(**t) for t in raw]
