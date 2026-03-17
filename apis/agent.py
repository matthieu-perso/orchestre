"""
Agent chat endpoint.

POST /agent/chat
  Body: { message, conversation_id? }
  Response: Server-Sent Events stream

GET /agent/conversations
  Returns list of active conversation IDs for the current user.

DELETE /agent/conversations/{conversation_id}
  Clears a conversation's history from Redis.
"""
import json
import uuid
import logging

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional

from agents.commerce_agent import agent
from core.utils.message import MessageErr, MessageOK
from .users import User, get_current_user

router = APIRouter()
logger = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None


@router.post(
    "/chat",
    summary="Chat with the commerce agent",
    description="""
Send a natural language message and receive a streaming response.

The agent can:
- Answer questions about your stores, orders, inventory, ads
- Run optimizations (always previews with dry_run first, then asks for confirmation)
- Create and manage fulfillment rules
- Explain what the automation has been doing

**Response format** (Server-Sent Events):
```
data: {"type": "status",  "content": "Checking inventory…"}
data: {"type": "token",   "content": "You have "}
data: {"type": "token",   "content": "3 SKUs…"}
data: {"type": "done",    "conversation_id": "abc123"}
```

Pass the returned `conversation_id` in subsequent messages to maintain context.
""",
)
async def chat(
    body: ChatRequest,
    curr_user: User = Depends(get_current_user),
):
    user_id = curr_user["uid"]
    conversation_id = body.conversation_id or str(uuid.uuid4())

    async def event_stream():
        try:
            async for event in agent.run(
                user_id=user_id,
                message=body.message,
                conversation_id=conversation_id,
            ):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as e:
            logger.exception("SSE stream error: %s", e)
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'conversation_id': conversation_id})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",       # disables Nginx response buffering
            "Connection": "keep-alive",
        },
    )


@router.get(
    "/conversations",
    summary="List active conversations",
    description="Returns all active conversation IDs for the current user (from Redis).",
)
async def list_conversations(curr_user: User = Depends(get_current_user)):
    try:
        import redis.asyncio as aioredis
        from core.config import settings

        r = aioredis.from_url(settings.REDIS_URL, encoding="utf-8", decode_responses=True)
        keys = await r.keys(f"agent_conv:{curr_user['uid']}:*")
        await r.aclose()

        conversation_ids = [k.split(":")[-1] for k in keys]
        return MessageOK(data={"conversations": conversation_ids})
    except Exception as e:
        return MessageErr(reason=str(e))


@router.delete(
    "/conversations/{conversation_id}",
    summary="Clear a conversation",
)
async def clear_conversation(
    conversation_id: str,
    curr_user: User = Depends(get_current_user),
):
    try:
        import redis.asyncio as aioredis
        from core.config import settings

        r = aioredis.from_url(settings.REDIS_URL, encoding="utf-8", decode_responses=True)
        await r.delete(f"agent_conv:{curr_user['uid']}:{conversation_id}")
        await r.aclose()
        return MessageOK(data={"cleared": conversation_id})
    except Exception as e:
        return MessageErr(reason=str(e))
