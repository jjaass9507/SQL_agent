from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ColumnSpec:
    name: str
    data_type: str
    nullable: bool
    description: str
    is_primary_key: bool = False
    is_foreign_key: bool = False
    references: Optional[str] = None  # "other_table.column"
    is_unique: bool = False
    is_indexed: bool = False
    length: Optional[int] = None
    default: Optional[str] = None


@dataclass
class TableSpec:
    table_name: str
    description: str
    columns: list[ColumnSpec]
    constraints: list[str] = field(default_factory=list)  # extra CHECK constraints
    related_tables: list[str] = field(default_factory=list)
