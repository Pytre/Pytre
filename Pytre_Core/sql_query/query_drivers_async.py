"""Async SQL drivers and the protocol definitions they implement,
plus get_driver() to pick the right driver for a Server.type"""

import logging
import re
import uuid
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from typing import Any, cast, Protocol

import asyncpg
import fastmssql

from Pytre_Core.about import APP_NAME, full_version
from Pytre_Core.servers import ServerType, Server
from Pytre_Core.utils import log as _log
from Pytre_Core.sql_query.sql_browser import split_instance_host, resolve_instance_port

APP_ID = f"{APP_NAME}_Server_{full_version()}"

_PARAM_PATTERN = re.compile(r"%\((@[\d\w#_$@]+)\)s")


def log(msg: str | object, level: int = logging.INFO, sublogger: str = __name__) -> None:
    _log(msg, level=level, sublogger=sublogger)


async def get_driver(server: Server) -> AsyncSqlDriver:
    if server.type == ServerType.mssql.name:
        return await FastMssqlDriver._driver(server)
    elif server.type == ServerType.postgre.name:
        return AsyncpgDriver._driver(server)
    else:
        raise ValueError("Unknown server type, getting a connection is not possible")


# ------------------------------------------------------------------------------------------
# protocol definitions
# ------------------------------------------------------------------------------------------
class AsyncSqlExecutionError(Exception):
    """Raised when a query fails to execute for a reason other than connectivity (bad SQL, etc.)."""


class AsyncSqlConnectionError(Exception):
    """Raised when a driver fails to connect, or loses its connection mid-query."""


class AsyncSqlResult(Protocol):
    """Result from a SELECT query, read batch by batch with fetch(). Keeps what reading needs (connection,
    cursor...) until close(), which must always be called - it can be called several times, and while a
    fetch() is still running."""

    def columns(self) -> list[str]: ...
    async def fetch(self, n: int) -> list[tuple[Any, ...]]: ...
    async def close(self) -> None: ...


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


# ------------------------------------------------------------------------------------------
# fastmssql driver implementation
# ------------------------------------------------------------------------------------------
class FastMssqlDriver:
    """AsyncSqlDriver implementation using fastmssql."""

    _conn_properties = """
        SET ARITHABORT ON;
        SET CONCAT_NULL_YIELDS_NULL ON;
        SET ANSI_NULLS ON;
        SET ANSI_NULL_DFLT_ON ON;
        SET ANSI_PADDING ON;
        SET ANSI_WARNINGS ON;
        SET CURSOR_CLOSE_ON_COMMIT ON;
        SET QUOTED_IDENTIFIER ON;
        SET TEXTSIZE 2147483647;
        SET DATEFORMAT ymd;
    """
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
    async def _driver(cls, server: Server) -> FastMssqlDriver:
        """Returns an mssql driver. Resolve host/port connection target."""
        hostname, port = await cls._resolve_conn_host(server.host, int(server.port))

        return FastMssqlDriver(
            host=hostname,
            port=port,
            database=server.database,
            username=server.user,
            password=server.password,
            trust_certificate=server.trust_server_certificate,
            pool_config=fastmssql.PoolConfig.performance(),
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
        return [tuple(row.values()) for row in self._stream.fetch(n)]

    async def close(self) -> None:
        pass


# ------------------------------------------------------------------------------------------
# asyncpg driver implementation
# ------------------------------------------------------------------------------------------
class AsyncpgDriver:
    """AsyncSqlDriver implementation using asyncpg."""

    _min_pool_size = 2
    _max_pool_size = 30
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
    ) -> None:

        self._host = host
        self._port = port
        self._database = database
        self._username = username
        self._password = password
        self._login_timeout = login_timeout
        self._statement_timeout_ms = int(statement_timeout_secs) * 1000
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
                min_size=self._min_pool_size,
                max_size=self._max_pool_size,
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
            async with self._pool.acquire(timeout=self._acquire_timeout_secs) as conn:
                statement = await cast(asyncpg.Connection, conn).prepare(sql)
                columns = [cast(asyncpg.types.Attribute, attribute).name for attribute in statement.get_attributes()]
                args = self._coerce_args(statement.get_parameters(), values)
                records = await statement.fetch(*args)
        except AsyncSqlExecutionError:
            raise
        except self._connection_errors as e:
            raise AsyncSqlConnectionError(self._error_msg(e)) from e
        except asyncpg.PostgresError as e:
            raise AsyncSqlExecutionError(self._error_msg(e)) from e

        return _AsyncpgResult(columns, records)

    def _coerce_args(self, param_types: Any, values: list) -> list:
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

    def _error_msg(self, error: Exception) -> str:
        return str(error) or type(error).__name__  # e.g. TimeoutError has an empty message

    @classmethod
    def _driver(cls, server: Server) -> AsyncpgDriver:
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
        )


class _AsyncpgResult:
    """Every record is loaded by execute(), fetch() returns them batch by batch."""

    def __init__(self, columns: list[str], records: list[asyncpg.Record]) -> None:
        self._columns: list[str] = columns
        self._records: list[asyncpg.Record] = records
        self._position: int = 0

    def columns(self) -> list[str]:
        return list(self._columns)

    async def fetch(self, n: int) -> list[tuple[Any, ...]]:
        batch = self._records[self._position : self._position + n]
        self._position += len(batch)
        return [tuple(_json_safe(value) for value in record.values()) for record in batch]

    async def close(self) -> None:
        self._records = []


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
