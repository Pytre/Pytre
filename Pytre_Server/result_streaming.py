import asyncio
import json
from collections.abc import AsyncGenerator
from dataclasses import replace
from datetime import datetime
from typing import Protocol

from Pytre_Core.logs_central import ExecutionLogEntry, ServerDriven
from Pytre_Core.remotes import encode_row_value
from Pytre_Core.sql_query.query_drivers_async import AsyncSqlResult


class DisconnectAware(Protocol):
    """Minimal surface needed from FastAPI's Request - only is_disconnected() -
    lets tests pass lightweight doubles instead of a full ASGI Request."""

    async def is_disconnected(self) -> bool: ...


class ClientDisconnected(Exception):
    """Raised by pings_until_done() when the client went away while waiting."""


async def stream_result_ndjson(
    result: AsyncSqlResult,
    http_request: DisconnectAware,
    chunk_size: int,
    heartbeat_secs: int,
    execution_logs: ServerDriven | None,
    log_entry: ExecutionLogEntry,
) -> AsyncGenerator[str]:
    """Streams `result` as NDJSON: a "__columns__" line, then "__row__" lines, fetching and sending `chunk_size`
    rows at a time. Waiting for the next rows can take some time so pings are sent meanwhile.

    A failing fetch or a row failing to convert/serialize are reported as a final "__error__" line.
    Already-serialized rows are flushed first, nothing successfully read is thrown away.

    Stops early if the client disconnects (checked once per chunk, and while waiting for one). A fetch still
    running is then cancelled. Closing `result` has to be handled by the caller."""

    columns = tuple(result.columns())
    yield json.dumps({"__columns__": columns}) + "\n"

    json_lines: list[str] = []
    nb_rows = 0
    try:
        while True:
            fetch_task = asyncio.create_task(result.fetch(chunk_size))
            try:
                async for ping_line in pings(fetch_task, http_request, heartbeat_secs):
                    yield ping_line
            except ClientDisconnected:
                fetch_task.cancel()
                await asyncio.gather(fetch_task, return_exceptions=True)
                return

            rows = fetch_task.result()
            if not rows:
                break

            for row in rows:
                json_lines.append(json.dumps({"__row__": tuple(encode_row_value(value) for value in row)}))
                nb_rows += 1

            yield "\n".join(json_lines) + "\n"
            json_lines = []

            if await http_request.is_disconnected():
                return
    except Exception as e:
        if json_lines:
            yield "\n".join(json_lines) + "\n"
        yield json.dumps({"__error__": {"type": type(e).__name__, "message": str(e)}}) + "\n"
        return

    if execution_logs is not None:
        duration_secs = int((datetime.now() - log_entry.start).total_seconds())
        execution_logs.log_execution(replace(log_entry, duration_secs=duration_secs, nb_rows=nb_rows))


async def pings(task: asyncio.Task, http_request: DisconnectAware, heartbeat_secs: int) -> AsyncGenerator[str]:
    """Prevent network devices to cut an idle connection after a few minutes by yielding a ping NDJSON line
    every `heartbeat_secs` (0 to disable) while waiting for `task` to finish.
    Raises ClientDisconnected if the client disconnected."""

    if heartbeat_secs <= 0:
        await asyncio.wait({task})
        return

    while True:
        done, _pending = await asyncio.wait({task}, timeout=heartbeat_secs)

        if task in done:
            return
        if await http_request.is_disconnected():
            raise ClientDisconnected

        yield json.dumps({"__ping__": True}) + "\n"
