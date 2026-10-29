"""
Integration tests for the asyncpg driver (Pytre_Core/sql_query/query_drivers_async.py).
Requires the local postgre-test docker container (see tests/resources/docker-compose.postgre.yml and
tests/resources/build_test_keepass.py for its pinned connection details) - started automatically,
skipped if it can't be reached.
"""

import asyncio
import json
import time
from typing import Any

import pytest

from Pytre_Core.remotes import encode_row_value
from Pytre_Core.sql_query.query_drivers_async import (
    APP_ID,
    AsyncpgDriver,
    AsyncSqlConnectionError,
    AsyncSqlExecutionError,
)
from tests.integration.conftest import requires_postgre_docker
from tests.resources.build_test_keepass import TEST_PG_SERVER

pytestmark = pytest.mark.integration

_CONNECTION: dict[str, Any] = {
    "host": TEST_PG_SERVER["host"],
    "port": int(TEST_PG_SERVER["port"]),
    "database": TEST_PG_SERVER["database"],
    "username": TEST_PG_SERVER["user"],
    "password": TEST_PG_SERVER["password"],
}


def _connection(**overrides: Any) -> dict[str, Any]:
    return {**_CONNECTION, **overrides}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def driver():
    d = AsyncpgDriver(**_CONNECTION, trust_certificate=True, statement_timeout_secs=2)  # self-signed certificate
    await d.connect()
    yield d
    await d.disconnect()


async def _rows(driver: AsyncpgDriver, template: str, params: dict | None = None) -> list[dict]:
    result = await driver.execute(template, params or {})
    try:
        rows = await result.fetch(1000)
        return [dict(zip(result.columns(), row)) for row in rows]
    finally:
        await result.close()


# ================================================================================
# connection
# ================================================================================


@requires_postgre_docker
@pytest.mark.anyio
async def test_trusted_connection_accepts_the_self_signed_certificate(driver):
    assert await _rows(driver, "SELECT 1 AS n") == [{"n": 1}]


@requires_postgre_docker
@pytest.mark.anyio
async def test_certificate_is_verified_unless_the_server_is_trusted():
    driver = AsyncpgDriver(**_CONNECTION, trust_certificate=False)

    with pytest.raises(AsyncSqlConnectionError) as exc_info:
        await driver.connect()

    assert "CERTIFICATE_VERIFY_FAILED" in str(exc_info.value)


@requires_postgre_docker
@pytest.mark.anyio
async def test_wrong_password_is_a_connection_error():
    driver = AsyncpgDriver(**_connection(password="not-the-password"), trust_certificate=True)

    with pytest.raises(AsyncSqlConnectionError):
        await driver.connect()


@requires_postgre_docker
@pytest.mark.anyio
async def test_unreachable_server_is_a_connection_error():
    driver = AsyncpgDriver(**_connection(port=5999), trust_certificate=True)

    with pytest.raises(AsyncSqlConnectionError):
        await driver.connect()


@requires_postgre_docker
@pytest.mark.anyio
async def test_execute_after_disconnect_is_a_connection_error(driver):
    await driver.disconnect()

    with pytest.raises(AsyncSqlConnectionError):
        await driver.execute("SELECT 1", {})


# ================================================================================
# parameters
# ================================================================================


@requires_postgre_docker
@pytest.mark.anyio
async def test_string_parameters_are_coerced_to_the_expected_types(driver):
    """asyncpg alone refuses a str for an int/date/timestamp/bool argument - Pytre sends str/int."""
    rows = await _rows(
        driver,
        "SELECT (5 = %(@i)s) AS i, (DATE '2024-01-02' = %(@d)s) AS d, "
        "(TIMESTAMP '2024-01-02 03:04:05' = %(@ts)s) AS ts, (TRUE = %(@b)s) AS b",
        {"@i": "5", "@d": "2024-01-02", "@ts": "2024-01-02 03:04:05", "@b": 1},
    )

    assert rows == [{"i": True, "d": True, "ts": True, "b": True}]


