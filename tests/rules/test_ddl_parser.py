"""Unit tests for app/rules/ddl_parser.py."""
from app.rules.ddl_parser import parse_ddl


def test_parse_simple_table():
    tables = parse_ddl("CREATE TABLE users (id serial PRIMARY KEY, email varchar(255) NOT NULL);")
    assert len(tables) == 1
    t = tables[0]
    assert t.table_name == "users"
    assert len(t.columns) == 2
    id_col = next(c for c in t.columns if c.name == "id")
    assert id_col.is_primary_key is True
    assert id_col.data_type == "serial"
    email_col = next(c for c in t.columns if c.name == "email")
    assert email_col.nullable is False
    assert email_col.length == 255


def test_parse_multiple_tables_with_fk():
    ddl = """
    CREATE TABLE users (id serial PRIMARY KEY, name text NOT NULL);
    CREATE TABLE posts (id serial PRIMARY KEY, user_id integer REFERENCES users(id), title text);
    """
    tables = parse_ddl(ddl)
    assert {t.table_name for t in tables} == {"users", "posts"}
    posts = next(t for t in tables if t.table_name == "posts")
    fk = next(c for c in posts.columns if c.name == "user_id")
    assert fk.is_foreign_key is True
    assert fk.references == "users.id"
    assert "users" in posts.related_tables


def test_parse_table_level_constraints():
    ddl = """
    CREATE TABLE orders (
      id bigserial,
      member_id integer NOT NULL,
      total numeric(10,2) NOT NULL,
      PRIMARY KEY (id),
      FOREIGN KEY (member_id) REFERENCES members(id)
    );
    """
    t = parse_ddl(ddl)[0]
    id_col = next(c for c in t.columns if c.name == "id")
    assert id_col.is_primary_key is True
    member = next(c for c in t.columns if c.name == "member_id")
    assert member.is_foreign_key is True
    assert member.references == "members.id"


def test_parse_empty_and_non_ddl():
    assert parse_ddl("") == []
    assert parse_ddl("   ") == []
    assert parse_ddl("SELECT 1;") == []


def test_parse_schema_prefix_and_if_not_exists():
    ddl = "CREATE TABLE IF NOT EXISTS public.settings (key varchar(100) PRIMARY KEY, value text);"
    tables = parse_ddl(ddl)
    assert len(tables) == 1
    assert tables[0].table_name == "settings"


def test_parse_default_value_captured():
    t = parse_ddl("CREATE TABLE t (status varchar(20) NOT NULL DEFAULT 'pending');")[0]
    assert t.columns[0].default == "'pending'"
