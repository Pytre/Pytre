"""
Integration tests for Pytre_Server/execution/fastmssql_driver.py
Requires the local mssql-test docker container (see tests/resources/build_test_keepass.py for
its pinned connection details) - skipped automatically if it isn't reachable.
"""

import pytest

from Pytre_Server.execution.fastmssql_driver import FastMssqlDriver
from Pytre_Server.execution.template_converter import to_positional_style
from tests.integration.conftest import requires_docker
from tests.resources.build_test_keepass import TEST_SQL_SERVER

pytestmark = pytest.mark.integration


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def driver():
    d = FastMssqlDriver(
        host=TEST_SQL_SERVER["host"],
        port=int(TEST_SQL_SERVER["port"]),
        database=TEST_SQL_SERVER["database"],
        username=TEST_SQL_SERVER["user"],
        password=TEST_SQL_SERVER["password"],
    )
    await d.connect()
    yield d
    await d.disconnect()


@requires_docker
@pytest.mark.anyio
async def test_query_returns_rows(driver):
    result = await driver.execute("SELECT @P1 AS n", [42])
    assert list(result)[0]["n"] == 42


@requires_docker
@pytest.mark.anyio
async def test_query_with_translated_named_params(driver):
    template, params = to_positional_style("SELECT %(@ID)s AS id", {"@ID": 7})
    result = await driver.execute(template, params)
    assert list(result)[0]["id"] == 7
