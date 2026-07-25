import asyncio
import jwt
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from fastmssql import PoolConfig, SqlConnectionError, SqlError
from pydantic import BaseModel

from Pytre_Core.context import AppContext, UserContext
from Pytre_Core.logs_central import ExecutionLogEntry, ServerDriven
from Pytre_Core.servers import Server, ServerType, filter_servers
from Pytre_Core.users import AuthType, hash_password, verify_password
from Pytre_Core.utils import log as _log
from Pytre_Core.sql_query.loader import build_queries_from_parsers, filter_queries

from Pytre_Server.auth_token import create_token, decode_token
from Pytre_Server.query_catalog import QueryCatalog
from Pytre_Server.result_streaming import stream_result_and_release
from Pytre_Server.execution.fastmssql_driver import FastMssqlDriver
from Pytre_Server.execution.protocol import AsyncSqlDriver
from Pytre_Server.execution.template_converter import to_positional_style


MAX_LOGIN_ATTEMPTS = 5
LOGIN_THROTTLE_WINDOW_MINUTES = 15


def log(msg: str | object, level: int = logging.INFO, sublogger: str = __name__) -> None:
    _log(msg, level=level, sublogger=sublogger)


@dataclass(frozen=True)
class ServerResources:
    """Shared, mutable state that must survive a full ServerState reload

    fastmssql.Connection is a connection pool, so one driver per server_id is
    created lazily and reused across every user's requests, never per-request."""

    drivers: dict[str, AsyncSqlDriver] = field(default_factory=dict)  # dict contents are still mutable
    drivers_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    user_semaphores: dict[str, asyncio.Semaphore] = field(default_factory=dict)
    execution_logs: ServerDriven | None = None
    ignore_hash_until: dict[str, datetime] = field(default_factory=dict)  # query_id -> hash ignored until datetime
    throttle_failed_login: dict[str, list[datetime]] = field(default_factory=dict)

    async def _get_driver(self, server: Server) -> AsyncSqlDriver:
        """Return the cached driver for `server`, or connect and cache a new one.
        Double-checked locking to avoid concurrent requests for the same not-yet-cached `server`."""
        if server.id in self.drivers:
            return self.drivers[server.id]

        async with self.drivers_lock:
            if server.id in self.drivers:  # check again: another request may have connected first
                return self.drivers[server.id]

            driver = FastMssqlDriver(
                host=server.host,
                port=int(server.port),
                database=server.database,
                username=server.user,
                password=server.password,
                pool_config=PoolConfig.performance(),
            )
            await driver.connect()  # not cached on failure - next call retries fresh
            self.drivers[server.id] = driver
            return driver

    def _get_user_semaphore(self, max_concurrency: int, username: str) -> asyncio.Semaphore:
        """Lazily creates a per-user semaphore capping concurrent /execute requests.
        No lock needed: dict access has no await point, so two "concurrent" tasks
        can't interleave between the check and the creation."""
        if username not in self.user_semaphores:
            self.user_semaphores[username] = asyncio.Semaphore(max_concurrency)
        return self.user_semaphores[username]

    def is_login_throttled(self, username: str) -> bool:
        """True if `username` has hit MAX_LOGIN_ATTEMPTS failures within the rolling window"""
        cutoff = datetime.now() - timedelta(minutes=LOGIN_THROTTLE_WINDOW_MINUTES)
        user_key = self.throttle_standardize_username(username)

        recent = [a for a in self.throttle_failed_login.get(user_key, []) if a > cutoff]
        if recent:
            self.throttle_failed_login[user_key] = recent
        else:
            self.throttle_reset_failings(user_key)

        return len(recent) >= MAX_LOGIN_ATTEMPTS

    def throttle_record_failing(self, username: str) -> None:
        user_key = self.throttle_standardize_username(username)
        self.throttle_failed_login.setdefault(user_key, []).append(datetime.now())

    def throttle_reset_failings(self, username: str) -> None:
        user_key = self.throttle_standardize_username(username)
        self.throttle_failed_login.pop(user_key, None)

    @staticmethod
    def throttle_standardize_username(username: str) -> str:
        """Standardize `username` to avoid duplicates if different case is used"""
        return username.lower()


@dataclass(frozen=True)
class ServerState:
    """App context, catalog of available queries, and per-launch configuration.
    See ServerResources for the state a reload must not reset."""

    app_ctx: AppContext
    query_catalog: QueryCatalog
    chunk_size: int  # rows to stream to client in a chunk
    max_concurrency_per_user: int  # concurrent requests allowed per user
    resources: ServerResources = field(default_factory=ServerResources)


class ExecuteRequest(BaseModel):
    """Request body for POST /execute"""

    server_id: str
    query_id: str
    template_hash: str  # to check for staleness
    parameters: dict[str, str] = {}


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str


class ChangePasswordRequest(BaseModel):
    new_password: str


