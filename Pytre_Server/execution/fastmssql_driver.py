"""AsyncSqlDriver implementation backed by fastmssql (SQL Server only)."""

from typing import Any, cast

from fastmssql import Connection, PoolConfig

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
    """AsyncSqlDriver implementation using fastmssql.

    TrustServerCertificate is hardcoded to true for now - needed for local/dev
    SQL Server instances with self-signed certificates. Must become configurable
    before any real deployment: as-is, this disables certificate validation."""

    def __init__(
        self, host: str, port: int, database: str, username: str, password: str, pool_config: PoolConfig | None = None
    ) -> None:
        self._connection = Connection(
            f"Server={host},{port};Database={database};User Id={username};Password={password};"
            + f"TrustServerCertificate=true",
            pool_config=pool_config,
        )

    async def connect(self) -> None:
        await self._connection.connect()

    async def disconnect(self) -> None:
        await self._connection.disconnect()

    async def execute(self, template: str, params: list[Any]) -> AsyncSqlResult:
        sql = _SESSION_INIT + template.rstrip().rstrip(";") + _RECOMPILE
        return cast(AsyncSqlResult, await self._connection.query(sql, params))
