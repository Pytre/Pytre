import asyncio
import json
import logging
import traceback
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import Annotated

import jwt
from fastapi import APIRouter, Depends, Header, HTTPException, Query as QueryParam, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, Json

from Pytre_Core.context import UserContext
from Pytre_Core.logs_central import ExecutionLogEntry
from Pytre_Core.servers import filter_servers
from Pytre_Core.users import AuthType, hash_password, verify_password
from Pytre_Core.user_prefs import DisplayFormat
from Pytre_Core.utils import log as _log
from Pytre_Core.sql_query.loader import build_queries_from_parsers, filter_queries
from Pytre_Core.sql_query.query import Query
from Pytre_Core.sql_query.query_drivers_async import AsyncSqlConnectionError, AsyncSqlExecutionError, AsyncSqlResult

from Pytre_Server.auth_token import create_token, decode_token, TokenType
from Pytre_Server.oidc import OidcReverifyError, verify_still_active
from Pytre_Server.result_streaming import DisconnectAware, stream_result_ndjson
from Pytre_Server.server_state import ServerResources, ServerState, get_server_state
from Pytre_Server.routes.client_sso import router as client_sso_router


def log(msg: str | object, level: int = logging.INFO, sublogger: str = __name__) -> None:
    _log(msg, level=level, sublogger=sublogger)


router = APIRouter()
router.include_router(client_sso_router)


class ExecuteRequest(BaseModel):
    """Request body for POST /execute"""

    server_id: str
    query_id: str
    template_hash: str  # to check for staleness
    display_format: DisplayFormat  # validated by DisplayFormat.__post_init__
    display_parameters: dict[str, str] = Field(default_factory=dict)


class QueriesRequest(BaseModel):
    """Request parameters for GET /queries"""

    server_id: str
    display_format: Json[DisplayFormat]  # sent as JSON, validated by DisplayFormat.__post_init__


class AuthMethodsResponse(BaseModel):
    sso_enabled: bool


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    refresh_token: str
    access_token: str


class RefreshRequest(BaseModel):
    refresh_token: str


class RefreshResponse(BaseModel):
    access_token: str
    refresh_token: str | None = None


class ChangePasswordRequest(BaseModel):
    new_password: str


class ChangePasswordResponse(BaseModel):
    refresh_token: str
    access_token: str


class ServersResponse(BaseModel):
    """user_infos carries what a client needs to display queries correctly using remote's own user data."""

    servers: list[dict]
    user_infos: dict


def username_from_token(token: str, token_type: TokenType, state: ServerState) -> str:
    """Verifies if the token is valid and return its username.
    401 on any failure - missing/malformed header, invalid/expired token, or a token
    issued before the user's password was last changed (see User.password_hash setter)."""

    try:
        payload = decode_token(token, state.app_ctx.settings.jwt_secret, token_type)
    except jwt.InvalidTokenError as e:
        http_error = status.HTTP_401_UNAUTHORIZED
        raise HTTPException(
            status_code=http_error, detail=f"Invalid or expired {token_type.name.lower()} token"
        ) from e

    username = payload["sub"]
    issued_at = datetime.fromtimestamp(payload["iat"], tz=timezone.utc)

    user = UserContext(state.app_ctx.users, username=username).user

    if (
        not user.exists
        or user.auth_type is AuthType.NONE
        or (user.password_changed_at is not None and issued_at < user.password_changed_at)
    ):
        if not user.exists or user.auth_type is AuthType.NONE:
            reason = "unknown user" if not user.exists else "disabled user"
            log(f"Rejected {token_type.name.lower()} token for '{username}': {reason}", logging.INFO)
        http_error = status.HTTP_401_UNAUTHORIZED
        raise HTTPException(status_code=http_error, detail=f"Invalid or expired {token_type.name.lower()} token")

    return username


def require_access_token(
    state: ServerState = Depends(get_server_state), authorization: str | None = Header(None)
) -> str:
    """FastAPI dependency: verifies the Bearer token and returns its username.
    401 on any failure - missing/malformed header, invalid/expired token, or a token
    issued before the user's password was last changed (see User.password_hash setter)."""
    if not authorization or not authorization.startswith("Bearer "):
        http_error = status.HTTP_401_UNAUTHORIZED
        raise HTTPException(status_code=http_error, detail="Missing or invalid Authorization header")

    token = authorization.removeprefix("Bearer ")
    return username_from_token(token, TokenType.ACCESS, state)


@router.get("/health")
async def health():
    """Return the health status of the server.
    Allows clients to do a quick check to confirm server is running."""
    return {"status": "ok"}


