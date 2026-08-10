"""Execute pre-validated DDL against a target PostgreSQL database.

This module is intentionally separate from db_manager.py (which is read-only).
All callers must pass DDL that has already been checked by
sql_safety.check_ddl_allowlist() and approved via the human-in-the-loop
change-request review flow.
"""
import logging
import re

from app.rules.sql_safety import skeleton, split_statements

logger = logging.getLogger(__name__)

DDL_TIMEOUT_MS = 30_000

# PostgreSQL 規定 CREATE INDEX CONCURRENTLY 不能在交易區塊內執行，因此這些語句
# 必須從主交易拆出來、以 autocommit 單獨跑。用 skeleton 比對，避免字串或註解裡
# 的同名字被誤判。
_CONCURRENTLY_RE = re.compile(r"\bCONCURRENTLY\b", re.IGNORECASE)
_CREATE_INDEX_RE = re.compile(r"^\s*CREATE\s+(UNIQUE\s+)?INDEX\b", re.IGNORECASE)


def _is_concurrent(statement: str) -> bool:
    return bool(_CONCURRENTLY_RE.search(skeleton(statement)))


def split_by_concurrency(ddl: str) -> tuple[list[str], list[str]]:
    """把 DDL 切成 (可共用單一交易的語句, 必須各自 autocommit 執行的語句)。"""
    transactional: list[str] = []
    concurrent: list[str] = []
    for statement in split_statements(ddl):
        (concurrent if _is_concurrent(statement) else transactional).append(statement)
    return transactional, concurrent


def locking_warnings(ddl: str) -> list[str]:
    """列出「dry-run 測不到、但執行時會擋住正式服務」的語句。

    dry-run 跑在空的暫存 schema 上，秒過；同一句話對 500 萬筆的正式表則會拿到
    ACCESS EXCLUSIVE 鎖，期間所有讀寫全部卡住。核准的人必須先知道這件事。
    """
    warnings = []
    for statement in split_statements(ddl):
        skel = skeleton(statement)
        if _CREATE_INDEX_RE.match(skel) and not _is_concurrent(statement):
            warnings.append(
                "這句建索引沒有加 CONCURRENTLY，執行期間該資料表會被鎖住、"
                "所有讀寫都會卡住（資料量大時可能數分鐘）。"
                f"若這是正在使用中的資料表，建議改用 CONCURRENTLY：{statement[:120]}"
            )
    return warnings


def execute_ddl(db_url: str, ddl: str) -> dict:
    """Execute one or more DDL statements against db_url in a single transaction.

    All statements run inside one transaction: if any statement fails, the
    whole transaction is rolled back and none of the DDL takes effect.
    Only on full success is the transaction committed.

    Returns {"ok": True, "statements_run": N} or {"ok": False, "error": "..."}.
    Caller is responsible for calling sql_safety.check_ddl_allowlist() first.
    """
    try:
        import psycopg2
    except ImportError:
        return {"ok": False, "error": "psycopg2-binary is not installed"}

    try:
        conn = psycopg2.connect(
            db_url,
            connect_timeout=10,
            options=f"-c statement_timeout={DDL_TIMEOUT_MS}",
        )
    except Exception as exc:
        return {"ok": False, "error": f"連線失敗：{str(exc)[:200]}"}

    transactional, concurrent = split_by_concurrency(ddl)
    statements_run = 0
    try:
        try:
            with conn.cursor() as cur:
                for stmt in transactional:
                    cur.execute(stmt)
                    statements_run += 1
        except Exception as exc:
            conn.rollback()
            logger.warning("ddl_executor: statement failed", extra={"err": str(exc)[:300]})
            return {"ok": False, "error": str(exc)[:300]}
        conn.commit()

        # CONCURRENTLY 必須在交易之外執行，因此放在主交易 commit 之後、各自獨立跑。
        # 代價是這些語句失敗時前面的變更已經生效、無法一併回滾——所以失敗訊息要
        # 講清楚「哪些已經生效」，讓人知道現在的資料庫處於什麼狀態。
        if concurrent:
            conn.autocommit = True
            try:
                with conn.cursor() as cur:
                    for stmt in concurrent:
                        cur.execute(stmt)
                        statements_run += 1
            except Exception as exc:
                _drop_invalid_indexes(conn, concurrent)
                logger.warning(
                    "ddl_executor: concurrent statement failed", extra={"err": str(exc)[:300]}
                )
                return {
                    "ok": False,
                    "error": (
                        f"{str(exc)[:250]}"
                        f"（注意：前面 {len(transactional)} 句已經生效且無法回滾）"
                    ),
                    "statements_run": statements_run,
                    "committed_before_failure": len(transactional),
                }
    finally:
        conn.close()

    logger.info("ddl_executor: executed %d statements", statements_run)
    return {"ok": True, "statements_run": statements_run}


def _drop_invalid_indexes(conn, statements: list[str]) -> None:
    """CONCURRENTLY 建到一半失敗會留下 INVALID 索引，PostgreSQL 要求手動清掉。

    清理本身失敗不再往上拋——此時真正該讓呼叫端看到的是原始錯誤。
    """
    name_re = re.compile(
        r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+CONCURRENTLY\s+(?:IF\s+NOT\s+EXISTS\s+)?(\S+)",
        re.IGNORECASE,
    )
    for statement in statements:
        match = name_re.search(skeleton(statement))
        if not match:
            continue
        try:
            with conn.cursor() as cur:
                cur.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {match.group(1)}")
        except Exception:  # noqa: BLE001
            logger.warning("ddl_executor: 無法清理未完成的索引 %s", match.group(1))
