"""Async SQL drivers and the protocol definitions they implement,
plus get_driver() to pick the right driver for a Server.type"""

import asyncio
import copy
import logging
import re
import uuid
from collections.abc import Callable, Coroutine
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from decimal import Decimal
from typing import Any, cast, Protocol

import asyncpg
import asyncpg.cursor
import asyncpg.transaction
import fastmssql
import pymssql

from Pytre_Core.about import APP_NAME, full_version
from Pytre_Core.servers import ServerType, Server
from Pytre_Core.utils import log as _log
from Pytre_Core.sql_query.query_drivers_mssql import CONN_PROPERTIES, connect_mssql, error_msg as mssql_error_msg
from Pytre_Core.sql_query.sql_browser import split_instance_host, resolve_instance_port

APP_ID = f"{APP_NAME}_Server_{full_version()}"

_PARAM_PATTERN = re.compile(r"%\((@[\d\w#_$@]+)\)s")


def log(msg: str | object, level: int = logging.INFO, sublogger: str = __name__) -> None:
    _log(msg, level=level, sublogger=sublogger)


async def get_driver(server: Server, pool_size: int) -> AsyncSqlDriver:
    if server.type == ServerType.mssql.name:
        return PyMssqlDriver(server, pool_size)
    elif server.type == ServerType.fastmssql.name:
        return await FastMssqlDriver._driver(server, pool_size)
    elif server.type == ServerType.postgre.name:
        return AsyncpgDriver._driver(server, pool_size)
    else:
        raise ValueError("Unknown server type, getting a connection is not possible")


# ------------------------------------------------------------------------------------------
# protocol definitions
# ------------------------------------------------------------------------------------------
class AsyncSqlExecutionError(Exception):
    """Raised when a query fails to execute for a reason other than connectivity (bad SQL, etc.)."""


class AsyncSqlConnectionError(Exception):
    """Raised when a driver fails to connect, or loses its connection mid-query."""


class AsyncSqlDriver(Protocol):
    """Async SQL Driver for one server.
    One driver instance is created per server and shared by every user. Its lifecycle is:
    connect() once, then any number of concurrent execute() calls, then disconnect().
    Errors are reported with AsyncSqlConnectionError  or AsyncSqlExecutionError."""

    async def connect(self) -> None:
        """Prepares the shared connection resources (pool)."""
        ...

    async def disconnect(self) -> None:
        """Releases connection resources (pool)."""
        ...

    async def execute(self, template: str, params: dict[str, Any]) -> AsyncSqlResult:
        """Runs a query template with %(name)s placeholders, filled from `params`.
        Returns the result to be read with fetch() then close()."""
        ...


class AsyncSqlResult(Protocol):
    """Result from a SELECT query, read batch by batch with fetch(). Keeps what reading needs (connection,
    cursor...) until close(), which must always be called - it can be called several times, and while a
    fetch() is still running."""

    def columns(self) -> list[str]: ...
    async def fetch(self, n: int) -> list[tuple[Any, ...]]: ...
    async def close(self) -> None: ...


# ------------------------------------------------------------------------------------------
# pymssql driver implementation
# ------------------------------------------------------------------------------------------
class PyMssqlDriver:
    """AsyncSqlDriver implementation using pymssql, which reads rows from the network as they are fetched.

    pymssql is synchronous: each query opens its own connection, used from the driver's own threads.
    pymssql releases the GIL while waiting for the network.
    pymssql's timeout and login_timeout are process-wide: the last connection opened sets them for every query.

    At most `pool_size` queries run at once, a query waiting longer than _acquire_timeout_secs for its turn fails."""

    _acquire_timeout_secs = 10.0

    def __init__(self, server: Server, pool_size: int = 30) -> None:
        self._server = copy.copy(server)  # parameters as they are now, edits apply once connections are reset
        self._slots = asyncio.Semaphore(pool_size)
        self._executor = ThreadPoolExecutor(max_workers=pool_size, thread_name_prefix=f"pymssql_{server.id}")

    async def connect(self) -> None:
        pass  # no pool to open, each query opens its own connection

    async def disconnect(self) -> None:
        self._executor.shutdown(wait=False)

    async def execute(self, template: str, params: dict[str, Any]) -> AsyncSqlResult:
        try:
            await asyncio.wait_for(self._slots.acquire(), timeout=self._acquire_timeout_secs)
        except TimeoutError as e:
            msg = f"No slot available within {self._acquire_timeout_secs:g} s."
            raise AsyncSqlConnectionError(msg) from e

        result = _PymssqlResult(self._executor, self._slots.release)
        try:
            await result.open(self._server, template, params)
        except BaseException:
            await result.close()  # waits for the thread to be done before closing its connection
            raise
        return result


