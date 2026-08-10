"""DB Agent 對話 API：`POST /agent/chat`。

`Accept: text/event-stream` 時以 SSE 推送 `tool_call`/`tool_result`/`delta`/
`turn_done` 事件；其餘情況回傳最終結果的一次性 JSON（相容測試與非串流客戶端）。

注意：`get_db`（`app/api/deps.py`）是「進 request 就開 session、離開就 commit/rollback」
的依賴——`StreamingResponse` 的 body 是在 endpoint 函式返回之後才逐步送出，
若把整個 agent 迴圈放進串流產生器裡跑，DB session 早已被關閉。因此本端點在
依賴仍存活的 endpoint 函式本體內把整回合跑完、事件先收集成 list，SSE 只是把
收集好的事件依序序列化送出（前端仍會照事件順序拿到 tool_call/tool_result 的
步驟軌跡，但不是逐步即時推播）。
"""

import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.services import agent_service
from app.services.auth_service import CurrentUser

router = APIRouter(prefix="/agent", tags=["agent"])

# 模組層級單例：避免 B008（Depends() 直接寫在參數預設值會被 lint 擋下）。
_DbDep = Depends(get_db)
# AUTH_ENABLED=false 時 get_current_user 恆回 None、不擋任何請求（匿名模式行為不變）；
# true 時缺少或無效憑證一律 401。Agent 能透過工具讀取業務資料庫的真實資料，
# 不該是未登入即可呼叫的端點。
_AuthDep = Depends(get_current_user)
_CurrentUserDep = Depends(get_current_user)


class ChatBody(BaseModel):
    message: str
    db_name: str | None = None


def _format_sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/chat", dependencies=[_AuthDep])
async def chat(
    body: ChatBody,
    request: Request,
    db: AsyncSession = _DbDep,
    current_user: CurrentUser | None = _CurrentUserDep,
):
    # agent 讀得到業務資料庫的真實資料，是全平台唯一沒有留痕的路徑——身分要往下傳。
    actor = getattr(current_user, "email", None) if current_user else None
    events = [
        event
        async for event in agent_service.run_agent_turn_stream(
            db, body.message, body.db_name, actor=actor
        )
    ]

    accept = request.headers.get("accept", "")
    if "text/event-stream" in accept:

        async def event_source():
            for event in events:
                yield _format_sse(event["event"], event["data"])

        return StreamingResponse(event_source(), media_type="text/event-stream")

    final = next((e["data"] for e in events if e["event"] == "turn_done"), None)
    return final


@router.post("/conversations", status_code=201, dependencies=[_AuthDep])
async def new_conversation(db: AsyncSession = _DbDep) -> dict:
    """開一條新的 Agent 對話（切乾淨上下文）。舊對話保留於資料庫，只是不再沿用。"""
    session_id = await agent_service.start_new_conversation(db)
    return {"session_id": str(session_id)}
