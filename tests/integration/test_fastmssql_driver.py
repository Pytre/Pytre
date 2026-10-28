"""
Integration tests for Pytre_Server/execution/fastmssql_driver.py
Requires the local mssql-test docker container (see tests/resources/build_test_keepass.py for
its pinned connection details) - skipped automatically if it isn't reachable.
"""

import pytest

from Pytre_Core.sql_query.query_drivers_async import FastMssqlDriver
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
        trust_certificate=True,  # mssql-test docker uses a self-signed certificate
    )
    await d.connect()
    yield d
    await d.disconnect()


@requires_docker
@pytest.mark.anyio
async def test_query_with_named_params(driver):
    """execute() takes a %(name)s-style template + dict, like the sync driver - it converts to
    fastmssql's positional @P1 style itself."""
    result = await driver.execute("SELECT %(@ID)s AS id", {"@ID": 7})
    assert result.columns() == ["id"]
    assert await result.fetch(10) == [(7,)]
    await result.close()
