"""架構規則：把「跨檔案的約定」變成會失敗的測試。

這些約定原本只寫在各模組的 docstring 裡，沒有任何東西在強制執行。單元測試不會
抓到它們——沒有哪一個檔案「該為」跨檔案的規則負責。這裡用原始碼掃描補上。

新增規則的判準：這條約定被違反時，是否會產生一整類 bug、而現有測試抓不到。
"""

import re
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2] / "app"


def sources(package: str) -> list[Path]:
    return sorted(p for p in (APP / package).rglob("*.py") if "__pycache__" not in p.parts)


def code_of(path: Path) -> str:
    """去掉註解，避免「註解裡提到某個寫法」被誤判成違規。"""
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        out.append(line.split("  # ")[0])
    return "\n".join(out)


def imports_of(path: Path) -> str:
    """只取 import 區塊，避免比對到註解或字串裡的模組名稱。"""
    lines = [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith(("import ", "from "))
    ]
    return "\n".join(lines)


# ── 分層方向：上層可以依賴下層，反過來不行 ──────────────────────────────
# api → services → repos → models
#         ↓
#     rules / llm（純邏輯，不知道有 DB 與 HTTP 的存在）

LAYER_RULES = [
    # (package, 禁止 import 的樣式, 為什麼)
    (
        "services",
        r"^(from|import) fastapi",
        "services 不得依賴 HTTP 框架，否則無法在 worker 內重用",
    ),
    ("rules", r"from app\.(services|api|repos|llm)", "rules 是純規則模組，不得依賴任何有狀態的層"),
    ("llm", r"from app\.(repos|api|services)", "llm 是純呼叫層，不得知道 DB 與 HTTP 的存在"),
    ("repos", r"from app\.(services|api)", "repos 不得反向依賴上層"),
]


@pytest.mark.parametrize("package,forbidden,reason", LAYER_RULES)
def test_layer_does_not_depend_upwards(package, forbidden, reason):
    offenders = [
        f"{p.relative_to(APP)}: {m.group(0)}"
        for p in sources(package)
        if (m := re.search(forbidden, imports_of(p), re.MULTILINE))
    ]
    assert not offenders, f"{reason}\n違規：\n" + "\n".join(offenders)


# ── LLM 呼叫點必須採用探測到的能力檔 ────────────────────────────────────


# 唯一合法的例外：探針必須量測「未經任何覆蓋」的真實能力，
# 以及工廠本身就是那個唯一入口。
_PROFILE_EXEMPT = {"api/routers/llm.py", "services/provider_factory.py"}


def test_call_sites_do_not_bypass_the_capability_profile():
    """直接呼叫 from_settings() 會拿到「全部支援」的預設值，繞過 /llm/diagnose 的探測結果。

    這條規則的由來：實測發現只有 DB Agent 會帶入探測結果，訪談與 NL2SQL 面對
    不支援 system role 的 gateway 時直接 500。修好之後需要有東西擋住「日後新增
    呼叫點時又忘記」。
    """
    offenders = [
        rel
        for p in sources("")
        if (rel := str(p.relative_to(APP))) not in _PROFILE_EXEMPT
        and "LLMProvider.from_settings(" in code_of(p)
    ]
    assert not offenders, (
        "以下檔案直接建立 provider，會忽略探測到的 gateway 能力檔；"
        "請改用 provider_factory.build_provider(db)：\n" + "\n".join(offenders)
    )


# ── 使用者可見的錯誤訊息必須是中文 ──────────────────────────────────────

_DETAIL_RE = re.compile(r'detail="([^"]+)"')
_HAS_CJK = re.compile(r"[一-鿿]")


def test_http_error_details_are_written_for_users():
    """`lib/api.js` 的 friendlyMessage 會直接把後端的 detail 顯示成 toast。

    平台的使用者包含完全不懂技術的人——驗收時就有使用者把英文的
    「Only SELECT and EXPLAIN queries are allowed」理解成系統故障。
    """
    offenders = []
    for path in sources("api"):
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for message in _DETAIL_RE.findall(line):
                if not _HAS_CJK.search(message):
                    offenders.append(f"{path.relative_to(APP)}:{line_no}  {message}")
    assert not offenders, (
        "以下錯誤訊息會直接顯示給使用者，但沒有中文：\n" + "\n".join(offenders)
    )