@router.get("/auth-methods")
async def auth_methods(state: ServerState = Depends(get_server_state)) -> AuthMethodsResponse:
    """Lets the client know whether a single usable OIDC provider is configured, so its remote
    config UI can offer SSO as a login method - no provider identification needed, the client
    never picks one (see /login/sso/start)."""
    usable = state.app_ctx.oidc_providers.get_usable_providers()
    return AuthMethodsResponse(sso_enabled=len(usable) == 1)


@router.post("/login")
async def login(request: LoginRequest, state: ServerState = Depends(get_server_state)) -> LoginResponse:
    """Authenticate with a username/password, return a token usable as a Bearer token on refresh route.
    Always the same generic error on failure - don't reveal whether the username exists,
    to avoid account enumeration (by response content or timing)."""
    resources: ServerResources = state.resources

    if resources.is_login_throttled(request.username):
        http_error = status.HTTP_429_TOO_MANY_REQUESTS
        raise HTTPException(status_code=http_error, detail="Too many attempts, try again later")

    user = UserContext(state.app_ctx.users, username=request.username).user
    is_basic_existing_user = user.exists and user.auth_type == AuthType.BASIC
    password_ok = verify_password(request.password, user.password_hash if is_basic_existing_user else "")

    if not is_basic_existing_user or not password_ok:
        resources.throttle_record_failing(request.username)
        http_error = status.HTTP_401_UNAUTHORIZED
        raise HTTPException(status_code=http_error, detail="Invalid username or password")

    resources.throttle_reset_failings(request.username)
    return LoginResponse(
        refresh_token=create_token(user.username, state.app_ctx.settings.jwt_secret, TokenType.REFRESH),
        access_token=create_token(user.username, state.app_ctx.settings.jwt_secret, TokenType.ACCESS),
    )


@router.post("/refresh", response_model_exclude_none=True)
async def refresh(request: RefreshRequest, state: ServerState = Depends(get_server_state)) -> RefreshResponse:
    """Authenticate with a refresh token and return a token usable as a Bearer token on other routes.
    For an OIDC user, this also periodically re-confirms with the IdP that the account is still active
    (throttled) before minting a new access token  - and, when that check just ran and succeeded, rolls
    the refresh token itself forward too. A continuously-active OIDC account never has to go through an
    interactive browser login again."""

    username = username_from_token(request.refresh_token, TokenType.REFRESH, state)

    user = UserContext(state.app_ctx.users, username=username).user
    verified = False
    if user.auth_type is AuthType.OIDC:
        try:
            verified = await verify_still_active(state.resources, state.app_ctx.oidc_providers, user)
        except OidcReverifyError as e:
            log(f"Rejected refresh token for '{username}': {e}", logging.INFO)
            http_error = status.HTTP_401_UNAUTHORIZED
            raise HTTPException(status_code=http_error, detail="Invalid or expired refresh token") from e

    refresh_token = None
    if verified:
        refresh_token = create_token(username, state.app_ctx.settings.jwt_secret, TokenType.REFRESH)

    return RefreshResponse(
        access_token=create_token(username, state.app_ctx.settings.jwt_secret, TokenType.ACCESS),
        refresh_token=refresh_token,
    )


@router.post("/change-password")
async def change_password(
    request: ChangePasswordRequest,
    username: str = Depends(require_access_token),
    state: ServerState = Depends(get_server_state),
) -> ChangePasswordResponse:
    """Self-service password change. Returns a fresh token immediately: the one used
    to authenticate this call becomes invalid after new password_hash is set."""

    user = UserContext(state.app_ctx.users, username=username).user
    user.password_hash = hash_password(request.new_password)
    user.save()

    return ChangePasswordResponse(
        refresh_token=create_token(username, state.app_ctx.settings.jwt_secret, TokenType.REFRESH),
        access_token=create_token(username, state.app_ctx.settings.jwt_secret, TokenType.ACCESS),
    )


@router.get("/servers")
async def list_servers(
    username: str = Depends(require_access_token), state: ServerState = Depends(get_server_state)
) -> ServersResponse:
    """Return the SQL servers the client can use, as id/description only, plus the user's own infos.
    The client never connects directly, the server does."""
    user_ctx = UserContext(state.app_ctx.users, username=username)
    user = user_ctx.user
    grp_authorized = user.groups if not user.admin else None
    servers_dict = filter_servers(state.app_ctx.servers.servers_dict, grp_filter=grp_authorized)

    return ServersResponse(
        servers=[{"id": s.id, "description": s.description} for s in list(servers_dict.values())],
        user_infos=user.export_infos_for_remote(),
    )