@requires_postgre_docker
@pytest.mark.anyio
async def test_repeated_and_null_parameters(driver):
    rows = await _rows(
        driver, "SELECT %(@a)s::text AS a1, %(@a)s::text AS a2, %(@n)s::int AS n", {"@a": "z", "@n": None}
    )

    assert rows == [{"a1": "z", "a2": "z", "n": None}]


@requires_postgre_docker
@pytest.mark.anyio
async def test_invalid_parameter_value_is_an_execution_error(driver):
    with pytest.raises(AsyncSqlExecutionError) as exc_info:
        await driver.execute("SELECT 1 WHERE 1 = %(@i)s::int", {"@i": "abc"})

    assert "$1" in str(exc_info.value)


@requires_postgre_docker
@pytest.mark.anyio
async def test_percent_escaped_for_psycopg2_is_a_single_percent(driver):
    """Templates are shared with the local psycopg2 path, which needs %% for a literal %."""
    rows = await _rows(driver, "SELECT 'a%%b' AS x, ('abc' LIKE 'a%%') AS m")

    assert rows == [{"x": "a%b", "m": True}]


# ================================================================================
# results
# ================================================================================


@requires_postgre_docker
@pytest.mark.anyio
async def test_columns_are_available_without_any_row(driver):
    result = await driver.execute("SELECT 1 AS a, 'x' AS b WHERE false", {})

    assert result.columns() == ["a", "b"]
    assert await result.fetch(10) == []
    await result.close()


@requires_postgre_docker
@pytest.mark.anyio
async def test_results_are_json_serializable_whatever_the_column_type(driver):
    """UUID/interval/time/bytea/inet/arrays would break json.dumps in _stream_result_ndjson otherwise."""
    rows = await _rows(
        driver,
        "SELECT gen_random_uuid() AS u, '1 day'::interval AS iv, current_time AS tm, '\\xdead'::bytea AS b, "
        "ARRAY[1.5, 2.5]::numeric[] AS arr, '10.0.0.1'::inet AS ip, now() AS ts, current_date AS d, 1.50::numeric AS n",
    )

    json.dumps({key: encode_row_value(value) for key, value in rows[0].items()})  # must not raise
    assert rows[0]["b"] == "\\xdead"
    assert rows[0]["arr"] == ["1.5", "2.5"]


# ================================================================================
# errors and guarantees
# ================================================================================


@requires_postgre_docker
@pytest.mark.anyio
async def test_sql_error_is_an_execution_error_and_the_driver_stays_usable(driver):
    with pytest.raises(AsyncSqlExecutionError):
        await driver.execute("SELEC 1", {})

    assert await _rows(driver, "SELECT 42 AS answer") == [{"answer": 42}]


@requires_postgre_docker
@pytest.mark.anyio
async def test_several_statements_are_refused(driver):
    with pytest.raises(AsyncSqlExecutionError):
        await driver.execute("SELECT 1; SELECT 2", {})


@requires_postgre_docker
@pytest.mark.anyio
async def test_sessions_are_read_only(driver):
    """The statement only runs on the first fetch, which is where it's refused."""
    with pytest.raises(AsyncSqlExecutionError) as exc_info:
        await _rows(driver, "CREATE TABLE pytre_should_never_exist(a int)")

    assert "read-only" in str(exc_info.value)


@requires_postgre_docker
@pytest.mark.anyio
async def test_statement_timeout_is_enforced_by_the_server(driver):
    with pytest.raises(AsyncSqlExecutionError) as exc_info:
        await _rows(driver, "SELECT pg_sleep(4)")  # fixture sets statement_timeout to 2s

    assert "statement timeout" in str(exc_info.value)


