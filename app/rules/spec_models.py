"""Pydantic v2 rewrite of v0.5's ``models/schema.py`` dataclasses.

Field names, ordering, and defaults are unchanged from the original
``ColumnSpec`` / ``TableSpec`` dataclasses so every module ported into
``app/rules/`` can keep using them without any logic changes. A small
``__init__`` override restores positional-argument construction (e.g.
``ColumnSpec("id", "uuid", False, "PK", is_primary_key=True)``), which the
ported v0.5 tests rely on and which Pydantic ``BaseModel`` does not support
out of the box.
"""
from pydantic import BaseModel, Field


class _PositionalModel(BaseModel):
    """BaseModel that also accepts positional args, in field-declaration order."""

    def __init__(self, *args, **kwargs):
        field_names = list(self.__class__.model_fields.keys())
        for field_name, value in zip(field_names, args, strict=False):
            kwargs.setdefault(field_name, value)
        super().__init__(**kwargs)


class ColumnSpec(_PositionalModel):
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


class TableSpec(_PositionalModel):
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