class _PymssqlResult:
    """A query on its own pymssql connection, used by one driver thread at a time. Can't be interrupted
    from another thread, so close() waits for the running call before cancelling the query in turn."""

    def __init__(self, executor: ThreadPoolExecutor, release_slot: Callable[[], None]) -> None:
        self._executor = executor
        self._release_slot = release_slot

        self._conn: pymssql.Connection | None = None
        self._cursor: pymssql.Cursor | None = None
        self._columns: list[str] = []
        self._exhausted = False

        self._running: asyncio.Future | None = None
        self._closing: asyncio.Task | None = None

    def columns(self) -> list[str]:
        return list(self._columns)

    async def open(self, server: Server, template: str, params: dict[str, Any]) -> None:
        await self._run(self._worker_open, server, template, params)

    async def fetch(self, n: int) -> list[tuple[Any, ...]]:
        if self._exhausted or self._closing is not None:
            return []
        return await self._run(self._worker_fetch, n)

    async def close(self) -> None:
        if self._closing is None:
            self._closing = asyncio.create_task(self._close())
        await asyncio.shield(self._closing)  # a cancelled caller doesn't stop the closing

    async def _close(self) -> None:
        try:
            if self._running is not None:
                await asyncio.wait({self._running})  # whatever its outcome
            await asyncio.get_running_loop().run_in_executor(self._executor, self._worker_close)
        finally:
            self._release_slot()

    async def _run(self, func: Callable[..., Any], *args: Any) -> Any:
        """Runs `func` in a driver thread. Shielded: cancelling the caller can't stop the thread, and
        `_running` must keep tracking it until it's really done."""
        self._running = asyncio.get_running_loop().run_in_executor(self._executor, func, *args)
        return await asyncio.shield(self._running)

    # ------------------------------------------------------------------------------------------
    # worker task run from a thread
    # ------------------------------------------------------------------------------------------
    def _worker_open(self, server: Server, template: str, params: dict[str, Any]) -> None:
        try:
            self._conn = connect_mssql(server, APP_ID)
        except pymssql.Error as e:
            raise AsyncSqlConnectionError(mssql_error_msg(e)) from e

        try:
            self._cursor = self._conn.cursor()
            self._cursor.execute(template, params)
        except pymssql.Error as e:
            raise AsyncSqlExecutionError(mssql_error_msg(e)) from e

        description = self._cursor.description or ()  # None without any result set
        self._columns = [column[0] for column in description]
        self._exhausted = not description

    def _worker_fetch(self, n: int) -> list[tuple[Any, ...]]:
        if self._cursor is None:
            return []
        try:
            rows = self._cursor.fetchmany(n)
        except pymssql.Error as e:
            raise AsyncSqlExecutionError(mssql_error_msg(e)) from e
        self._exhausted = len(rows or []) < n
        return [tuple(_json_safe(value) for value in row) for row in rows or []]

    def _worker_close(self) -> None:
        if self._conn is None:
            return
        try:
            if not self._exhausted:
                self._conn._conn.cancel()  # stops the query on SQL Server - pymssql only exposes it on _conn
        except (pymssql.Error, pymssql._mssql.MSSQLException):  # _conn raises _mssql errors, not pymssql ones
            pass  # closing anyway
        finally:
            self._conn.close()
            self._conn = None
            self._cursor = None


