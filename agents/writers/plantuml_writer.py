"""Export the designed schema to a PlantUML ER diagram. Pure template, no LLM."""
from models.schema import TableSpec


def _type(c) -> str:
    return f"{c.data_type}({c.length})" if c.length else c.data_type


class PlantUMLWriter:
    def generate(self, tables: list[TableSpec]) -> str:
        names = {t.table_name for t in tables}
        lines = ["@startuml", "hide circle", "skinparam linetype ortho", ""]
        relations: list[str] = []
        for t in tables:
            lines.append(f"entity {t.table_name} {{")
            keys = [c for c in t.columns if c.is_primary_key]
            others = [c for c in t.columns if not c.is_primary_key]
            for c in keys:
                lines.append(f"  * {c.name} : {_type(c)} <<PK>>")
            if keys and others:
                lines.append("  --")
            for c in others:
                flag = " <<FK>>" if c.is_foreign_key else ""
                nn = "* " if not c.nullable else ""
                lines.append(f"  {nn}{c.name} : {_type(c)}{flag}")
            lines.append("}")
            lines.append("")
            for c in t.columns:
                if c.is_foreign_key and c.references and "." in c.references:
                    target = c.references.split(".")[0].strip()
                    if target in names:
                        relations.append(f"{target} ||--o{{ {t.table_name}")
        lines.extend(relations)
        lines.append("@enduml")
        return "\n".join(lines) + "\n"
