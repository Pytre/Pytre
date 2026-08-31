"""
Tests for Pytre_Server/execution/fastmssql_driver.py
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastmssql import ApplicationIntent, SslConfig

from Pytre_Server.execution.fastmssql_driver import FastMssqlDriver, _RECOMPILE, _SESSION_INIT


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

    fake_connection.query.assert_called_once_with(_SESSION_INIT + "SELECT @P1 AS n" + _RECOMPILE, [42])


@pytest.mark.anyio
async def test_execute_strips_trailing_semicolon_before_appending_recompile():
    """A template ending with ';' (most .sql files do) must not produce a stray ';' before
    OPTION (RECOMPILE) - that would be a syntax error."""
    fake_connection = Mock()
    fake_connection.query = AsyncMock(return_value=Mock())

    with patch("Pytre_Server.execution.fastmssql_driver.Connection", return_value=fake_connection):
        driver = FastMssqlDriver(host="h", port=1433, database="db", username="u", password="p")
        await driver.execute("SELECT @P1 AS n;", [42])

    fake_connection.query.assert_called_once_with(_SESSION_INIT + "SELECT @P1 AS n" + _RECOMPILE, [42])


@pytest.mark.unit
def test_connection_defaults_to_validating_the_certificate():
    """trust_certificate not specified must default to False (secure) - see the plan doc's
    TrustServerCertificate section: this default only just flipped from always-true."""
    with patch("Pytre_Server.execution.fastmssql_driver.Connection") as MockConnection:
        FastMssqlDriver(host="h", port=1433, database="db", username="u", password="p")

    _, kwargs = MockConnection.call_args
    assert kwargs["ssl_config"] == SslConfig(trust_server_certificate=False)


@pytest.mark.unit
def test_connection_forwards_trust_certificate_true():
    with patch("Pytre_Server.execution.fastmssql_driver.Connection") as MockConnection:
        FastMssqlDriver(host="h", port=1433, database="db", username="u", password="p", trust_certificate=True)

    _, kwargs = MockConnection.call_args
    assert kwargs["ssl_config"] == SslConfig(trust_server_certificate=True)


@pytest.mark.unit
def test_connection_sets_application_name_and_read_only_intent():
    """Mirrors the local pymssql path's appname=f"{APP_NAME}_{APP_VERSION}"/read_only=True -
    Pytre is a read-only extraction tool, and a distinct appname lets a DBA tell a Pytre_Server
    connection apart from a desktop client one in sys.dm_exec_sessions."""
    with patch("Pytre_Server.execution.fastmssql_driver.Connection") as MockConnection:
        FastMssqlDriver(host="h", port=1433, database="db", username="u", password="p")

    _, kwargs = MockConnection.call_args
    assert kwargs["application_name"].startswith("Pytre_Server_")
    assert kwargs["application_intent"] == ApplicationIntent.READ_ONLY
