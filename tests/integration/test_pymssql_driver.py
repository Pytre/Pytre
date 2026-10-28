"""
Integration tests for the pymssql driver (Pytre_Core/sql_query/query_drivers_async.py).
Requires the local mssql-test docker container (see tests/resources/build_test_keepass.py for
its pinned connection details) - skipped automatically if it isn't reachable.
"""

import asyncio
import json
import time
from typing import Any
from unittest.mock import Mock

import pytest

from Pytre_Core.remotes import encode_row_value
from Pytre_Core.servers import Server, ServerType
from Pytre_Core.sql_query.query_drivers_async import (
    APP_ID,
    AsyncSqlConnectionError,
    AsyncSqlExecutionError,
    FastMssqlDriver,
    PyMssqlDriver,
)
from tests.integration.conftest import requires_docker
from tests.resources.build_test_keepass import TEST_SQL_SERVER

pytestmark = pytest.mark.integration

_BIG_QUERY = """
SELECT TOP (500000) ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) AS n
FROM sys.all_columns a CROSS JOIN sys.all_columns b
"""


def _server(**overrides: Any) -> Server:
    server = Server(Mock())
    server.id = TEST_SQL_SERVER["id"]
    server.type = ServerType.mssql.name
    server.host = TEST_SQL_SERVER["host"]
    server.port = TEST_SQL_SERVER["port"]
    server.database = TEST_SQL_SERVER["database"]
    server.user = TEST_SQL_SERVER["user"]
    server.password = TEST_SQL_SERVER["password"]
    server.charset = "UTF-8"
    for name, value in overrides.items():
        setattr(server, name, value)
    return server


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def driver():
    d = PyMssqlDriver(_server())
    await d.connect()
    yield d
    await d.disconnect()


async def _rows(driver, template: str, params: dict | None = None) -> list[tuple]:
    result = await driver.execute(template, params or {})
    try:
        return await result.fetch(1000)
    finally:
        await result.close()


# ================================================================================
# parameters and results
# ================================================================================


@requires_docker
@pytest.mark.anyio
async def test_named_params_and_literal_percent(driver):
    """Same template as the client's pymssql path: %(name)s parameters, a literal % left as is."""
    assert await _rows(driver, "SELECT %(@ID)s AS id, 'a%b' AS x", {"@ID": 7}) == [(7, "a%b")]


@requires_docker
@pytest.mark.anyio
async def test_rows_are_fetched_batch_by_batch(driver):
    result = await driver.execute("SELECT TOP (5) ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) AS n FROM sys.all_columns", {})
    try:
        batches = [await result.fetch(2) for _ in range(4)]
    finally:
        await result.close()

    assert result.columns() == ["n"]
    assert batches == [[(1,), (2,)], [(3,), (4,)], [(5,)], []]


@requires_docker
@pytest.mark.anyio
async def test_columns_are_available_without_any_row(driver):
    result = await driver.execute("SELECT 1 AS a, 'x' AS b WHERE 1 = 0", {})

    assert result.columns() == ["a", "b"]
    assert await result.fetch(10) == []
    await result.close()


_TYPED_QUERY = """
SELECT
    CAST(42 AS int) AS i, CAST(1 AS bit) AS b, CAST(1.50 AS decimal(10, 2)) AS dec, CAST(0.5 AS float) AS f,
    CAST(N'café ✓' AS nvarchar(20)) AS nv, CAST('2024-01-02' AS date) AS d,
    CAST('2024-01-02 03:04:05.123' AS datetime2(3)) AS dt2, CAST('2024-01-02 03:04:05' AS datetime) AS dt,
    CAST('13:05:09' AS time(0)) AS tm, CAST('9836F28D-CBDB-4D34-95D2-D4110F4D37FF' AS uniqueidentifier) AS u,
    CAST(0xDEAD AS varbinary(2)) AS bin, CAST(NULL AS int) AS n
"""


@requires_docker
@pytest.mark.anyio
async def test_values_are_json_serializable_whatever_the_column_type(driver):
    [row] = await _rows(driver, _TYPED_QUERY)

    json.dumps([encode_row_value(value) for value in row])  # must not raise


