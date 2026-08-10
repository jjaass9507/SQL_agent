"""Regression tests for PostgreSQL introspection query parameter binding."""

from app.rules.db_introspect import _COLS_QUERY, _TABLES_QUERY


def test_introspection_queries_are_valid_pyformat_with_mapping_parameters():
    """Literal percent signs must not be parsed as positional placeholders.

    psycopg2 uses ``pyformat`` parameters.  A raw ``%`` in a query that also
    receives a dict makes the driver treat the query as mixed positional and
    named parameters, which raises ``TypeError: dict is not a sequence``.
    """
    cols_sql = _COLS_QUERY % {"schema": "sales", "tables": ["orders"]}
    tables_sql = _TABLES_QUERY % {"schema": "sales", "name_contains": "order"}

    assert "NOT LIKE 'pg\\_%'" in cols_sql
    assert "NOT LIKE 'pg\\_%'" in tables_sql
    assert "ILIKE '%' || order || '%'" in tables_sql