@requires_postgre_docker
@pytest.mark.anyio
async def test_saturated_pool_is_a_connection_error():
    """A result keeps its connection until it's closed."""
    driver = AsyncpgDriver(**_CONNECTION, trust_certificate=True, pool_size=1)
    driver._acquire_timeout_secs = 0.5
    await driver.connect()
    try:
        first = await driver.execute("SELECT 1 AS n", {})
        with pytest.raises(AsyncSqlConnectionError):
            await driver.execute("SELECT 1 AS n", {})
        await first.close()

        assert await _rows(driver, "SELECT 2 AS n") == [{"n": 2}]  # closing gave the connection back
    finally:
        await driver.disconnect()


# ================================================================================
# cursor
# ================================================================================

# pg_sleep makes PostgreSQL produce rows slowly: the query is still running while rows are fetched
_SLOW_QUERY = "SELECT g AS n, pg_sleep(0.001) FROM generate_series(1, 1000000) g"


async def _running_queries(driver: AsyncpgDriver) -> int:
    """Queries of Pytre_Server running on PostgreSQL, other than the one counting them."""
    [row] = await _rows(
        driver,
        "SELECT count(*) AS n FROM pg_stat_activity "
        "WHERE application_name = %(@app)s AND state = 'active' AND pid <> pg_backend_pid()",
        {"@app": APP_ID},
    )
    return row["n"]


@requires_postgre_docker
@pytest.mark.anyio
async def test_rows_are_fetched_batch_by_batch(driver):
    result = await driver.execute("SELECT g AS n FROM generate_series(1, 5) g", {})
    try:
        batches = [await result.fetch(2) for _ in range(4)]
    finally:
        await result.close()

    assert batches == [[(1,), (2,)], [(3,), (4,)], [(5,)], []]


@requires_postgre_docker
@pytest.mark.anyio
async def test_first_rows_arrive_before_the_query_is_done(driver):
    """Reading a million slow rows would take minutes - the first batch comes right away."""
    result = await driver.execute(_SLOW_QUERY, {})
    try:
        start = time.monotonic()
        assert len(await result.fetch(10)) == 10
        assert time.monotonic() - start < 5
    finally:
        await result.close()


@requires_postgre_docker
@pytest.mark.anyio
async def test_closing_before_the_last_row_stops_the_query(driver):
    result = await driver.execute(_SLOW_QUERY, {})
    await result.fetch(10)

    await result.close()

    assert await _running_queries(driver) == 0


@requires_postgre_docker
@pytest.mark.anyio
async def test_cancelled_fetch_stops_the_query_on_the_server(driver):
    """Client gone during a fetch: the fetch task is cancelled, then the result closed."""
    result = await driver.execute(_SLOW_QUERY, {})
    fetch_task = asyncio.create_task(result.fetch(100_000))
    await asyncio.sleep(0.3)
    assert await _running_queries(driver) == 1

    fetch_task.cancel()
    await asyncio.gather(fetch_task, return_exceptions=True)
    start = time.monotonic()
    await result.close()

    assert time.monotonic() - start < 5
    assert await _running_queries(driver) == 0


@requires_postgre_docker
@pytest.mark.anyio
async def test_closed_connections_go_back_to_the_pool_clean():
    """The transaction is rolled back before release - asyncpg reports a connection released with an
    active transaction to the event loop's exception handler."""
    reported: list[str] = []
    asyncio.get_running_loop().set_exception_handler(lambda _loop, context: reported.append(context["message"]))
    driver = AsyncpgDriver(**_CONNECTION, trust_certificate=True, pool_size=1)
    await driver.connect()
    try:
        result = await driver.execute(_SLOW_QUERY, {})
        await result.fetch(10)
        await result.close()
        with pytest.raises(AsyncSqlExecutionError):
            await _rows(driver, "SELECT 1 / (n - 1) AS x FROM (VALUES (1)) v(n)")  # fails in the transaction

        assert await _rows(driver, "SELECT 42 AS answer") == [{"answer": 42}]  # same, only connection
    finally:
        await driver.disconnect()
        asyncio.get_running_loop().set_exception_handler(None)

    assert reported == []