class ChangePasswordResponse(BaseModel):
    token: str


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Context function to manage server shutdown tasks"""
    yield

    state: ServerState = app.state.server
    for driver in state.resources.drivers.values():
        await driver.disconnect()

    if state.resources.execution_logs is not None:
        state.resources.execution_logs.stop()


def create_app(master_key: str, chunk_size: int = 2000, max_concurrency_per_user: int = 4) -> FastAPI:
    """Build and configure the FastAPI app"""

    app = FastAPI(title="Pytre Server", lifespan=_lifespan)
    _app_ctx = AppContext(master_key=master_key)

    execution_logs: ServerDriven | None = None
    if _app_ctx.settings.logs_are_on:
        execution_logs = ServerDriven(logs_folder=_app_ctx.settings.logs_folder)

    app.state.server = ServerState(
        app_ctx=_app_ctx,
        query_catalog=QueryCatalog.load(_app_ctx.settings.queries_folder),
        chunk_size=chunk_size,
        max_concurrency_per_user=max_concurrency_per_user,
        resources=ServerResources(execution_logs=execution_logs),
    )

    def username_from_token(authorization: str | None = Header(None)) -> str:
        """FastAPI dependency: verifies the Bearer token and returns its username.
        401 on any failure - missing/malformed header, invalid/expired token, or a token
        issued before the user's password was last changed (see User.password_hash setter)."""
        if not authorization or not authorization.startswith("Bearer "):
            http_error = status.HTTP_401_UNAUTHORIZED
            raise HTTPException(status_code=http_error, detail="Missing or invalid Authorization header")

        state: ServerState = app.state.server
        try:
            claims = decode_token(authorization.removeprefix("Bearer "), state.app_ctx.settings.jwt_secret)
        except jwt.InvalidTokenError as e:
            http_error = status.HTTP_401_UNAUTHORIZED
            raise HTTPException(status_code=http_error, detail="Invalid or expired token") from e

        username = claims["sub"]
        user = UserContext(state.app_ctx.users, username=username).current_user
        issued_at = datetime.fromtimestamp(claims["iat"], tz=timezone.utc)
        if user.password_changed_at is not None and issued_at < user.password_changed_at:
            http_error = status.HTTP_401_UNAUTHORIZED
            raise HTTPException(status_code=http_error, detail="Invalid or expired token")

        return username

    @app.get("/health")
    async def health():
        """Return the health status of the server.
        Allows clients to do a quick check to confirm server is running."""
        return {"status": "ok"}

    @app.post("/login")
    async def login(request: LoginRequest) -> LoginResponse:
        """Authenticate with a username/password, return a token usable as a Bearer token on other routes.
        Always the same generic error on failure - don't reveal whether the username exists,
        to avoid account enumeration (by response content or timing)."""
        state: ServerState = app.state.server
        resources: ServerResources = state.resources

        if resources.is_login_throttled(request.username):
            http_error = status.HTTP_429_TOO_MANY_REQUESTS
            raise HTTPException(status_code=http_error, detail="Too many attempts, try again later")

        user = UserContext(state.app_ctx.users, username=request.username).current_user
        is_basic_existing_user = user.exists and user.auth_type == AuthType.BASIC
        password_ok = verify_password(request.password, user.password_hash if is_basic_existing_user else "")

        if not is_basic_existing_user or not password_ok:
            resources.throttle_record_failing(request.username)
            http_error = status.HTTP_401_UNAUTHORIZED
            raise HTTPException(status_code=http_error, detail="Invalid username or password")

        resources.throttle_reset_failings(request.username)
        return LoginResponse(token=create_token(user.username, state.app_ctx.settings.jwt_secret))

    @app.post("/change-password")
    async def change_password(
        request: ChangePasswordRequest, username: str = Depends(username_from_token)
    ) -> ChangePasswordResponse:
        """Self-service password change. Returns a fresh token immediately: the one used
        to authenticate this call becomes invalid after new password_hash is set."""
        state: ServerState = app.state.server
        user = UserContext(state.app_ctx.users, username=username).current_user
        if not user.exists:
            http_error = status.HTTP_401_UNAUTHORIZED
            raise HTTPException(status_code=http_error, detail="Invalid or expired token")

        user.password_hash = hash_password(request.new_password)
        user.save()

        return ChangePasswordResponse(token=create_token(username, state.app_ctx.settings.jwt_secret))

    @app.get("/servers")
    async def list_servers(username: str = Depends(username_from_token)) -> list[dict]:
        """Return the SQL servers the client can use, as id/description only.
        The client never connects directly, the server does."""
        state: ServerState = app.state.server
        user_ctx = UserContext(state.app_ctx.users, username=username)
        user = user_ctx.current_user
        grp_authorized = user.grp_authorized if not user.admin else None
        servers_dict = filter_servers(state.app_ctx.servers.servers_dict, grp_filter=grp_authorized)

        supported_servers = []
        for server in servers_dict.values():
            if server.type not in (ServerType.mssql.name,):
                log(f"Server '{server.id}' ignored, unsupported type: {server.type}", logging.WARNING)
                continue
            supported_servers.append(server)

        return [{"id": s.id, "description": s.description} for s in supported_servers]

    @app.get("/queries")
    async def list_queries(server_id: str, username: str = Depends(username_from_token)) -> list[dict]:
        """Return the queries the client can use."""
        state: ServerState = app.state.server
        user_ctx = UserContext(state.app_ctx.users, username=username)
        queries, errors = build_queries_from_parsers(state.app_ctx, user_ctx, state.query_catalog.parsers)
        filtered = filter_queries(state.app_ctx.servers, queries, server_id, user_ctx.current_user)
        return [q.serialize() for q in filtered]

    @app.post("/execute")
    async def execute_query(
        request: ExecuteRequest, http_request: Request, username: str = Depends(username_from_token)
    ) -> StreamingResponse:
        """Execute query using infos sent by the client : a query_id, template hash (to check for staleness) and
        parameter values. Authorization is re-validated (query exists, is associated with server_id, user allowed)"""
        state: ServerState = app.state.server
        resources: ServerResources = state.resources
        user_ctx = UserContext(state.app_ctx.users, username=username)

        # build authorized queries on the requested SQL server
        queries, _errors = build_queries_from_parsers(state.app_ctx, user_ctx, state.query_catalog.parsers)
        authorized = filter_queries(state.app_ctx.servers, queries, request.server_id, user_ctx.current_user)

        # get query version on the server matching the requested query_id
        query = next((q for q in authorized if q.name == request.query_id), None)
        if query is None:
            http_error = status.HTTP_404_NOT_FOUND
            raise HTTPException(status_code=http_error, detail="Unknown or unauthorized query_id for this server_id")

        # check if the client's template hash matches the server's version, unless exempted
        hash_exemption_until = resources.ignore_hash_until.get(query.name)
        hash_must_be_checked = hash_exemption_until is None or datetime.now() > hash_exemption_until
        if hash_must_be_checked and request.template_hash != query.get_template_hash():
            http_error = status.HTTP_409_CONFLICT
            raise HTTPException(status_code=http_error, detail="Query is not up to date, refresh your query list")

        # check the client's parameters matches what the server expects
        expected_params = set(query.parameters.params.keys())
        received_params = set(request.parameters.keys())
        if expected_params != received_params:
            missing = sorted(expected_params - received_params)
            extra = sorted(received_params - expected_params)
            http_error = status.HTTP_409_CONFLICT
            detail = f"Parameters mismatch, refresh your query list - missing: {missing}, unexpected: {extra}"
            raise HTTPException(status_code=http_error, detail=detail)

        # update query parameter values
        try:
            query.set_display_values(request.parameters)
        except ValueError as e:
            http_error = status.HTTP_400_BAD_REQUEST
            raise HTTPException(status_code=http_error, detail=f"Invalid value: {e.args[0]}") from e

        # translate command to positional style and configure SQL driver
        exec_config = query.get_exec_config(request.server_id, file_output=False)
        positional_template, params = to_positional_style(exec_config.cmd_template, exec_config.cmd_parameters)

        # cap how many requests user can have running at once (execute + streaming)
        semaphore = resources._get_user_semaphore(state.max_concurrency_per_user, username)
        if semaphore.locked():
            http_error = status.HTTP_429_TOO_MANY_REQUESTS
            raise HTTPException(status_code=http_error, detail="Too many concurrent requests, try again later")

        # execute the requested query on the server's shared, pooled driver
        await semaphore.acquire()
        success: bool = False
        try:
            server = state.app_ctx.servers.servers_dict[request.server_id]
            driver = await resources._get_driver(server)
            start_time = datetime.now()
            result = await driver.execute(positional_template, params)
            success = True
        except SqlConnectionError as e:
            http_error = status.HTTP_503_SERVICE_UNAVAILABLE
            raise HTTPException(status_code=http_error, detail=f"Server unavailable: {e}") from e
        except SqlError as e:
            http_error = status.HTTP_500_INTERNAL_SERVER_ERROR
            raise HTTPException(status_code=http_error, detail=f"SQL error: {e}") from e
        except Exception as e:
            http_error = status.HTTP_500_INTERNAL_SERVER_ERROR
            raise HTTPException(status_code=http_error, detail=f"Unexpected error: {e}") from e
        finally:
            if not success:
                semaphore.release()

        # return result
        log_entry = ExecutionLogEntry(
            server_id=request.server_id,
            user_id=user_ctx.current_user.username,
            user_name=user_ctx.current_user.title,
            query_id=request.query_id,
            start=start_time,
            parameters=request.parameters,
        )

        return StreamingResponse(
            stream_result_and_release(
                result, http_request, semaphore, state.chunk_size, resources.execution_logs, log_entry
            ),
            media_type="application/x-ndjson",
        )

    return app


if __name__ == "__main__":
    import uvicorn
    from Pytre_Server.logs import setup_file_logging, setup_terminal_logging
    from Pytre_Server.master_key import get_master_key_interactive

    setup_terminal_logging(logging.DEBUG)

    log_file = None  # specify a file to allow file logging
    if log_file is not None:
        setup_file_logging(log_file)

    master_key = get_master_key_interactive()
    if master_key is None:
        print("Startup cancelled: no master password configured.")
    else:
        uvicorn.run(create_app(master_key=master_key), host="127.0.0.1", port=8000)
