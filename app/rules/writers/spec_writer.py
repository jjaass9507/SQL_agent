from app.rules.spec_models import TableSpec


class SpecWriter:
    """Renders database specification doc from TableSpec — no LLM call needed."""

    def generate(self, tables: list[TableSpec]) -> str:
        sections = ["# 資料庫規格書與資料字典\n"]
        for t in tables:
            sections.append(f"## 資料表：`{t.table_name}`\n")
            sections.append(f"**說明**：{t.description}\n")
            sections.append(
                "| 欄位名稱 | 資料型態 | 長度 | 允許 NULL | 預設值 | PK | FK | UNIQUE "
                "| INDEX | 說明 |\n"
                "|----------|----------|------|-----------|--------|----|----|--------"
                "|-------|------|\n"
            )
            for c in t.columns:
                if c.references:
                    ref = c.references
                    if isinstance(ref, dict):
                        ref = f"{ref.get('table', '')}.{ref.get('column', '')}"
                    fk_ref = f"→ {ref}"
                else:
                    fk_ref = ""
                sections.append(
                    f"| `{c.name}` | {c.data_type} | {c.length or ''} "
                    f"| {'是' if c.nullable else '否'} "
                    f"| {c.default or ''} "
                    f"| {'✓' if c.is_primary_key else ''} "
                    f"| {'✓' if c.is_foreign_key else ''} {fk_ref} "
                    f"| {'✓' if c.is_unique else ''} "
                    f"| {'✓' if c.is_indexed else ''} "
                    f"| {c.description} |\n"
                )
            if t.constraints:
                sections.append("\n**額外約束**：\n")
                for ct in t.constraints:
                    sections.append(f"- `{ct}`\n")
            sections.append("\n")
        return "".join(sections)
