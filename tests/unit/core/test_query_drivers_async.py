"""
Tests for Pytre_Core/sql_query/query_drivers_async.py
"""

from unittest.mock import AsyncMock, Mock, patch

import fastmssql
import pytest

from Pytre_Core.servers import ServerType
from Pytre_Core.sql_query.query_drivers_async import (
    AsyncSqlConnectionError,
    AsyncSqlExecutionError,
    FastMssqlDriver,
    get_driver,
    _to_positional_style,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _make_server(host: str = "h", port: str = "1433", trust_server_certificate: bool = False) -> Mock:
    return Mock(
        type=ServerType.mssql.name,
        host=host,
        port=port,
        database="db",
        user="u",
        password="p",
        trust_server_certificate=trust_server_certificate,
    )


# ================================================================================
# _to_positional_style
# ================================================================================


@pytest.mark.unit
def test_single_param():
    template, params = _to_positional_style("SELECT * FROM t WHERE id = %(@ID)s", {"@ID": 42})

    assert template == "SELECT * FROM t WHERE id = @P1"
    assert params == [42]


@pytest.mark.unit
def test_multiple_distinct_params_keep_first_appearance_order():
    template, params = _to_positional_style(
        "SELECT * FROM t WHERE name = %(@Name)s AND id = %(@ID)s",
        {"@ID": 42, "@Name": "foo"},
    )

    assert template == "SELECT * FROM t WHERE name = @P1 AND id = @P2"
    assert params == ["foo", 42]


@pytest.mark.unit
def test_repeated_param_reuses_same_positional_index():
    template, params = _to_positional_style(
        "SELECT * FROM t WHERE a = %(@ID)s OR b = %(@ID)s",
        {"@ID": 7},
    )

    assert template == "SELECT * FROM t WHERE a = @P1 OR b = @P1"
    assert params == [7]


@pytest.mark.unit
def test_no_params():
    template, params = _to_positional_style("SELECT * FROM t", {})

    assert template == "SELECT * FROM t"
    assert params == []


@pytest.mark.unit
def test_ignores_unused_dict_entries():
    template, params = _to_positional_style("SELECT * FROM t WHERE id = %(@ID)s", {"@ID": 1, "@Unused": 99})

    assert template == "SELECT * FROM t WHERE id = @P1"
    assert params == [1]


# ================================================================================
# FastMssqlDriver.__init__ / connection setup
# ================================================================================


@pytest.mark.unit
def test_connection_defaults_to_validating_the_certificate():
    """trust_certificate not specified must default to False (secure) - see the plan doc's
    TrustServerCertificate section: this default only just flipped from always-true."""
    with patch("Pytre_Core.sql_query.query_drivers_async.fastmssql.Connection") as MockConnection:
        FastMssqlDriver(host="h", port=1433, database="db", username="u", password="p")

    _, kwargs = MockConnection.call_args
    assert kwargs["ssl_config"] == fastmssql.SslConfig(trust_server_certificate=False)


@pytest.mark.unit
def test_connection_forwards_trust_certificate_true():
    with patch("Pytre_Core.sql_query.query_drivers_async.fastmssql.Connection") as MockConnection:
        FastMssqlDriver(host="h", port=1433, database="db", username="u", password="p", trust_certificate=True)

    _, kwargs = MockConnection.call_args
    assert kwargs["ssl_config"] == fastmssql.SslConfig(trust_server_certificate=True)


@pytest.mark.unit
def test_connection_sets_application_name_and_read_only_intent():
    """Mirrors the local pymssql path's appname=f"{APP_NAME}_{APP_VERSION}"/read_only=True -
    Pytre is a read-only extraction tool, and a distinct appname lets a DBA tell a Pytre_Server
    connection apart from a desktop client one in sys.dm_exec_sessions."""
    with patch("Pytre_Core.sql_query.query_drivers_async.fastmssql.Connection") as MockConnection:
        FastMssqlDriver(host="h", port=1433, database="db", username="u", password="p")

    _, kwargs = MockConnection.call_args
    assert kwargs["application_name"].startswith("Pytre_Server_")
    assert kwargs["application_intent"] == fastmssql.ApplicationIntent.READ_ONLY


# ================================================================================
# FastMssqlDriver.execute
# ================================================================================


@pytest.mark.anyio
async def test_execute_converts_named_params_and_prefixes_session_init():
    """execute() takes a %(name)s-style template + dict (like the sync driver) and converts it
    to fastmssql's positional @P1 style itself. The session-init SET block is prefixed to every
    batch - not just at connect() time - since fastmssql pools several physical connections and
    a SET issued on only one of them wouldn't reach the others."""
    fake_connection = Mock()
    fake_connection.query = AsyncMock(return_value=Mock())

    with patch("Pytre_Core.sql_query.query_drivers_async.fastmssql.Connection", return_value=fake_connection):
        driver = FastMssqlDriver(host="h", port=1433, database="db", username="u", password="p")
        await driver.execute("SELECT %(@ID)s AS n", {"@ID": 42})

    sql, params = fake_connection.query.call_args[0]
    assert "SET DATEFORMAT ymd;" in sql
    assert "SELECT @P1 AS n" in sql
    assert "OPTION (RECOMPILE);" in sql
    assert params == [42]


@pytest.mark.anyio
async def test_execute_strips_trailing_semicolon_before_appending_recompile():
    """A template ending with ';' (most .sql files do) must not produce a stray ';' before
    OPTION (RECOMPILE) - that would be a syntax error."""
    fake_connection = Mock()
    fake_connection.query = AsyncMock(return_value=Mock())

    with patch("Pytre_Core.sql_query.query_drivers_async.fastmssql.Connection", return_value=fake_connection):
        driver = FastMssqlDriver(host="h", port=1433, database="db", username="u", password="p")
        await driver.execute("SELECT %(@ID)s AS n;", {"@ID": 42})

    sql, _params = fake_connection.query.call_args[0]
    assert ";\nOPTION (RECOMPILE);" not in sql
    assert sql.rstrip().endswith("OPTION (RECOMPILE);")


# ================================================================================
# FastMssqlDriver.connect / execute - fastmssql exception translation
# ================================================================================


@pytest.mark.anyio
async def test_connect_translates_sql_connection_error():
    """routes/client.py must never need to import fastmssql directly - every driver
    translates its own library's exceptions into the shared AsyncSql*Error types."""
    fake_connection = Mock()
    original = fastmssql.SqlConnectionError("connection refused")
    fake_connection.connect = AsyncMock(side_effect=original)

    with patch("Pytre_Core.sql_query.query_drivers_async.fastmssql.Connection", return_value=fake_connection):
        driver = FastMssqlDriver(host="h", port=1433, database="db", username="u", password="p")

        with pytest.raises(AsyncSqlConnectionError) as exc_info:
            await driver.connect()

    assert exc_info.value.__cause__ is original


@pytest.mark.anyio
async def test_execute_translates_sql_connection_error():
    fake_connection = Mock()
    original = fastmssql.SqlConnectionError("pool timeout")
    fake_connection.query = AsyncMock(side_effect=original)

    with patch("Pytre_Core.sql_query.query_drivers_async.fastmssql.Connection", return_value=fake_connection):
        driver = FastMssqlDriver(host="h", port=1433, database="db", username="u", password="p")

        with pytest.raises(AsyncSqlConnectionError) as exc_info:
            await driver.execute("SELECT 1", {})

    assert exc_info.value.__cause__ is original


@pytest.mark.anyio
async def test_execute_translates_sql_error():
    fake_connection = Mock()
    original = fastmssql.SqlError("Incorrect syntax")
    fake_connection.query = AsyncMock(side_effect=original)

    with patch("Pytre_Core.sql_query.query_drivers_async.fastmssql.Connection", return_value=fake_connection):
        driver = FastMssqlDriver(host="h", port=1433, database="db", username="u", password="p")

        with pytest.raises(AsyncSqlExecutionError) as exc_info:
            await driver.execute("SELECT 1", {})

    assert exc_info.value.__cause__ is original


# ================================================================================
# FastMssqlDriver._driver / _resolve_conn_host
# ================================================================================


@pytest.mark.anyio
async def test_driver_forwards_trust_server_certificate(monkeypatch):
    captured_kwargs: dict = {}

    def fake_init(self, **kwargs):
        captured_kwargs.update(kwargs)

    monkeypatch.setattr(FastMssqlDriver, "__init__", fake_init)
    server = _make_server(trust_server_certificate=True)

    await FastMssqlDriver._driver(server)

    assert captured_kwargs["trust_certificate"] is True


@pytest.mark.anyio
async def test_resolve_conn_host_skips_resolution_for_plain_host(monkeypatch):
    resolve_mock = Mock()
    monkeypatch.setattr("Pytre_Core.sql_query.query_drivers_async.resolve_instance_port", resolve_mock)

    target = await FastMssqlDriver._resolve_conn_host("SWTPX3BDD01", 1433)

    assert target == ("SWTPX3BDD01", 1433)
    assert not resolve_mock.called


@pytest.mark.anyio
async def test_resolve_conn_host_uses_resolved_port_for_named_instance(monkeypatch):
    async def fake_resolve(hostname, instance_name, timeout=5.0):
        return 52149

    monkeypatch.setattr("Pytre_Core.sql_query.query_drivers_async.resolve_instance_port", fake_resolve)

    target = await FastMssqlDriver._resolve_conn_host("SWTPX3BDD01\\DBX3V12", 1433)

    assert target == ("SWTPX3BDD01", 52149)


@pytest.mark.anyio
async def test_resolve_conn_host_falls_back_to_configured_port_if_resolution_fails(monkeypatch):
    async def fake_resolve(hostname, instance_name, timeout=5.0):
        return None

    monkeypatch.setattr("Pytre_Core.sql_query.query_drivers_async.resolve_instance_port", fake_resolve)

    target = await FastMssqlDriver._resolve_conn_host("SWTPX3BDD01\\DBX3V12", 1433)

    assert target == ("SWTPX3BDD01", 1433)


# ================================================================================
# get_driver
# ================================================================================


@pytest.mark.anyio
async def test_get_driver_dispatches_mssql_to_fastmssql_driver(monkeypatch):
    fake_driver = Mock()

    async def fake_build(server):
        return fake_driver

    monkeypatch.setattr(FastMssqlDriver, "_driver", fake_build)
    server = _make_server()

    driver = await get_driver(server)

    assert driver is fake_driver


@pytest.mark.anyio
async def test_get_driver_unknown_type_raises_error():
    server = Mock(type="unknown")

    with pytest.raises(ValueError):
        await get_driver(server)