# ------------------------------------------------------------------------------------------
# fastmssql driver implementation
# ------------------------------------------------------------------------------------------
class FastMssqlDriver:
    """AsyncSqlDriver implementation using fastmssql."""

    _conn_properties = CONN_PROPERTIES
    _recompile_option = "\nOPTION (RECOMPILE);"

    def __init__(
        self,
        host: str,
        port: int,
        database: str,
        username: str,
        password: str,
        trust_certificate: bool = False,
        pool_config: fastmssql.PoolConfig | None = None,
    ) -> None:

        self._connection = fastmssql.Connection(
            server=host,
            port=port,
            database=database,
            username=username,
            password=password,
            pool_config=pool_config,
            ssl_config=fastmssql.SslConfig(trust_server_certificate=trust_certificate),
            application_name=APP_ID,
            application_intent=fastmssql.ApplicationIntent.READ_ONLY,
        )

    async def connect(self) -> None:
        try:
            await self._connection.connect()
        except fastmssql.SqlConnectionError as e:
            raise AsyncSqlConnectionError(str(e)) from e

    async def disconnect(self) -> None:
        await self._connection.disconnect()

    async def execute(self, template: str, params: dict[str, Any]) -> AsyncSqlResult:
        # translate command and parameters to positional style to match fastmssql query args
        positional_template, params_lst = _to_positional_style(template, params, "@P{}")

        # add session options
        sql = self._conn_properties + positional_template.rstrip().rstrip(";") + self._recompile_option

        try:
            return _FastMssqlResult(await self._connection.query(sql, params_lst))
        except fastmssql.SqlConnectionError as e:
            raise AsyncSqlConnectionError(str(e)) from e
        except fastmssql.SqlError as e:
            raise AsyncSqlExecutionError(str(e)) from e

    @classmethod
    async def _driver(cls, server: Server, pool_size: int) -> FastMssqlDriver:
        """Returns an mssql driver. Resolve host/port connection target.
        Values other than the pool size are those of fastmssql's performance()."""
        hostname, port = await cls._resolve_conn_host(server.host, int(server.port))

        return FastMssqlDriver(
            host=hostname,
            port=port,
            database=server.database,
            username=server.user,
            password=server.password,
            trust_certificate=server.trust_server_certificate,
            pool_config=fastmssql.PoolConfig(
                max_size=pool_size,
                min_idle=min(10, pool_size),
                max_lifetime_secs=7200,
                idle_timeout_secs=1800,
                connection_timeout_secs=10,
            ),
        )

    @classmethod
    async def _resolve_conn_host(cls, host: str, port: int) -> tuple[str, int]:
        """Return (hostname, port) to actually connect to.
        If `host` has no named-instance suffix (no backslash), returns it unchanged - no network call.
        Otherwise resolves the instance's real TCP port via the SQL Browser service;
        falls back to the configured port unchanged if resolution fails (Browser service disabled/unreachable)."""

        hostname, instance_name = split_instance_host(host)
        target = (hostname, int(port))

        if instance_name is None:
            log(f"'{host}' has no named-instance suffix, using configured port as-is", logging.DEBUG)
        else:
            resolved_port = await resolve_instance_port(hostname, instance_name)
            if resolved_port is not None:
                target = (hostname, resolved_port)

        return target


class _FastMssqlResult:
    """fastmssql loaded every row before query() returns, and its pooled connection is already
    released - fetch() only converts rows batch by batch."""

    def __init__(self, stream: fastmssql.QueryStream) -> None:
        self._stream: fastmssql.QueryStream = stream

    def columns(self) -> list[str]:
        try:
            return self._stream.columns()
        except ValueError:
            return []  # fastmssql can't tell the column names if no line is returned

    async def fetch(self, n: int) -> list[tuple[Any, ...]]:
        return [tuple(_json_safe(value) for value in row.values()) for row in self._stream.fetch(n)]

    async def close(self) -> None:
        pass


