"""Preparation shared by both ways to execute a query, /execute and /jobs: the request is checked against the
queries the user is authorized to use, and one of the user's execution slots is taken. No route here."""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from pydantic import BaseModel, Field

from Pytre_Core.context import UserContext
from Pytre_Core.user_prefs import DisplayFormat
from Pytre_Core.utils import log as _log
from Pytre_Core.sql_query.loader import build_queries_from_parsers, filter_queries
from Pytre_Core.sql_query.query import Query

from Pytre_Server.server_state import ServerResources, ServerState


def log(msg: str | object, level: int = logging.INFO, sublogger: str = __name__) -> None:
    _log(msg, level=level, sublogger=sublogger)


class ExecuteRequest(BaseModel):
    """Request body for POST /execute and POST /jobs"""

    server_id: str
    query_id: str
    template_hash: str  # to check for staleness
    display_format: DisplayFormat  # validated by DisplayFormat.__post_init__
    display_parameters: dict[str, str] = Field(default_factory=dict)


@dataclass(frozen=True)
class PreparedExecution:
    """What an execution needs once its request is checked, one of the user's execution slots already taken"""

    user_ctx: UserContext
    template: str
    params: dict[str, Any]
    semaphore: asyncio.Semaphore  # acquired: to release once the execution is over


async def prepare_execution(request: ExecuteRequest, username: str, state: ServerState) -> PreparedExecution:
    """Checks `request` and takes one of the user's execution slots, for /execute and /jobs.
    Raises HTTPException: 404 unknown or unauthorized query, 409 stale query or parameters, 400 invalid value,
    429 too many executions at once for the user."""
    resources: ServerResources = state.resources
    user_ctx = UserContext(state.app_ctx.users, username=username, display_format_override=request.display_format)

    # build and control query
    query = _resolve_query(state, user_ctx, request)
    _check_template_hash(query, request, resources)
    _apply_parameters(query, request, user_ctx.user.admin)

    # retrieve build command template and parameters for execution
    exec_config = query.get_exec_config(request.server_id, file_output=False)
    template, params = exec_config.cmd_template, exec_config.cmd_parameters

    # cap how many requests user can have running at once
    semaphore = resources._get_user_semaphore(state.max_concurrency_per_user, username)
    if semaphore.locked():
        http_error = status.HTTP_429_TOO_MANY_REQUESTS
        raise HTTPException(status_code=http_error, detail="Too many concurrent requests, try again later")

    await semaphore.acquire()  # released once the execution is over: by _run_query() or by the job

    # log execution request
    msg_params = f"with {params} as parameters" if params else "(no parameter)"
    msg = f"Query execution for '{username}': '{request.query_id}' on '{request.server_id}' {msg_params}"
    log(msg, logging.INFO)

    return PreparedExecution(user_ctx, template, params, semaphore)


def _resolve_query(state: ServerState, user_ctx: UserContext, request: ExecuteRequest) -> Query:
    """Finds request.query_id among the queries the user is authorized to use.
    Raises HTTPException(404) if not found or not authorized."""

    # build the requested query and filter to keep only if authorized
    target_parser = state.query_catalog.by_file_id.get(request.query_id)
    queries, _errors = build_queries_from_parsers(state.app_ctx, user_ctx, [target_parser] if target_parser else [])
    authorized = filter_queries(state.app_ctx.servers, queries, request.server_id, user_ctx.user)

    # get query version on the server matching the requested query_id
    query = next((q for q in authorized if q.file_id == request.query_id), None)
    if query is None:
        http_error = status.HTTP_404_NOT_FOUND
        raise HTTPException(status_code=http_error, detail="Unknown or unauthorized query_id for this server_id")

    return query


def _check_template_hash(query: Query, request: ExecuteRequest, resources: ServerResources) -> None:
    """Check client's template hash against the server's version. Raises HTTPException(409) unless exempted."""
    hash_exemption_until = resources.ignore_hash_until.get(query.file_id)
    hash_must_be_checked = hash_exemption_until is None or datetime.now(timezone.utc) > hash_exemption_until
    if hash_must_be_checked and request.template_hash != query.get_template_hash():
        http_error = status.HTTP_409_CONFLICT
        raise HTTPException(status_code=http_error, detail="Query is not up to date, refresh your query list")


def _apply_parameters(query: Query, request: ExecuteRequest, is_admin: bool) -> None:
    """Validates parameters matches what the server expects,
    replace received hidden parameters with server computed one unless user is an admin,
    then applies the values onto `query`.
    Raises HTTPException(409) on a name mismatch or HTTPException(400) on an invalid value."""

    # check the client's parameters matches what the server expects
    expected_params = set(query.parameters.params.keys())
    received_params = set(request.display_parameters.keys())
    if expected_params != received_params:
        missing = sorted(expected_params - received_params)
        extra = sorted(received_params - expected_params)
        http_error = status.HTTP_409_CONFLICT
        detail = f"Parameters mismatch, refresh your query list - missing: {missing}, unexpected: {extra}"
        raise HTTPException(status_code=http_error, detail=detail)

    # unless user is an admin, replace received hidden parameters with the server computed one
    if not is_admin:
        for key, param in query.parameters.params.items():
            if param.is_hidden:
                request.display_parameters[key] = param.display_value

    # update query parameter values
    try:
        query.set_display_values(request.display_parameters)
    except ValueError as e:
        http_error = status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=http_error, detail=f"Invalid value: {e.args[0]}") from e
