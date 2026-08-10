"""build_mermaid_er：中文命名的資料表也要畫得出來。

平台面向中文使用者，資料表與欄位用中文命名是常態。mermaid 的 erDiagram
不接受非 ASCII 的實體名與屬性名（已對 vendored mermaid 10.9 實測），
處理不當會整張圖變成一堆底線、或直接語法錯誤顯示紅色炸彈圖。
"""

from app.rules.spec_models import ColumnSpec, TableSpec
from app.services.writers.diagram_writer import build_mermaid_er


def col(name, data_type="varchar(20)", **kwargs):
    return ColumnSpec(name=name, data_type=data_type, nullable=False, description="", **kwargs)


def table(name, columns, description=""):
    return TableSpec(table_name=name, description=description, columns=columns)


def test_chinese_table_name_is_quoted():
    """不加引號的中文實體名會讓 mermaid 直接語法錯誤。"""
    diagram = build_mermaid_er([table("訂單", [col("id", "uuid", is_primary_key=True)])])
    assert '"訂單" {' in diagram
    assert "___" not in diagram


def test_ascii_table_name_stays_unquoted():
    diagram = build_mermaid_er([table("orders", [col("id", "uuid", is_primary_key=True)])])
    assert "    orders {" in diagram


def test_chinese_column_name_kept_readable_as_comment():
    """屬性名不能是中文，但原名要留在註解裡，否則看圖的人只會看到底線。"""
    diagram = build_mermaid_er([table("orders", [col("通路")])])
    assert '"通路"' in diagram


def test_relationship_between_chinese_tables():
    tables = [
        table("訂單", [col("id", "uuid", is_primary_key=True)]),
        table(
            "退貨",
            [
                col("id", "uuid", is_primary_key=True),
                col("order_id", "uuid", is_foreign_key=True, references="訂單.id"),
            ],
        ),
    ]
    diagram = build_mermaid_er(tables)
    assert '"訂單" ||--o{ "退貨"' in diagram


def test_embedded_quote_does_not_break_the_label():
    """mermaid 的引號字串沒有跳脫語法，欄位名帶引號時整張圖會壞掉。"""
    diagram = build_mermaid_er([table("orders", [col('weird"name')])])
    assert '""' not in diagram.replace('" "', "")