# ------------------------------------------------------------------------------------------
# asyncpg driver implementation
# ------------------------------------------------------------------------------------------
class AsyncpgDriver:
    """AsyncSqlDriver implementation using asyncpg.
    Rows are read through a cursor, each query keeps its pooled connection until its result is closed.
    statement_timeout applies to each fetch, not to the whole query: PostgreSQL restarts it on each batch asked."""

    _min_pool_size = 2
    _acquire_timeout_secs = 10.0

    _connection_errors = (
        OSError,  # includes TimeoutError (pool saturated, connect timeout), ssl.SSLError, gaierror
        asyncpg.InterfaceError,
        asyncpg.PostgresConnectionError,
        asyncpg.InvalidAuthorizationSpecificationError,
        asyncpg.CannotConnectNowError,
        asyncpg.TooManyConnectionsError,
    )

    def __init__(
        self,
        host: str,
        port: int,
        database: str,
        username: str,
        password: str,
        trust_certificate: bool = False,
        login_timeout: int = 60,
        statement_timeout_secs: int = 300,
        pool_size: int = 30,
    ) -> None:

        self._host = host
        self._port = port
        self._database = database
        self._username = username
        self._password = password
        self._login_timeout = login_timeout
        self._statement_timeout_ms = int(statement_timeout_secs) * 1000
        self._pool_size = pool_size
        self._pool: asyncpg.Pool | None = None

        # secure by default (certificate chain and host name verified). Trusting the server means accepting
        # what it offers: TLS without verification if available, plain connection otherwise.
        self._ssl: bool | str = "prefer" if trust_certificate else True

    async def connect(self) -> None:
        try:
            self._pool = await asyncpg.create_pool(
                host=self._host,
                port=self._port,
                database=self._database,
                user=self._username,
                password=self._password,
                ssl=self._ssl,
                min_size=min(self._min_pool_size, self._pool_size),
                max_size=self._pool_size,
                timeout=self._login_timeout,
                server_settings={
                    "application_name": APP_ID,
                    "default_transaction_read_only": "on",
                    "statement_timeout": str(self._statement_timeout_ms),
                },
            )
        except (*self._connection_errors, asyncpg.PostgresError) as e:  # authentication, unknown database...
            raise AsyncSqlConnectionError(self._error_msg(e)) from e

    async def disconnect(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def execute(self, template: str, params: dict[str, Any]) -> AsyncSqlResult:
        if self._pool is None:
            raise AsyncSqlConnectionError("Not connected")

        # translate command to $1, $2... style; templates are shared with psycopg2 which needs %% for a literal %
        sql, values = _to_positional_style(template, params, "${}")
        sql = sql.replace("%%", "%")

        try:
            conn = await self._pool.acquire(timeout=self._acquire_timeout_secs)
        except self._connection_errors as e:
            raise AsyncSqlConnectionError(self._error_msg(e)) from e

        result = _AsyncpgResult(self._pool, cast(asyncpg.Connection, conn))
        try:
            await result.open(sql, values)
        except BaseException:
            await result.close()  # rolls the transaction back and gives the connection back to the pool
            raise
        return result

    @staticmethod
    def _coerce_args(param_types: Any, values: list) -> list:
        # asyncpg types query arguments server-side and refuses a str for an int/date/timestamp/bool argument,
        # whereas Pytre gives str/int command parameters - coerce by the type PostgreSQL expects
        coercers: dict[str, Callable[[Any], Any]] = {
            "int2": int,
            "int4": int,
            "int8": int,
            "float4": float,
            "float8": float,
            "bool": lambda v: bool(int(v)),
            "date": lambda v: date.fromisoformat(v) if isinstance(v, str) else v,
            "timestamp": lambda v: datetime.fromisoformat(v) if isinstance(v, str) else v,
            "timestamptz": lambda v: datetime.fromisoformat(v) if isinstance(v, str) else v,
            "uuid": lambda v: uuid.UUID(v) if isinstance(v, str) else v,
        }

        args = []
        for position, (pg_type, value) in enumerate(zip(param_types, values), start=1):
            coercer = coercers.get(pg_type.name)
            if value is None or coercer is None:
                args.append(value)
                continue
            try:
                args.append(coercer(value))
            except (ValueError, TypeError) as e:
                msg = f"Invalid value for parameter ${position} ({pg_type.name}): {value!r}"
                raise AsyncSqlExecutionError(msg) from e

        return args

    @staticmethod
    def _error_msg(error: Exception) -> str:
        return str(error) or type(error).__name__  # e.g. TimeoutError has an empty message

    @classmethod
    def _driver(cls, server: Server, pool_size: int) -> AsyncpgDriver:
        """Returns a postgre driver built from server's parameters."""
        return AsyncpgDriver(
            host=server.host,
            port=int(server.port),
            database=server.database,
            username=server.user,
            password=server.password,
            trust_certificate=server.trust_server_certificate,
            login_timeout=server.login_timeout,
            statement_timeout_secs=server.timeout,
            pool_size=pool_size,
        )


class _AsyncpgResult:
    """A query read through a cursor, in a read-only transaction, on its own pooled connection until close().
    Cancelling a running fetch() also cancels the query on PostgreSQL."""

    def __init__(self, pool: asyncpg.Pool, conn: asyncpg.Connection) -> None:
        self._pool = pool
        self._conn = conn

        self._transaction: asyncpg.transaction.Transaction | None = None
        self._cursor: asyncpg.cursor.Cursor | None = None
        self._columns: list[str] = []

        self._running: asyncio.Future | None = None
        self._closing: asyncio.Task | None = None

    def columns(self) -> list[str]:
        return list(self._columns)

    async def open(self, sql: str, values: list) -> None:
        await self._run(self._task_open(sql, values))

    async def fetch(self, n: int) -> list[tuple[Any, ...]]:
        if self._cursor is None or self._closing is not None:
            return []
        records: list[asyncpg.Record] = await self._run(self._cursor.fetch(n))
        return [tuple(_json_safe(value) for value in record.values()) for record in records]

    async def close(self) -> None:
        if self._closing is None:
            self._closing = asyncio.create_task(self._task_close())
        await asyncio.shield(self._closing)  # a cancelled caller doesn't stop the closing

    async def _run(self, operation: Coroutine[Any, Any, Any]) -> Any:
        """Runs `operation` as a task that close() can cancel. Not shielded: cancelling the caller cancels
        the query too. asyncpg errors are translated."""
        self._running = asyncio.ensure_future(operation)
        try:
            return await self._running
        except AsyncpgDriver._connection_errors as e:
            raise AsyncSqlConnectionError(AsyncpgDriver._error_msg(e)) from e
        except asyncpg.PostgresError as e:
            raise AsyncSqlExecutionError(AsyncpgDriver._error_msg(e)) from e

    async def _task_open(self, sql: str, values: list) -> None:
        self._transaction = self._conn.transaction(readonly=True)  # a cursor only lives in a transaction
        await self._transaction.start()
        statement = await self._conn.prepare(sql)
        self._columns = [cast(asyncpg.types.Attribute, attribute).name for attribute in statement.get_attributes()]
        args = AsyncpgDriver._coerce_args(statement.get_parameters(), values)
        self._cursor = await statement.cursor(*args)

    async def _task_close(self) -> None:
        try:
            if self._running is not None:
                self._running.cancel()  # no effect once done, otherwise asyncpg cancels the query on PostgreSQL
                await asyncio.wait({self._running})
            if self._transaction is not None:
                await self._transaction.rollback()  # waits for a cancellation to be done first
        except (*AsyncpgDriver._connection_errors, asyncpg.PostgresError):
            self._conn.terminate()  # can't be reset, the pool replaces it
        finally:
            await self._pool.release(self._conn)


# ------------------------------------------------------------------------------------------
# Other functions
# ------------------------------------------------------------------------------------------
def _json_safe(value: Any, nested: bool = False) -> Any:
    """Turns a database value into something json.dumps accepts, once encode_row_value() has run on it:
    that encoder handles Decimal/datetime/date at top level only, everything else JSON can't
    represent (UUID, time, timedelta, IP addresses...) becomes a string - the client displays text anyway."""

    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    elif not nested and isinstance(value, (Decimal, datetime, date)):
        return value
    elif isinstance(value, list):
        return [_json_safe(item, nested=True) for item in value]
    elif isinstance(value, bytes):
        return "\\x" + value.hex()
    else:
        return str(value)


def _to_positional_style(cmd_template: str, cmd_parameters: dict[str, Any], placeholder: str) -> tuple[str, list]:
    """Convert a template using %(name)s style and a dict for parameters into a template
    using positional `placeholder` style (e.g "@P{}" for @P1, @P2, ...) and an ordered list of parameters."""

    name_pos: dict[str, int] = {}

    def replace_func(match: re.Match) -> str:
        name = match.group(1)
        if name not in name_pos:
            name_pos[name] = len(name_pos) + 1
        return placeholder.format(name_pos[name])

    template = _PARAM_PATTERN.sub(replace_func, cmd_template)
    parameters = [cmd_parameters[name] for name in name_pos.keys()]

    return template, parameters