@router.get("/queries")
async def list_queries(
    request: Annotated[QueriesRequest, QueryParam()],
    username: str = Depends(require_access_token),
    state: ServerState = Depends(get_server_state),
) -> list[dict]:
    """Return the queries the client can use."""
    user_ctx = UserContext(state.app_ctx.users, username=username, display_format_override=request.display_format)
    queries, _errors = build_queries_from_parsers(state.app_ctx, user_ctx, state.query_catalog.parsers)
    filtered = filter_queries(state.app_ctx.servers, queries, request.server_id, user_ctx.user)
    return [q.serialize() for q in filtered]


@router.post("/execute")
async def execute_query(
    request: ExecuteRequest,
    http_request: Request,
    username: str = Depends(require_access_token),
    state: ServerState = Depends(get_server_state),
) -> StreamingResponse:
    """Execute query using infos sent by the client : a query_id, template hash (to check for staleness) and
    parameter values. Authorization is re-validated (query exists, is associated with server_id, user allowed)"""

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

    await semaphore.acquire()  # released by _run_query()

    # stream result - sends pings while the query is executing then NDJSON columns/rows once it completes
    return StreamingResponse(
        _run_query(state, resources, request, user_ctx, template, params, semaphore, http_request),
        media_type="application/x-ndjson",
    )


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
    """Executes the query on the server's shared, pooled driver, sending a `{"__ping__": true}` NDJSON
    line every state.heartbeat_secs while it's still running (0 disables), then streams the NDJSON
    result (_stream_result_ndjson). Releases `semaphore` once done, whichever way this ends.

    The query doesn't stream incrementally - the whole result is materialized before returning, so the
    HTTP connection to the client is completely silent for the entire query duration. Some network devices
    (NAT/firewall/proxy) cut an idle connection after a few minutes of silence even though both sides are
    fine - the ping keeps bytes flowing on it regardless of how long the query takes.

    Also lets the server notice a client disconnecting during query execution. Can't abort the SQL
    Server-side query either way (driver limitation) - the background task is still awaited to completion
    so the pooled connection is returned properly. Its result is just discarded."""

    query_id = request.query_id
    server_id = request.server_id
    username = user_ctx.user.username
    server = state.app_ctx.servers.servers_dict[request.server_id]

    async def execute() -> tuple[AsyncSqlResult, datetime]:
        async with resources.use_driver(server) as driver:
            start_time = datetime.now()
            return await driver.execute(template, params), start_time

    try:
        task = asyncio.create_task(execute())
        try:
            # emit an heartbeat while waiting for query to finish execution
            if state.heartbeat_secs > 0:
                while True:
                    done, _pending = await asyncio.wait({task}, timeout=state.heartbeat_secs)
                    if task in done:
                        break
                    if await http_request.is_disconnected():
                        log(
                            f"User '{username}' disconnected while query '{query_id}' on server '{server_id}' "
                            "was still executing.",
                            logging.INFO,
                        )
                        await asyncio.gather(task, return_exceptions=True)
                        return
                    yield json.dumps({"__ping__": True}) + "\n"
            # retrieve result generator and start_time
            result, start_time = await task
        except AsyncSqlConnectionError as e:
            log(f"Server unavailable on server '{server_id}' (user {username}) : {e}", logging.WARNING)
            yield json.dumps({"__error__": {"type": type(e).__name__, "message": f"Server unavailable: {e}"}}) + "\n"
            return
        except AsyncSqlExecutionError as e:
            msg = f"SQL error for query '{query_id}' on server '{server_id}' (user {username}): {e}"
            log(msg, logging.ERROR)
            yield json.dumps({"__error__": {"type": type(e).__name__, "message": f"SQL error: {e}"}}) + "\n"
            return
        except Exception as e:
            msg = f"Unexpected error for query '{query_id}' on server '{server_id}' (user {username}): "
            log(f"{msg} {type(e).__name__}: {e}\n{traceback.format_exc()}", logging.ERROR)
            yield json.dumps({"__error__": {"type": type(e).__name__, "message": f"Unexpected error: {e}"}}) + "\n"
            return

        # create log_entry
        log_entry = ExecutionLogEntry(
            server_id=server_id,
            user_id=username,
            user_name=user_ctx.user.title,
            query_id=query_id,
            start=start_time,
            parameters=request.display_parameters,
        )

        # stream query result
        async for chunk in stream_result_ndjson(
            result, http_request, state.chunk_size, resources.execution_logs, log_entry
        ):
            yield chunk
    finally:
        semaphore.release()
