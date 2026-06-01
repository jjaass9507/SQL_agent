"""Export the designed schema to dbdiagram.io DBML. Pure template, no LLM."""
from models.schema import TableSpec


def _type(c) -> str:
    return f"{c.data_type}({c.length})" if c.length else c.data_type


def _ref_parts(references: str):
    if not references or "." not in references:
        return None
    table, _, column = references.partition(".")
    return table.strip(), column.strip()


class DBMLWriter:
    def generate(self, tables: list[TableSpec]) -> str:
        lines: list[str] = []
        refs: list[str] = []
        for t in tables:
            lines.append(f"Table {t.table_name} {{")
            for c in t.columns:
                settings = []
                if c.is_primary_key:
                    settings.append("pk")
                if c.is_unique:
                    settings.append("unique")
                if not c.nullable:
                    settings.append("not null")
                if c.default:
                    settings.append(f"default: `{c.default}`")
                if c.description:
                    note = c.description.replace("'", "")
                    settings.append(f"note: '{note}'")
                setting_str = f" [{', '.join(settings)}]" if settings else ""
                lines.append(f"  {c.name} {_type(c)}{setting_str}")
                if c.is_foreign_key and c.references:
                    parts = _ref_parts(c.references)
                    if parts:
                        refs.append(f"Ref: {t.table_name}.{c.name} > {parts[0]}.{parts[1]}")
            lines.append("}\n")
        if refs:
            lines.append("\n".join(refs))
        return "\n".join(lines).rstrip() + "\n"
