"""Export the designed schema as a data-dictionary CSV. Pure template, no LLM."""
import csv
import io

from models.schema import TableSpec

_HEADER = ["table", "column", "data_type", "length", "nullable", "default",
           "pk", "fk", "references", "unique", "indexed", "description"]


def _yn(v: bool) -> str:
    return "Y" if v else ""


class DataDictWriter:
    def generate(self, tables: list[TableSpec]) -> str:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(_HEADER)
        for t in tables:
            for c in t.columns:
                writer.writerow([
                    t.table_name, c.name, c.data_type, c.length or "",
                    _yn(c.nullable), c.default or "",
                    _yn(c.is_primary_key), _yn(c.is_foreign_key), c.references or "",
                    _yn(c.is_unique), _yn(c.is_indexed), c.description or "",
                ])
        return buf.getvalue()
