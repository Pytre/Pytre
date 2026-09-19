"""Async SQL drivers and the protocol definitions they implement,
plus get_driver() to pick the right driver for a Server.type"""

import logging
import re
from collections.abc import Iterator
from typing import Any, cast, Protocol

import fastmssql

from Pytre_Core.about import APP_NAME, APP_VERSION
from Pytre_Core.servers import ServerType, Server
from Pytre_Core.utils import log as _log
from Pytre_Core.sql_query.sql_browser import split_instance_host, resolve_instance_port


_PARAM_PATTERN = re.compile(r"%\((@[\d\w#_$@]+)\)s")


def log(msg: str | object, level: int = logging.INFO, sublogger: str = __name__) -> None:
    _log(msg, level=level, sublogger=sublogger)


async def get_driver(server: Server) -> AsyncSqlDriver:
    if server.type == ServerType.mssql.name:
        driver = await FastMssqlDriver._driver(server)
        return cast(AsyncSqlDriver, driver)
    # elif server.type == ServerType.postgre.name:
    #    return _driver_postgre(server)
    else:
        raise ValueError("Unknown server type, getting a connection is not possible")


# ------------------------------------------------------------------------------------------
# protocol definitions
# ------------------------------------------------------------------------------------------
class AsyncSqlExecutionError(Exception):
    """Raised when a query fails to execute for a reason other than connectivity (bad SQL, etc.)."""


class AsyncSqlConnectionError(Exception):
    """Raised when a driver fails to connect, or loses its connection mid-query."""


class SqlRow(Protocol):
    """A single row from a SELECT query result."""

    def to_dict(self) -> dict[str, Any]: ...


class AsyncSqlResult(Protocol):
    """Result from SELECT query. Rows expose to_dict() rather than a full Mapping interface ;
    fastmssql's FastRow has no .keys()/.items(), only to_dict()/get()/get_by_index()/values()."""

    def __iter__(self) -> Iterator[SqlRow]: ...
    def columns(self) -> list[str]: ...


class AsyncSqlDriver(Protocol):
    """Async SQL Driver : fastmssql for now, designed to allow asyncpg in a later phase."""

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def execute(self, template: str, params: dict[str, Any]) -> AsyncSqlResult: ...


# ------------------------------------------------------------------------------------------
# drivers implementation
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
            application_name=f"{APP_NAME}_Server_{APP_VERSION}",
            application_intent=fastmssql.ApplicationIntent.READ_ONLY,
        )

    async def connect(self) -> None:
        try:
            await self._connection.connect()
        except fastmssql.SqlConnectionError as e:
            raise AsyncSqlConnectionError(str(e)) from e

    async def disconnect(self) -> None:
        await self._connection.disconnect()

    async def execute(self, template: str, params_dict: dict[str, Any]) -> AsyncSqlResult:
        # translate command and paramters to positional style to match fastmssql query args
        positional_template, params_lst = _to_positional_style(template, params_dict)

        # add session options
        sql = self._conn_properties + positional_template.rstrip().rstrip(";") + self._recompile_option

        try:
            return cast(AsyncSqlResult, await self._connection.query(sql, params_lst))
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


# ------------------------------------------------------------------------------------------
# Other functions
# ------------------------------------------------------------------------------------------
def _to_positional_style(cmd_template: str, cmd_parameters: dict[str, Any]) -> tuple[str, list]:
    """Convert a template using %(name)s style and a dict for parameters into a
    template using @P1, @P2... style and an ordered list of parameters."""

    name_pos: dict[str, int] = {}

    def replace_func(match: re.Match) -> str:
        name = match.group(1)
        if name not in name_pos:
            name_pos[name] = len(name_pos) + 1
        return f"@P{name_pos[name]}"

    template = _PARAM_PATTERN.sub(replace_func, cmd_template)
    parameters = [cmd_parameters[name] for name in name_pos.keys()]

    return template, parameters
