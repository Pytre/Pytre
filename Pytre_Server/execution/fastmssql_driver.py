"""AsyncSqlDriver implementation backed by fastmssql (SQL Server only)."""

from typing import Any, cast

from fastmssql import ApplicationIntent, Connection, PoolConfig, SslConfig

from Pytre_Core.about import APP_NAME, APP_VERSION
from Pytre_Server.execution.protocol import AsyncSqlResult

# Standard recommended set for connection properties plus an explicit DATEFORMAT
_SESSION_INIT = """
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

_RECOMPILE = "\nOPTION (RECOMPILE);"


class FastMssqlDriver:
    """AsyncSqlDriver implementation using fastmssql."""

    def __init__(
        self,
        host: str,
        port: int,
        database: str,
        username: str,
        password: str,
        trust_certificate: bool = False,
        pool_config: PoolConfig | None = None,
    ) -> None:

        self._connection = Connection(
            server=host,
            port=port,
            database=database,
            username=username,
            password=password,
            pool_config=pool_config,
            ssl_config=SslConfig(trust_server_certificate=trust_certificate),
            application_name=f"{APP_NAME}_Server_{APP_VERSION}",
            application_intent=ApplicationIntent.READ_ONLY,
        )

    async def connect(self) -> None:
        await self._connection.connect()

    async def disconnect(self) -> None:
        await self._connection.disconnect()

    async def execute(self, template: str, params: list[Any]) -> AsyncSqlResult:
        sql = _SESSION_INIT + template.rstrip().rstrip(";") + _RECOMPILE
        return cast(AsyncSqlResult, await self._connection.query(sql, params))
