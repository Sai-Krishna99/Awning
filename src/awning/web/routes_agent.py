import json
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from fastapi.sse import EventSourceResponse, format_sse_event
from pydantic import BaseModel, StringConstraints

from awning.web.routes_shop import _ctx, _error, templates

router = APIRouter()

NO_STORE = {"Cache-Control": "no-store"}
STARTER_PROMPTS = [
    "What can I get at Maria's Cart?",
    "Order a large latte with oat milk from Maria's Cart for 9am pickup, name Sai",
    "Where can I get a croissant?",
]


class MessageRequest(BaseModel):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


@router.get("/try")
def try_page(request: Request) -> Response:
    return templates.TemplateResponse(request, "agent.html", {"starters": STARTER_PROMPTS})


@router.post("/api/agent/sessions")
def create_session(request: Request) -> Response:
    session = _ctx(request).agent_sessions.create()
    return JSONResponse({"session_id": session.id}, headers=NO_STORE)


async def _sse(events: AsyncIterator[dict]) -> AsyncIterator[bytes]:
    async for event in events:
        yield format_sse_event(data_str=json.dumps(event), event=event["type"])


@router.post("/api/agent/sessions/{session_id}/messages")
async def send_message(request: Request, session_id: str, payload: MessageRequest) -> Response:
    ctx = _ctx(request)
    session = ctx.agent_sessions.get(session_id)
    if session is None:
        return _error(404, "session_not_found", "This chat has expired. Start a new chat.")
    # request.client.host is the real client address behind Render's proxy (see routes_merchant.start_upload).
    client_ip = request.client.host if request.client else "unknown"
    if not (ctx.agent_limiter_ip.allow(client_ip) and ctx.agent_limiter_global.allow("global")):
        return _error(429, "rate_limited", "The demo assistant has hit its limit for now. Please try again later.")
    return EventSourceResponse(
        _sse(ctx.agent.run_turn(session, payload.text)), headers={**NO_STORE, "X-Accel-Buffering": "no"}
    )
