"""
Admin webui routes for the live application log viewer ("Logs" page) - a read-only tail of what
the process-wide logger has captured in its LogBufferHandler since this process started.
Gated by require_admin_session (see admin_router.py).
"""

import asyncio
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from Pytre_Server.log_buffer import LogBufferHandler
from Pytre_Server.result_streaming import DisconnectAware
from Pytre_Server.server_state import ServerState, get_server_state
from Pytre_Server.templating import templates

router = APIRouter(prefix="/logs")

POLL_INTERVAL_SECS = 0.75
INITIAL_PAGE_SIZE = 300
BATCH_SIZE = 300
LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


@router.get("", response_class=HTMLResponse)
def logs_page(request: Request, state: ServerState = Depends(get_server_state)) -> HTMLResponse:
    """Renders the most recent INITIAL_PAGE_SIZE entries, newest first - never the whole buffer,
    which can hold many more than that (bounded by bytes, not entry count - see log_buffer.py)."""
    log_buffer = state.resources.log_buffer
    entries = log_buffer.entries_latest(INITIAL_PAGE_SIZE) if log_buffer is not None else []

    context = {
        "entries": entries,
        "last_seq": entries[0].seq if entries else 0,
        "levels": LEVELS,
    }
    return templates.TemplateResponse(request, "admin/logs.html", context)


async def _stream_events(
    log_buffer: LogBufferHandler | None, http_request: DisconnectAware, after: int
) -> AsyncGenerator[str]:
    """SSE tail of new entries past `after`, oldest first - the client prepends each one in turn,
    which naturally keeps the newest-on-top order correct with no client-side sorting needed.
    Each event carries its seq as SSE `id`, sent back by the browser as Last-Event-ID when
    EventSource reconnects on its own - see logs_stream().

    A standalone function (not nested in the route) so it can be unit-tested directly with a fake
    DisconnectAware - Starlette's TestClient can't drive an HTTP request against a StreamingResponse
    whose generator runs forever (it waits for the response to finish before yielding control back
    to the test), so this can't be exercised through a real HTTP round trip in tests."""
    last_seq = after
    while True:
        if await http_request.is_disconnected():
            return
        if log_buffer is not None:
            for entry in log_buffer.entries_since(last_seq):
                yield f"id: {entry.seq}\ndata: {entry.to_json()}\n\n"
                last_seq = entry.seq
        await asyncio.sleep(POLL_INTERVAL_SECS)


@router.get("/stream")
async def logs_stream(
    request: Request,
    after: int = 0,
    last_event_id: int | None = Header(None),
    state: ServerState = Depends(get_server_state),
) -> StreamingResponse:
    """`after` is only the starting point of the page's first connection. On an automatic reconnect,
    EventSource requests that same URL again but sends the last received seq as Last-Event-ID -
    resuming from `after` instead would replay every entry already shown since the page loaded."""
    start = last_event_id if last_event_id is not None else after
    return StreamingResponse(
        _stream_events(state.resources.log_buffer, request, start),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/before")
def logs_before(before: int, limit: int = BATCH_SIZE, state: ServerState = Depends(get_server_state)) -> JSONResponse:
    """Feeds the page's infinite scroll toward older history - entries just before `before`,
    newest first, ready to append at the bottom. Empty once there's nothing older left."""
    log_buffer = state.resources.log_buffer
    entries = log_buffer.entries_before(before, limit) if log_buffer is not None else []
    return JSONResponse([e.to_dict() for e in entries])