@requires_docker
@pytest.mark.anyio
async def test_values_are_the_same_as_with_fastmssql(driver):
    """Switching a server between "SQL Server" and "SQL Server (fastmssql)" must not change what clients receive."""
    fast_driver = FastMssqlDriver(
        host=TEST_SQL_SERVER["host"],
        port=int(TEST_SQL_SERVER["port"]),
        database=TEST_SQL_SERVER["database"],
        username=TEST_SQL_SERVER["user"],
        password=TEST_SQL_SERVER["password"],
        trust_certificate=True,  # mssql-test docker uses a self-signed certificate
    )
    await fast_driver.connect()
    try:
        [fast_row] = await _rows(fast_driver, _TYPED_QUERY)
    finally:
        await fast_driver.disconnect()

    [row] = await _rows(driver, _TYPED_QUERY)

    columns = ["i", "b", "dec", "f", "nv", "d", "dt2", "dt", "tm", "u", "bin", "n"]
    encoded = dict(zip(columns, (encode_row_value(value) for value in row)))
    fast_encoded = dict(zip(columns, (encode_row_value(value) for value in fast_row)))
    assert encoded == fast_encoded


# ================================================================================
# errors and guarantees
# ================================================================================


@requires_docker
@pytest.mark.anyio
async def test_sql_error_is_an_execution_error_and_the_driver_stays_usable(driver):
    with pytest.raises(AsyncSqlExecutionError) as exc_info:
        await driver.execute("SELECT * FROM pytre_no_such_table", {})

    assert str(exc_info.value).startswith("Invalid object name 'pytre_no_such_table'.")
    assert await _rows(driver, "SELECT 42 AS answer") == [(42,)]


@requires_docker
@pytest.mark.anyio
async def test_wrong_password_is_a_connection_error():
    driver = PyMssqlDriver(_server(password="not-the-password"))
    try:
        with pytest.raises(AsyncSqlConnectionError):
            await driver.execute("SELECT 1", {})
    finally:
        await driver.disconnect()


@requires_docker
@pytest.mark.anyio
async def test_sessions_are_named_after_pytre_server(driver):
    assert await _rows(driver, "SELECT APP_NAME() AS app") == [(APP_ID,)]


@requires_docker
@pytest.mark.anyio
async def test_saturated_pool_is_a_connection_error():
    driver = PyMssqlDriver(_server(), pool_size=1)
    driver._acquire_timeout_secs = 0.5
    try:
        first = await driver.execute("SELECT 1 AS n", {})
        with pytest.raises(AsyncSqlConnectionError):
            await driver.execute("SELECT 1 AS n", {})
        await first.close()

        assert await _rows(driver, "SELECT 2 AS n") == [(2,)]  # closing gave the slot back
    finally:
        await driver.disconnect()


async def _sessions_of_pytre_server(driver) -> int:
    [(count,)] = await _rows(
        driver,
        "SELECT COUNT(*) FROM sys.dm_exec_sessions WHERE program_name = %(@app)s AND session_id <> @@SPID",
        {"@app": APP_ID},
    )
    return count


@requires_docker
@pytest.mark.anyio
async def test_closing_before_the_last_row_stops_the_query(driver):
    result = await driver.execute(_BIG_QUERY, {})
    assert len(await result.fetch(100)) == 100

    start = time.monotonic()
    await result.close()

    assert time.monotonic() - start < 5  # the remaining rows are cancelled, not read
    for _ in range(20):  # SQL Server drops the session shortly after the connection closes
        if await _sessions_of_pytre_server(driver) == 0:
            break
        await asyncio.sleep(0.1)
    assert await _sessions_of_pytre_server(driver) == 0


@requires_docker
@pytest.mark.anyio
async def test_event_loop_stays_responsive_during_a_long_fetch(driver):
    """Rows are read from a driver thread: pymssql releases the GIL while waiting for the network."""
    timestamps: list[float] = []
    stop = asyncio.Event()

    async def heartbeat() -> None:
        while not stop.is_set():
            await asyncio.sleep(0.01)
            timestamps.append(time.monotonic())

    heartbeat_task = asyncio.create_task(heartbeat())
    result = await driver.execute(_BIG_QUERY, {})
    try:
        start = time.monotonic()
        rows = await result.fetch(500_001)
        end = time.monotonic()
    finally:
        await result.close()
        stop.set()
        await heartbeat_task

    edges = [start] + [t for t in timestamps if start <= t <= end] + [end]
    assert len(rows) == 500_000
    assert max(b - a for a, b in zip(edges, edges[1:])) < 0.5
