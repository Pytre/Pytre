"""POST /execute: a query executed then its result streamed as NDJSON in a single response, pings sent meanwhile.
Kept for the clients already deployed, replaced by /jobs."""

import asyncio
import logging
import traceback
from collections.abc import AsyncGenerator
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from Pytre_Core.context import UserContext
from Pytre_Core.logs_central import ExecutionLogEntry
from Pytre_Core.remote_protocol import stream_error
from Pytre_Core.utils import log as _log
from Pytre_Core.sql_query.query_drivers_server import AsyncSqlConnectionError, AsyncSqlExecutionError

from Pytre_Server.result_streaming import ClientDisconnected, DisconnectAware, pings, stream_result_ndjson
from Pytre_Server.server_state import ServerResources, ServerState, get_server_state
from Pytre_Server.routes.client_auth import require_access_token
from Pytre_Server.routes.client_execution import ExecuteRequest, prepare_execution


def log(msg: str | object, level: int = logging.INFO, sublogger: str = __name__) -> None:
    _log(msg, level=level, sublogger=sublogger)


router = APIRouter()


@router.post("/execute")
async def execute_query(
    request: ExecuteRequest,
    http_request: Request,
    username: str = Depends(require_access_token),
    state: ServerState = Depends(get_server_state),
) -> StreamingResponse:
    """Execute query using infos sent by the client : a query_id, template hash (to check for staleness) and
    parameter values. Authorization is re-validated (query exists, is associated with server_id, user allowed)"""

    prepared = await prepare_execution(request, username, state)

    # stream result - sends pings while the query is executing then NDJSON columns/rows once it completes
    return StreamingResponse(
        _run_query(
            state,
            state.resources,
            request,
            prepared.user_ctx,
            prepared.template,
            prepared.params,
            prepared.semaphore,  # released by _run_query()
            http_request,
        ),
        media_type="application/x-ndjson",
    )


async def _run_query(
    state: ServerState,
    resources: ServerResources,
    request: ExecuteRequest,
    user_ctx: UserContext,
    template: str,
    params: dict,
    semaphore: asyncio.Semaphore,
    http_request: DisconnectAware,
) -> AsyncGenerator[str]:
    """Executes the query on the server's shared driver, then streams its NDJSON result.
    While the database executes the query and next rows are produced ping lines are sent every
    state.heartbeat_secs. The driver stays in use until the result is closed.
    Releases `semaphore` once done, whichever way this ends.

    A client disconnecting during the execution can't interrupt it: the execution is still awaited so its
    result is closed properly, then discarded."""

    query_id = request.query_id
    server_id = request.server_id
    username = user_ctx.user.username
    server = state.app_ctx.servers.servers_dict[request.server_id]

    try:
        async with resources.use_driver(server) as driver:
            # execute the query and emit an heartbeat while waiting
            start_time = datetime.now(timezone.utc)
            task = asyncio.create_task(driver.execute(template, params))
            try:
                async for ping_line in pings(task, http_request, state.heartbeat_secs):
                    yield ping_line
            except ClientDisconnected:
                msg = f"User '{username}' disconnected while query '{query_id}' on '{server_id}' was still executing."
                log(msg, logging.INFO)
                outcome = (await asyncio.gather(task, return_exceptions=True))[0]
                if not isinstance(outcome, BaseException):
                    await outcome.close()
                return
            result = await task

            # prepare a log_entry
            log_entry = ExecutionLogEntry(
                server_id=server_id,
                user_id=username,
                user_display_name=user_ctx.user.display_name,
                query_id=query_id,
                start=start_time,
                parameters=request.display_parameters,
            )

            # stream result
            try:
                async for chunk in stream_result_ndjson(
                    result, http_request, state.batch_size, state.heartbeat_secs, resources.execution_logs, log_entry
                ):
                    yield chunk
            finally:
                await result.close()

    except AsyncSqlConnectionError as e:
        log(f"Server unavailable on server '{server_id}' (user {username}) : {e}", logging.WARNING)
        yield stream_error(e, "Server unavailable: ")
    except AsyncSqlExecutionError as e:
        msg = f"SQL error for query '{query_id}' on '{server_id}' (user {username}): {e}"
        log(msg, logging.ERROR)
        yield stream_error(e, "SQL error: ")
    except Exception as e:
        msg = f"Unexpected error for query '{query_id}' on '{server_id}' (user {username}): "
        log(f"{msg} {type(e).__name__}: {e}\n{traceback.format_exc()}", logging.ERROR)
        yield stream_error(e, "Unexpected error: ")
    finally:
        semaphore.release()
