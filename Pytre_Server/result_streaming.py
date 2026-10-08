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


async def stream_result_ndjson(
    result: AsyncSqlResult,
    http_request: DisconnectAware,
    chunk_size: int,
    execution_logs: ServerDriven | None,
    log_entry: ExecutionLogEntry,
) -> AsyncGenerator[str]:
    """Serializes rows in chunks to keep peak memory at O(chunk_size).
    Iterating `result` is still synchronous (no await point in QueryStream),
    but yield hands control back between chunks.

    A row failing to convert/serialize (e.g. ConversionError) is reported as a final "__error__" line.
    The client distinguishes it from a normal row by that top-level key.
    Already-serialized rows are flushed first, nothing successfully read is thrown away.

    Stops early if the client disconnects (checked once per chunk, not per row - fastmssql
    exposes no way to cancel the underlying SQL Server query, so this only stops Pytre_Server
    from doing further useless work, it doesn't free the connection any sooner)."""

    try:
        columns = tuple(result.columns())
    except ValueError:
        columns = tuple()  # fastmssql can't tell the column names if no line is returned

    yield json.dumps({"__columns__": columns}) + "\n"

    json_lines: list[str] = []
    nb_rows = 0
    try:
        for row in result:
            row_dict = row.to_dict()
            row_values = tuple(encode_row_value(row_dict[col]) for col in columns)

            line = json.dumps({"__row__": row_values})
            json_lines.append(line)
            nb_rows += 1

            if len(json_lines) >= chunk_size:
                yield "\n".join(json_lines) + "\n"
                json_lines = []
                if await http_request.is_disconnected():
                    return
        if json_lines:
            yield "\n".join(json_lines) + "\n"
    except Exception as e:
        if json_lines:
            yield "\n".join(json_lines) + "\n"
        yield json.dumps({"__error__": {"type": type(e).__name__, "message": str(e)}}) + "\n"
        return

    if execution_logs is not None:
        duration_secs = int((datetime.now() - log_entry.start).total_seconds())
        execution_logs.log_execution(replace(log_entry, duration_secs=duration_secs, nb_rows=nb_rows))
