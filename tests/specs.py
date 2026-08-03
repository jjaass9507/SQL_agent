"""測試用的 TableSpec / ColumnSpec 建構捷徑。

`spec_models` 本身只接受 keyword 參數；測試裡要建幾十個欄位，一路寫
`ColumnSpec(name=..., data_type=..., nullable=..., description=...)` 會把斷言
淹沒在樣板字裡。位置參數的便利性留在這裡，不要漏進 app/。
"""

from app.rules.spec_models import ColumnSpec, TableSpec


def col(name: str, data_type: str, nullable: bool, description: str, **kwargs) -> ColumnSpec:
    return ColumnSpec(
        name=name,
        data_type=data_type,
        nullable=nullable,
        description=description,
        **kwargs,
    )


def table(
    table_name: str, description: str, columns: list[ColumnSpec], **kwargs
) -> TableSpec:
    return TableSpec(
        table_name=table_name, description=description, columns=columns, **kwargs
    )
