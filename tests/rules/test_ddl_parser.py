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


# ── to_ddl：確認頁「以 DDL 編輯」用，必須能被 parse_ddl 解回 ─────────────────


def test_to_ddl_round_trips_through_parse_ddl():
    from app.rules.ddl_parser import to_ddl
    from tests.specs import col, table

    original = [
        table(
            "users",
            "使用者",
            [
                col("id", "uuid", False, "主鍵", is_primary_key=True),
                col("email", "varchar", False, "信箱", length=255, is_unique=True),
                col("nickname", "varchar", True, "暱稱", length=50),
            ],
        ),
        table(
            "orders",
            "訂單",
            [
                col("id", "uuid", False, "主鍵", is_primary_key=True),
                col("user_id", "uuid", False, "下單者", is_foreign_key=True,
                    references="users.id"),
            ],
        ),
    ]

    parsed = parse_ddl(to_ddl(original))

    assert [t.table_name for t in parsed] == ["users", "orders"]
    users = parsed[0]
    assert [c.name for c in users.columns] == ["id", "email", "nickname"]
    assert users.columns[0].is_primary_key
    assert users.columns[1].is_unique
    assert users.columns[1].length == 255
    assert users.columns[1].nullable is False
    assert users.columns[2].nullable is True
    orders = parsed[1]
    assert orders.columns[1].is_foreign_key
    assert orders.columns[1].references == "users.id"


def test_to_ddl_keeps_column_descriptions_as_comments():
    """說明文字對使用者有意義但不是 CREATE TABLE 語法，以註解帶出、解析時忽略。"""
    from app.rules.ddl_parser import to_ddl
    from tests.specs import col, table

    ddl = to_ddl([table("t", "測試表", [col("c", "text", True, "這是說明")])])
    assert "-- 這是說明" in ddl
    assert parse_ddl(ddl)[0].columns[0].name == "c"
