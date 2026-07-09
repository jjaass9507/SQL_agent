"""DDL safety validation for Schema Chat.

Only a narrow allowlist of DDL operations is permitted — the rest is rejected
before any connection to the database is made.
"""
from web.sql_safety import check_ddl_allowlist


def check_ddl_safety(ddl: str) -> str | None:
    """Return an error string if the DDL is forbidden, else None.

    Checks:
    - Size limits
    - Each statement matches the allowlist
    - No forbidden keywords anywhere
    - No ALTER COLUMN
    """
    return check_ddl_allowlist(ddl)
