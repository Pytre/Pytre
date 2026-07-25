"""
Tests for Pytre_Server/execution/fastmssql_driver.py
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from Pytre_Server.execution.fastmssql_driver import FastMssqlDriver, _SESSION_INIT


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.unit
def test_session_init_forces_ymd_dateformat():
    """Pytre always sends dates as YYYY-MM-DD - fastmssql doesn't control the SQL login's
    default language the way pymssql implicitly does, so DATEFORMAT must be forced explicitly."""
    assert "SET DATEFORMAT ymd;" in _SESSION_INIT


@pytest.mark.anyio
async def test_execute_prefixes_session_init_to_every_query():
    """execute() must prefix _SESSION_INIT to every batch - not just at connect() time - since
    fastmssql pools several physical connections and a SET issued on only one of them wouldn't
    reach the others."""
    fake_connection = Mock()
    fake_connection.query = AsyncMock(return_value=Mock())

    with patch("Pytre_Server.execution.fastmssql_driver.Connection", return_value=fake_connection):
        driver = FastMssqlDriver(host="h", port=1433, database="db", username="u", password="p")
        await driver.execute("SELECT @P1 AS n", [42])

    fake_connection.query.assert_called_once_with(_SESSION_INIT + "SELECT @P1 AS n", [42])
