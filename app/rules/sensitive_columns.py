"""依欄位名稱判斷敏感資料，並遮蔽查詢結果。

用途只有一個：DB Agent 讀到的資料會成為 tool observation，進入**全平台共用的**
對話 transcript，並被送往 `LLM_BASE_URL` 指向的服務。若那是外部服務，等同把
個資或薪資明文送出公司網路邊界；就算是內網部署，這些值仍會留在共用 transcript
裡成為下一個使用者的上下文。

刻意不套用在人工查詢頁：那裡的使用者本來就有權限看真實資料，而且結果不會進入
LLM 也不會留在共用 transcript——遮了只是妨礙工作。

判斷只看欄位名稱。這會漏掉命名不明顯的欄位（例如 `col_17`），但誤判成本低、
實作成本也低；真正的資料分類是另一個層級的工作。
"""

import re
from typing import Any

MASK = "***"

# 命中即遮蔽。中英文並列是因為這個平台的資料表本來就會用中文命名。
SENSITIVE_HINTS = (
    # 憑證
    "password", "passwd", "pwd", "secret", "token", "api_key", "apikey",
    "private_key", "credential", "密碼", "金鑰",
    # 身分識別
    "id_number", "id_no", "national_id", "ssn", "passport", "身分證", "身份證", "護照",
    # 金流
    "credit_card", "card_number", "cvv", "bank_account", "iban", "信用卡", "銀行帳號",
    # 薪酬
    "salary", "payroll", "compensation", "bonus", "薪資", "薪水", "年薪",
)

_HINT_RE = re.compile("|".join(re.escape(h) for h in SENSITIVE_HINTS), re.IGNORECASE)


def is_sensitive(column_name: str) -> bool:
    """欄位名稱看起來像敏感資料就回 True。"""
    return bool(_HINT_RE.search(column_name or ""))


def mask_result(result: dict[str, Any]) -> dict[str, Any]:
    """遮蔽 `{"columns": [...], "rows": [[...]]}` 形狀結果裡的敏感欄位。

    只換值、不刪欄位——模型仍需要知道這個欄位存在，才能正確描述查到了什麼。
    """
    columns = result.get("columns")
    rows = result.get("rows")
    if not isinstance(columns, list) or not isinstance(rows, list):
        return result

    masked_indexes = [i for i, name in enumerate(columns) if is_sensitive(str(name))]
    if not masked_indexes:
        return result

    out = dict(result)
    out["rows"] = [
        [MASK if i in masked_indexes else value for i, value in enumerate(row)]
        if isinstance(row, list)
        else row
        for row in rows
    ]
    out["masked_columns"] = [columns[i] for i in masked_indexes]
    return out
