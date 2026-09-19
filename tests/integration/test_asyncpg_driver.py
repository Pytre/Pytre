"""
Integration tests for the asyncpg driver (Pytre_Core/sql_query/query_drivers_async.py).
Requires the local postgre-test docker container (see tests/resources/docker-compose.postgre.yml and
tests/resources/build_test_keepass.py for its pinned connection details) - started automatically,
skipped if it can't be reached.
"""

import asyncio
import json
from typing import Any

import pytest

from Pytre_Core.remotes import encode_row_value
from Pytre_Core.sql_query.query_drivers_async import AsyncpgDriver, AsyncSqlConnectionError, AsyncSqlExecutionError
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
    return [row.to_dict() for row in result]


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
    assert list(result) == []


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
    with pytest.raises(AsyncSqlExecutionError) as exc_info:
        await driver.execute("CREATE TABLE pytre_should_never_exist(a int)", {})

    assert "read-only" in str(exc_info.value)


@requires_postgre_docker
@pytest.mark.anyio
async def test_statement_timeout_is_enforced_by_the_server(driver):
    with pytest.raises(AsyncSqlExecutionError) as exc_info:
        await driver.execute("SELECT pg_sleep(4)", {})  # fixture sets statement_timeout to 2s

    assert "statement timeout" in str(exc_info.value)


@requires_postgre_docker
@pytest.mark.anyio
async def test_saturated_pool_is_a_connection_error():
    driver = AsyncpgDriver(**_CONNECTION, trust_certificate=True)
    driver._min_pool_size = 1
    driver._max_pool_size = 1
    driver._acquire_timeout_secs = 0.5
    await driver.connect()
    try:
        slow = asyncio.create_task(driver.execute("SELECT pg_sleep(2)", {}))
        await asyncio.sleep(0.2)

        with pytest.raises(AsyncSqlConnectionError):
            await driver.execute("SELECT 1", {})

        await slow
    finally:
        await driver.disconnect()
