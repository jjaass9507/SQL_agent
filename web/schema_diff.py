"""Compare AI-designed tables against existing DB tables."""
from models.schema import TableSpec


def compute_diff(designed: list[TableSpec], existing: list[TableSpec]) -> dict | None:
    """Return a diff dict, or None if there are no existing tables to compare against."""
    if not existing:
        return None

    designed_map = {t.table_name: t for t in designed}
    existing_map = {t.table_name: t for t in existing}

    new_tables = [name for name in designed_map if name not in existing_map]
    dropped_tables = [name for name in existing_map if name not in designed_map]
    modified_tables: dict[str, dict] = {}
    unchanged_tables: list[str] = []

    for name, d_tbl in designed_map.items():
        if name not in existing_map:
            continue
        e_tbl = existing_map[name]
        d_cols = {c.name: c for c in d_tbl.columns}
        e_cols = {c.name: c for c in e_tbl.columns}

        added = [{"name": c.name, "data_type": c.data_type}
                 for c in d_tbl.columns if c.name not in e_cols]
        removed = [{"name": c.name, "data_type": c.data_type}
                   for c in e_tbl.columns if c.name not in d_cols]
        changed = []
        for col_name, dc in d_cols.items():
            if col_name not in e_cols:
                continue
            ec = e_cols[col_name]
            diffs = []
            if dc.data_type != ec.data_type:
                diffs.append(f"型態：{ec.data_type} → {dc.data_type}")
            if dc.nullable != ec.nullable:
                before = "允許 NULL" if ec.nullable else "NOT NULL"
                after = "允許 NULL" if dc.nullable else "NOT NULL"
                diffs.append(f"{before} → {after}")
            if not ec.is_unique and dc.is_unique:
                diffs.append("新增 UNIQUE")
            if not ec.is_indexed and dc.is_indexed:
                diffs.append("新增索引")
            if diffs:
                changed.append({"name": col_name, "diffs": diffs})

        if added or removed or changed:
            modified_tables[name] = {
                "added_columns": added,
                "removed_columns": removed,
                "changed_columns": changed,
            }
        else:
            unchanged_tables.append(name)

    return {
        "has_changes": bool(new_tables or dropped_tables or modified_tables),
        "new_tables": new_tables,
        "dropped_tables": dropped_tables,
        "modified_tables": modified_tables,
        "unchanged_tables": unchanged_tables,
    }
