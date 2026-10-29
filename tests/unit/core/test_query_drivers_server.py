"""
Tests for Pytre_Core/sql_query/query_drivers_server.py
"""

import asyncio
import ipaddress
import json
import threading
import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, Mock, patch

import asyncpg
import fastmssql
import pymssql
import pytest

from Pytre_Core.remotes import encode_row_value
from Pytre_Core.servers import Server, ServerType
from Pytre_Core.sql_query.query_drivers_server import (
    APP_ID,
    AsyncpgDriver,
    AsyncSqlConnectionError,
    AsyncSqlExecutionError,
    FastMssqlDriver,
    PyMssqlDriver,
    _FastMssqlResult,
    get_driver,
    _json_safe,
    _to_positional_style,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _make_server(
    host: str = "h",
    port: str = "1433",
    trust_server_certificate: bool = False,
    server_type: str = ServerType.mssql.name,
) -> Mock:
    return Mock(
        type=server_type,
        host=host,
        port=port,
        database="db",
        user="u",
        password="p",
        trust_server_certificate=trust_server_certificate,
        login_timeout=60,
        timeout=300,
    )


# ================================================================================
# _to_positional_style
# ================================================================================


@pytest.mark.unit
def test_single_param():
    template, params = _to_positional_style("SELECT * FROM t WHERE id = %(@ID)s", {"@ID": 42}, "@P{}")

    assert template == "SELECT * FROM t WHERE id = @P1"
    assert params == [42]


@pytest.mark.unit
def test_multiple_distinct_params_keep_first_appearance_order():
    template, params = _to_positional_style(
        "SELECT * FROM t WHERE name = %(@Name)s AND id = %(@ID)s",
        {"@ID": 42, "@Name": "foo"},
        "@P{}",
    )

    assert template == "SELECT * FROM t WHERE name = @P1 AND id = @P2"
    assert params == ["foo", 42]


@pytest.mark.unit
def test_repeated_param_reuses_same_positional_index():
    template, params = _to_positional_style(
        "SELECT * FROM t WHERE a = %(@ID)s OR b = %(@ID)s",
        {"@ID": 7},
        "@P{}",
    )

    assert template == "SELECT * FROM t WHERE a = @P1 OR b = @P1"
    assert params == [7]


@pytest.mark.unit
def test_no_params():
    template, params = _to_positional_style("SELECT * FROM t", {}, "@P{}")

    assert template == "SELECT * FROM t"
    assert params == []


@pytest.mark.unit
def test_placeholder_format_is_configurable():
    """SQL Server takes @P1, PostgreSQL (asyncpg) takes $1 - same numbering, different placeholder."""
    template, params = _to_positional_style(
        "SELECT * FROM t WHERE a = %(@ID)s OR b = %(@Name)s OR c = %(@ID)s",
        {"@ID": 7, "@Name": "foo"},
        "${}",
    )

    assert template == "SELECT * FROM t WHERE a = $1 OR b = $2 OR c = $1"
    assert params == [7, "foo"]


@pytest.mark.unit
def test_ignores_unused_dict_entries():
    template, params = _to_positional_style("SELECT * FROM t WHERE id = %(@ID)s", {"@ID": 1, "@Unused": 99}, "@P{}")

    assert template == "SELECT * FROM t WHERE id = @P1"
    assert params == [1]


# ================================================================================
# FastMssqlDriver.__init__ / connection setup
# ================================================================================


@pytest.mark.unit
def test_connection_defaults_to_validating_the_certificate():
    """trust_certificate not specified must default to False (secure) - see the plan doc's
    TrustServerCertificate section: this default only just flipped from always-true."""
    with patch("Pytre_Core.sql_query.query_drivers_server.fastmssql.Connection") as MockConnection:
        FastMssqlDriver(host="h", port=1433, database="db", username="u", password="p")

    _, kwargs = MockConnection.call_args
    assert kwargs["ssl_config"] == fastmssql.SslConfig(trust_server_certificate=False)


@pytest.mark.unit
def test_connection_forwards_trust_certificate_true():
    with patch("Pytre_Core.sql_query.query_drivers_server.fastmssql.Connection") as MockConnection:
        FastMssqlDriver(host="h", port=1433, database="db", username="u", password="p", trust_certificate=True)

    _, kwargs = MockConnection.call_args
    assert kwargs["ssl_config"] == fastmssql.SslConfig(trust_server_certificate=True)


@pytest.mark.unit
def test_connection_sets_application_name_and_read_only_intent():
    """Mirrors the local pymssql path's appname=APP_ID/read_only=True -
    Pytre is a read-only extraction tool, and a distinct appname lets a DBA tell a Pytre_Server
    connection apart from a desktop client one in sys.dm_exec_sessions."""
    with patch("Pytre_Core.sql_query.query_drivers_server.fastmssql.Connection") as MockConnection:
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

    with patch("Pytre_Core.sql_query.query_drivers_server.fastmssql.Connection", return_value=fake_connection):
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

    with patch("Pytre_Core.sql_query.query_drivers_server.fastmssql.Connection", return_value=fake_connection):
        driver = FastMssqlDriver(host="h", port=1433, database="db", username="u", password="p")
        await driver.execute("SELECT %(@ID)s AS n;", {"@ID": 42})

    sql, _params = fake_connection.query.call_args[0]
    assert ";\nOPTION (RECOMPILE);" not in sql
    assert sql.rstrip().endswith("OPTION (RECOMPILE);")


@pytest.mark.anyio
async def test_execute_wraps_the_query_stream_in_a_result():
    stream = _FakeQueryStream(["n"], [(1,)])
    fake_connection = Mock()
    fake_connection.query = AsyncMock(return_value=stream)

    with patch("Pytre_Core.sql_query.query_drivers_server.fastmssql.Connection", return_value=fake_connection):
        driver = FastMssqlDriver(host="h", port=1433, database="db", username="u", password="p")
        result = await driver.execute("SELECT 1 AS n", {})

    assert isinstance(result, _FastMssqlResult)
    assert result.columns() == ["n"]
    assert await result.fetch(10) == [(1,)]


# ================================================================================
# _FastMssqlResult - fakes standing in for fastmssql's QueryStream / FastRow
# ================================================================================


class _FakeFastRow:
    def __init__(self, values: tuple) -> None:
        self._values = values

    def values(self) -> list:
        return list(self._values)


class _FakeQueryStream:
    """fetch(n) pops the next n rows, like QueryStream; columns() raises ValueError without rows."""

    def __init__(self, columns: list[str] | None, rows: list[tuple]) -> None:
        self._columns = columns
        self._rows = [_FakeFastRow(row) for row in rows]
        self.fetch_sizes: list[int] = []

    def columns(self) -> list[str]:
        if self._columns is None:
            raise ValueError("No column information available")
        return self._columns

    def fetch(self, n: int) -> list[_FakeFastRow]:
        self.fetch_sizes.append(n)
        batch, self._rows = self._rows[:n], self._rows[n:]
        return batch


@pytest.mark.unit
def test_fastmssql_result_columns():
    assert _FastMssqlResult(_FakeQueryStream(["a", "b"], [])).columns() == ["a", "b"]  # type: ignore


@pytest.mark.unit
def test_fastmssql_result_columns_falls_back_to_empty_when_no_row_is_returned():
    """fastmssql's columns() raises ValueError when the result has 0 rows (confirmed against the real driver)."""
    assert _FastMssqlResult(_FakeQueryStream(None, [])).columns() == []  # type: ignore


@pytest.mark.anyio
async def test_fastmssql_result_fetches_tuples_batch_by_batch():
    stream = _FakeQueryStream(["id", "label"], [(i, f"r{i}") for i in range(5)])
    result = _FastMssqlResult(stream)  # type: ignore

    batches = [await result.fetch(2) for _ in range(4)]

    assert batches == [[(0, "r0"), (1, "r1")], [(2, "r2"), (3, "r3")], [(4, "r4")], []]
    assert stream.fetch_sizes == [2, 2, 2, 2]


@pytest.mark.anyio
async def test_fastmssql_result_makes_values_json_safe():
    """fastmssql returns time and varbinary columns as datetime.time and bytes, which json.dumps refuses."""
    result = _FastMssqlResult(_FakeQueryStream(["tm", "raw", "n"], [(time(13, 5, 9), b"\xde\xad", 1)]))  # type: ignore

    assert await result.fetch(10) == [("13:05:09", "\\xdead", 1)]


@pytest.mark.anyio
async def test_fastmssql_result_close_can_be_called_several_times():
    result = _FastMssqlResult(_FakeQueryStream(["id"], [(1,)]))  # type: ignore

    await result.close()
    await result.close()


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

    with patch("Pytre_Core.sql_query.query_drivers_server.fastmssql.Connection", return_value=fake_connection):
        driver = FastMssqlDriver(host="h", port=1433, database="db", username="u", password="p")

        with pytest.raises(AsyncSqlConnectionError) as exc_info:
            await driver.connect()

    assert exc_info.value.__cause__ is original


@pytest.mark.anyio
async def test_execute_translates_sql_connection_error():
    fake_connection = Mock()
    original = fastmssql.SqlConnectionError("pool timeout")
    fake_connection.query = AsyncMock(side_effect=original)

    with patch("Pytre_Core.sql_query.query_drivers_server.fastmssql.Connection", return_value=fake_connection):
        driver = FastMssqlDriver(host="h", port=1433, database="db", username="u", password="p")

        with pytest.raises(AsyncSqlConnectionError) as exc_info:
            await driver.execute("SELECT 1", {})

    assert exc_info.value.__cause__ is original


@pytest.mark.anyio
async def test_execute_translates_sql_error():
    fake_connection = Mock()
    original = fastmssql.SqlError("Incorrect syntax")
    fake_connection.query = AsyncMock(side_effect=original)

    with patch("Pytre_Core.sql_query.query_drivers_server.fastmssql.Connection", return_value=fake_connection):
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

    await FastMssqlDriver._driver(server, pool_size=30)

    assert captured_kwargs["trust_certificate"] is True


@pytest.mark.anyio
@pytest.mark.parametrize("pool_size, min_idle", [(30, 10), (50, 10), (4, 4), (1, 1)])
async def test_driver_sizes_the_pool_and_keeps_performance_preset_values(monkeypatch, pool_size, min_idle):
    captured_kwargs: dict = {}

    def fake_init(self, **kwargs):
        captured_kwargs.update(kwargs)

    monkeypatch.setattr(FastMssqlDriver, "__init__", fake_init)

    await FastMssqlDriver._driver(_make_server(), pool_size=pool_size)

    pool_config = captured_kwargs["pool_config"]
    preset = fastmssql.PoolConfig.performance()
    assert pool_config.max_size == pool_size
    assert pool_config.min_idle == min_idle  # can't keep more idle connections than the pool holds
    assert pool_config.connection_timeout_secs == 10  # waiting for a free connection fails after 10 s
    assert pool_config.max_lifetime_secs == preset.max_lifetime_secs
    assert pool_config.idle_timeout_secs == preset.idle_timeout_secs


@pytest.mark.anyio
async def test_resolve_conn_host_skips_resolution_for_plain_host(monkeypatch):
    resolve_mock = Mock()
    monkeypatch.setattr("Pytre_Core.sql_query.query_drivers_server.resolve_instance_port", resolve_mock)

    target = await FastMssqlDriver._resolve_conn_host("SWTPX3BDD01", 1433)

    assert target == ("SWTPX3BDD01", 1433)
    assert not resolve_mock.called


@pytest.mark.anyio
async def test_resolve_conn_host_uses_resolved_port_for_named_instance(monkeypatch):
    async def fake_resolve(hostname, instance_name, timeout=5.0):
        return 52149

    monkeypatch.setattr("Pytre_Core.sql_query.query_drivers_server.resolve_instance_port", fake_resolve)

    target = await FastMssqlDriver._resolve_conn_host("SWTPX3BDD01\\DBX3V12", 1433)

    assert target == ("SWTPX3BDD01", 52149)


@pytest.mark.anyio
async def test_resolve_conn_host_falls_back_to_configured_port_if_resolution_fails(monkeypatch):
    async def fake_resolve(hostname, instance_name, timeout=5.0):
        return None

    monkeypatch.setattr("Pytre_Core.sql_query.query_drivers_server.resolve_instance_port", fake_resolve)

    target = await FastMssqlDriver._resolve_conn_host("SWTPX3BDD01\\DBX3V12", 1433)

    assert target == ("SWTPX3BDD01", 1433)


# ================================================================================
# get_driver
# ================================================================================


@pytest.mark.anyio
async def test_get_driver_dispatches_mssql_to_pymssql_driver():
    driver = await get_driver(_make_pymssql_server(), 12)

    try:
        assert isinstance(driver, PyMssqlDriver)
        assert _free_slots(driver) == 12
        assert driver._executor._max_workers == 12  # one thread per running query
    finally:
        await driver.disconnect()


@pytest.mark.anyio
async def test_get_driver_dispatches_fastmssql_to_fastmssql_driver(monkeypatch):
    fake_driver = Mock()
    build_calls: list[tuple] = []

    async def fake_build(server, pool_size):
        build_calls.append((server, pool_size))
        return fake_driver

    monkeypatch.setattr(FastMssqlDriver, "_driver", fake_build)
    server = _make_server(server_type=ServerType.fastmssql.name)

    driver = await get_driver(server, 12)

    assert driver is fake_driver
    assert build_calls == [(server, 12)]


@pytest.mark.anyio
async def test_get_driver_dispatches_postgre_to_asyncpg_driver(monkeypatch):
    fake_driver = Mock()
    build = Mock(return_value=fake_driver)
    monkeypatch.setattr(AsyncpgDriver, "_driver", build)
    server = _make_server(server_type=ServerType.postgre.name)

    driver = await get_driver(server, 12)

    assert driver is fake_driver
    build.assert_called_once_with(server, 12)


@pytest.mark.anyio
async def test_get_driver_unknown_type_raises_error():
    server = Mock(type="unknown")

    with pytest.raises(ValueError):
        await get_driver(server, 30)


# ================================================================================
# PyMssqlDriver - fakes standing in for a pymssql connection, each call recorded with the thread
# running it (a pymssql connection must only ever be used by one thread at a time)
# ================================================================================


class _FakeDbLibConnection:
    """Stands in for pymssql's internal _mssql connection (Connection._conn) - only cancel() is used."""

    def __init__(self, owner: _FakePymssqlConnection) -> None:
        self._owner = owner

    def cancel(self) -> None:
        self._owner.record("cancel")
        if self._owner.cancel_error is not None:
            raise self._owner.cancel_error


class _FakePymssqlCursor:
    def __init__(self, owner: _FakePymssqlConnection, columns, rows, execute_error, fetch_error) -> None:
        self._owner = owner
        self._columns = columns
        self._rows = list(rows)
        self._execute_error = execute_error
        self._fetch_error = fetch_error
        self.description = None
        self.executed: tuple = ()
        self.fetch_sizes: list[int] = []

    def execute(self, template: str, params: dict) -> None:
        self._owner.record("execute")
        self.executed = (template, params)
        if self._execute_error is not None:
            raise self._execute_error
        if self._columns is not None:  # None = statement without any result set
            self.description = tuple((name, 1, None, None, None, None, None) for name in self._columns)

    def fetchmany(self, n: int) -> list[tuple]:
        self._owner.record("fetchmany")
        self.fetch_sizes.append(n)
        if self._fetch_error is not None:
            raise self._fetch_error
        batch, self._rows = self._rows[:n], self._rows[n:]
        return batch


class _FakePymssqlConnection:
    """`blocked_call` makes that call wait for `gate` (after setting `entered`), like a query still
    running on SQL Server."""

    def __init__(
        self, columns=("n",), rows=(), execute_error=None, fetch_error=None, cancel_error=None, blocked_call=None
    ) -> None:
        self.cancel_error = cancel_error
        self.blocked_call = blocked_call
        self.entered = threading.Event()
        self.gate = threading.Event()
        self.calls: list[tuple[str, str]] = []  # (call, name of the thread running it)
        self.cursor_obj = _FakePymssqlCursor(self, columns, rows, execute_error, fetch_error)
        self._conn = _FakeDbLibConnection(self)

    def record(self, call: str) -> None:
        self.calls.append((call, threading.current_thread().name))
        if call == self.blocked_call:
            self.entered.set()
            self.gate.wait(5)

    def call_names(self) -> list[str]:
        return [call for call, _thread in self.calls]

    def cursor(self) -> _FakePymssqlCursor:
        self.record("cursor")
        return self.cursor_obj

    def close(self) -> None:
        self.record("close")


def _make_pymssql_server() -> Server:
    server = Server(Mock())
    server.id = "SRV1"
    server.host = "h"
    return server


def _free_slots(driver: PyMssqlDriver) -> int:
    return driver._slots._value


def _patch_connect(monkeypatch, connection: _FakePymssqlConnection | None = None, error=None) -> list[tuple]:
    """Patches connect_mssql to return `connection` (or raise `error`), returns its (server, app_id, thread) calls."""
    connect_calls: list[tuple] = []

    def fake_connect(server, app_id):
        connect_calls.append((server, app_id, threading.current_thread().name))
        if error is not None:
            raise error
        return connection

    monkeypatch.setattr("Pytre_Core.sql_query.query_drivers_server.connect_mssql", fake_connect)
    return connect_calls


@pytest.fixture
def make_pymssql_driver():
    drivers: list[PyMssqlDriver] = []

    def make(pool_size: int = 30) -> PyMssqlDriver:
        driver = PyMssqlDriver(_make_pymssql_server(), pool_size)
        drivers.append(driver)
        return driver

    yield make
    for driver in drivers:
        driver._executor.shutdown(wait=True)


# --- execute ------------------------------------------------------------------


@pytest.mark.anyio
async def test_pymssql_execute_runs_the_template_in_a_driver_thread(monkeypatch, make_pymssql_driver):
    """pymssql takes the %(name)s template and its dict as is - no positional conversion needed."""
    connection = _FakePymssqlConnection(columns=("id", "label"))
    connect_calls = _patch_connect(monkeypatch, connection)
    driver = make_pymssql_driver()

    result = await driver.execute("SELECT %(@ID)s AS id, 'a%%' AS label", {"@ID": 7})

    assert result.columns() == ["id", "label"]
    assert connection.cursor_obj.executed == ("SELECT %(@ID)s AS id, 'a%%' AS label", {"@ID": 7})
    [(_server, app_id, connect_thread)] = connect_calls
    assert app_id == APP_ID  # Pytre_Server's own name in sys.dm_exec_sessions
    assert connect_thread.startswith("pymssql_SRV1")
    assert all(thread.startswith("pymssql_SRV1") for _call, thread in connection.calls)
    await result.close()


@pytest.mark.anyio
async def test_pymssql_driver_keeps_the_server_parameters_it_was_built_with(monkeypatch):
    """Editing a server only applies once its connections are reset (a new driver is built)."""
    connect_calls = _patch_connect(monkeypatch, _FakePymssqlConnection())
    server = _make_pymssql_server()
    driver = PyMssqlDriver(server, 1)
    server.host = "edited-host"

    try:
        result = await driver.execute("SELECT 1 AS n", {})
        await result.close()
    finally:
        await driver.disconnect()

    assert connect_calls[0][0].host == "h"


@pytest.mark.anyio
async def test_pymssql_connection_error_is_translated_and_frees_the_slot(monkeypatch, make_pymssql_driver):
    original = pymssql.OperationalError((18456, b"Login failed for user 'u'."))  # connect errors carry 1 tuple
    _patch_connect(monkeypatch, error=original)
    driver = make_pymssql_driver(pool_size=1)

    with pytest.raises(AsyncSqlConnectionError) as exc_info:
        await driver.execute("SELECT 1", {})

    assert str(exc_info.value) == "Login failed for user 'u'."
    assert exc_info.value.__cause__ is original
    assert _free_slots(driver) == 1


@pytest.mark.anyio
async def test_pymssql_sql_error_is_translated_and_closes_the_connection(monkeypatch, make_pymssql_driver):
    original = pymssql.ProgrammingError(102, b"Incorrect syntax near 'SELEC'.")  # cursor errors carry 2 args
    connection = _FakePymssqlConnection(execute_error=original)
    _patch_connect(monkeypatch, connection)
    driver = make_pymssql_driver(pool_size=1)

    with pytest.raises(AsyncSqlExecutionError) as exc_info:
        await driver.execute("SELEC 1", {})

    assert str(exc_info.value) == "Incorrect syntax near 'SELEC'."
    assert exc_info.value.__cause__ is original
    assert connection.call_names()[-1] == "close"
    assert _free_slots(driver) == 1


@pytest.mark.anyio
async def test_pymssql_saturated_pool_is_a_connection_error(monkeypatch, make_pymssql_driver):
    _patch_connect(monkeypatch, _FakePymssqlConnection())
    driver = make_pymssql_driver(pool_size=1)
    driver._acquire_timeout_secs = 0.05
    first = await driver.execute("SELECT 1 AS n", {})

    with pytest.raises(AsyncSqlConnectionError, match="No slot available"):
        await driver.execute("SELECT 1 AS n", {})

    await first.close()
    second = await driver.execute("SELECT 1 AS n", {})  # closing gave the slot back
    await second.close()


@pytest.mark.unit
def test_pymssql_waits_10_seconds_for_a_slot():
    assert PyMssqlDriver._acquire_timeout_secs == 10


@pytest.mark.anyio
async def test_pymssql_cancelled_execute_closes_once_the_thread_is_done(monkeypatch, make_pymssql_driver):
    """A query can't be interrupted from another thread: the connection is only closed once
    cursor.execute() returns, from a driver thread."""
    connection = _FakePymssqlConnection(rows=[(1,)], blocked_call="execute")
    _patch_connect(monkeypatch, connection)
    driver = make_pymssql_driver(pool_size=1)

    execute_task = asyncio.create_task(driver.execute("SELECT n", {}))
    await asyncio.to_thread(connection.entered.wait, 5)
    execute_task.cancel()
    await asyncio.sleep(0.05)

    assert connection.call_names() == ["cursor", "execute"]  # the connection is left alone meanwhile
    assert _free_slots(driver) == 0

    connection.gate.set()
    with pytest.raises(asyncio.CancelledError):
        await execute_task

    assert connection.call_names() == ["cursor", "execute", "cancel", "close"]
    assert all(thread.startswith("pymssql_SRV1") for _call, thread in connection.calls)
    assert _free_slots(driver) == 1


@pytest.mark.anyio
async def test_pymssql_disconnect_stops_the_threads(make_pymssql_driver):
    driver = make_pymssql_driver()

    await driver.disconnect()

    with pytest.raises(RuntimeError):
        driver._executor.submit(print)


@pytest.mark.anyio
async def test_pymssql_connect_has_nothing_to_open(monkeypatch, make_pymssql_driver):
    connect_calls = _patch_connect(monkeypatch, _FakePymssqlConnection())

    await make_pymssql_driver().connect()

    assert connect_calls == []  # each query opens its own connection


# --- result -------------------------------------------------------------------


@pytest.mark.anyio
async def test_pymssql_result_fetches_tuples_batch_by_batch(monkeypatch, make_pymssql_driver):
    connection = _FakePymssqlConnection(rows=[(1,), (2,), (3,)])
    _patch_connect(monkeypatch, connection)
    result = await make_pymssql_driver().execute("SELECT n", {})

    batches = [await result.fetch(2) for _ in range(3)]

    assert batches == [[(1,), (2,)], [(3,)], []]
    assert connection.cursor_obj.fetch_sizes == [2, 2]  # a short batch means every row was read
    await result.close()
    assert "cancel" not in connection.call_names()  # nothing left to cancel
    assert connection.call_names()[-1] == "close"


@pytest.mark.anyio
async def test_pymssql_result_makes_values_json_safe(monkeypatch, make_pymssql_driver):
    _patch_connect(
        monkeypatch, _FakePymssqlConnection(columns=("u", "raw", "n"), rows=[(uuid.UUID(int=1), b"\xde\xad", 1)])
    )
    result = await make_pymssql_driver().execute("SELECT u, raw, n", {})

    assert await result.fetch(10) == [("00000000-0000-0000-0000-000000000001", "\\xdead", 1)]
    await result.close()


@pytest.mark.anyio
async def test_pymssql_result_without_result_set_has_no_column_and_no_row(monkeypatch, make_pymssql_driver):
    """pymssql's fetchmany() raises when the statement returned no result set - it must not be called."""
    connection = _FakePymssqlConnection(columns=None)
    _patch_connect(monkeypatch, connection)
    result = await make_pymssql_driver().execute("SET NOCOUNT ON", {})

    assert result.columns() == []
    assert await result.fetch(10) == []
    await result.close()
    assert connection.call_names() == ["cursor", "execute", "close"]


@pytest.mark.anyio
async def test_pymssql_result_columns_returns_a_copy(monkeypatch, make_pymssql_driver):
    _patch_connect(monkeypatch, _FakePymssqlConnection(columns=("a",)))
    result = await make_pymssql_driver().execute("SELECT a", {})

    result.columns().append("b")

    assert result.columns() == ["a"]
    await result.close()


@pytest.mark.anyio
async def test_pymssql_fetch_error_is_an_execution_error(monkeypatch, make_pymssql_driver):
    original = pymssql.OperationalError(1205, b"Transaction was deadlocked.")  # cursor errors carry 2 args
    _patch_connect(monkeypatch, _FakePymssqlConnection(rows=[(1,)], fetch_error=original))
    result = await make_pymssql_driver().execute("SELECT n", {})

    with pytest.raises(AsyncSqlExecutionError) as exc_info:
        await result.fetch(10)

    assert str(exc_info.value) == "Transaction was deadlocked."
    assert exc_info.value.__cause__ is original
    await result.close()


@pytest.mark.anyio
async def test_pymssql_close_cancels_unread_rows_and_frees_the_slot(monkeypatch, make_pymssql_driver):
    connection = _FakePymssqlConnection(rows=[(1,), (2,), (3,)])
    _patch_connect(monkeypatch, connection)
    driver = make_pymssql_driver(pool_size=1)
    result = await driver.execute("SELECT n", {})
    await result.fetch(1)

    await result.close()

    assert connection.call_names()[-2:] == ["cancel", "close"]  # stops the query on SQL Server first
    assert all(thread.startswith("pymssql_SRV1") for _call, thread in connection.calls)
    assert _free_slots(driver) == 1


@pytest.mark.anyio
async def test_pymssql_close_can_be_called_several_times(monkeypatch, make_pymssql_driver):
    connection = _FakePymssqlConnection(rows=[(1,)])
    _patch_connect(monkeypatch, connection)
    driver = make_pymssql_driver(pool_size=1)
    result = await driver.execute("SELECT n", {})

    await result.close()
    await result.close()

    assert connection.call_names().count("close") == 1
    assert _free_slots(driver) == 1  # released once only


@pytest.mark.anyio
async def test_pymssql_fetch_after_close_returns_no_row(monkeypatch, make_pymssql_driver):
    connection = _FakePymssqlConnection(rows=[(1,)])
    _patch_connect(monkeypatch, connection)
    result = await make_pymssql_driver().execute("SELECT n", {})

    await result.close()

    assert await result.fetch(10) == []
    assert "fetchmany" not in connection.call_names()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "error",
    [
        pymssql._mssql.MSSQLDatabaseException((20047, b"DBPROCESS is dead or not enabled")),
        pymssql.InterfaceError("Connection is closed."),
    ],
)
async def test_pymssql_close_still_closes_when_cancel_fails(monkeypatch, make_pymssql_driver, error):
    """cancel() raises pymssql's internal _mssql errors, which aren't pymssql.Error subclasses."""
    connection = _FakePymssqlConnection(rows=[(1,), (2,)], cancel_error=error)
    _patch_connect(monkeypatch, connection)
    driver = make_pymssql_driver(pool_size=1)
    result = await driver.execute("SELECT n", {})

    await result.close()  # must not raise

    assert connection.call_names()[-2:] == ["cancel", "close"]
    assert _free_slots(driver) == 1


@pytest.mark.anyio
async def test_pymssql_close_waits_for_the_running_fetch(monkeypatch, make_pymssql_driver):
    """Client gone during a fetch: the connection is only cancelled and closed once fetchmany()
    returns, from a driver thread."""
    connection = _FakePymssqlConnection(rows=[(1,), (2,), (3,)], blocked_call="fetchmany")
    _patch_connect(monkeypatch, connection)
    driver = make_pymssql_driver(pool_size=1)
    result = await driver.execute("SELECT n", {})

    fetch_task = asyncio.create_task(result.fetch(1))
    await asyncio.to_thread(connection.entered.wait, 5)
    fetch_task.cancel()
    close_task = asyncio.create_task(result.close())
    await asyncio.sleep(0.05)

    assert not close_task.done()
    assert connection.call_names() == ["cursor", "execute", "fetchmany"]
    assert _free_slots(driver) == 0

    connection.gate.set()
    await close_task

    assert connection.call_names() == ["cursor", "execute", "fetchmany", "cancel", "close"]
    assert all(thread.startswith("pymssql_SRV1") for _call, thread in connection.calls)
    assert _free_slots(driver) == 1
    with pytest.raises(asyncio.CancelledError):
        await fetch_task


@pytest.mark.anyio
async def test_pymssql_cancelled_close_keeps_closing(monkeypatch, make_pymssql_driver):
    """A caller cancelled while waiting in close() (e.g. the request task) can't leave the
    connection open or the slot taken."""
    connection = _FakePymssqlConnection(rows=[(1,), (2,)], blocked_call="fetchmany")
    _patch_connect(monkeypatch, connection)
    driver = make_pymssql_driver(pool_size=1)
    result = await driver.execute("SELECT n", {})

    fetch_task = asyncio.create_task(result.fetch(1))
    await asyncio.to_thread(connection.entered.wait, 5)
    close_task = asyncio.create_task(result.close())
    await asyncio.sleep(0.05)
    close_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await close_task

    connection.gate.set()
    await fetch_task
    await result.close()  # waits for the closing already under way

    assert connection.call_names()[-2:] == ["cancel", "close"]
    assert connection.call_names().count("close") == 1
    assert _free_slots(driver) == 1


# ================================================================================
# AsyncpgDriver - fakes standing in for an asyncpg pool (behavior established against a real
# PostgreSQL server in tests/integration/test_asyncpg_driver.py)
# ================================================================================


class _Named:
    """Stands in for asyncpg's Attribute (column) and Type (parameter) objects - only .name is read."""

    def __init__(self, name: str) -> None:
        self.name = name


class _FakeRecord(dict):
    """asyncpg.Record exposes .values() like a dict."""


class _FakeCursor:
    def __init__(self, connection: _FakeConnection, records) -> None:
        self._connection = connection
        self._records = list(records)
        self.fetch_sizes: list[int] = []

    async def fetch(self, n: int) -> list:
        self.fetch_sizes.append(n)
        await self._connection.step("fetch")
        batch, self._records = self._records[:n], self._records[n:]
        return batch


class _FakeStatement:
    def __init__(self, connection: _FakeConnection, columns, param_types, records) -> None:
        self._connection = connection
        self._columns = columns
        self._param_types = param_types
        self._records = records
        self.cursor_args: tuple = ()
        self.cursor_obj: _FakeCursor | None = None

    def get_attributes(self):
        return [_Named(name) for name in self._columns]

    def get_parameters(self):
        return [_Named(name) for name in self._param_types]

    async def cursor(self, *args) -> _FakeCursor:
        self.cursor_args = args
        await self._connection.step("cursor")
        self.cursor_obj = _FakeCursor(self._connection, self._records)
        return self.cursor_obj


class _FakeTransaction:
    def __init__(self, connection: _FakeConnection) -> None:
        self._connection = connection

    async def start(self) -> None:
        await self._connection.step("begin")

    async def rollback(self) -> None:
        await self._connection.step("rollback")


class _FakeConnection:
    """Records each step (begin, prepare, cursor, fetch, rollback, terminate, release). `errors` makes a step
    raise, `blocked` makes it wait for `gate` (after setting `entered`) like a query still running on
    PostgreSQL - a step cancelled meanwhile is recorded as "<step> cancelled"."""

    def __init__(self, columns=(), param_types=(), records=(), errors=None, blocked=None) -> None:
        self.statement = _FakeStatement(self, columns, param_types, records)
        self.errors: dict[str, BaseException] = errors or {}
        self.blocked = blocked
        self.entered = asyncio.Event()
        self.gate = asyncio.Event()
        self.steps: list[str] = []
        self.prepared_sql = ""
        self.readonly: bool | None = None

    async def step(self, name: str) -> None:
        self.steps.append(name)
        if name == self.blocked:
            self.entered.set()
            try:
                await self.gate.wait()
            except asyncio.CancelledError:
                self.steps.append(f"{name} cancelled")
                raise
        if name in self.errors:
            raise self.errors[name]

    def transaction(self, readonly: bool = False) -> _FakeTransaction:
        self.readonly = readonly
        return _FakeTransaction(self)

    async def prepare(self, sql: str) -> _FakeStatement:
        self.prepared_sql = sql
        await self.step("prepare")
        return self.statement

    def terminate(self) -> None:
        self.steps.append("terminate")


class _FakePool:
    def __init__(self, connection: _FakeConnection, acquire_error=None) -> None:
        self.connection = connection
        self._acquire_error = acquire_error
        self.acquire_timeout = None
        self.released: list[_FakeConnection] = []
        self.closed = False

    async def acquire(self, timeout=None) -> _FakeConnection:
        self.acquire_timeout = timeout
        if self._acquire_error is not None:
            raise self._acquire_error
        return self.connection

    async def release(self, connection: _FakeConnection) -> None:
        connection.steps.append("release")
        self.released.append(connection)

    async def close(self) -> None:
        self.closed = True


def _make_pg_driver(connection: _FakeConnection | None = None, acquire_error=None, **kwargs) -> AsyncpgDriver:
    driver = AsyncpgDriver(host="h", port=5432, database="db", username="u", password="p", **kwargs)
    driver._pool = _FakePool(connection or _FakeConnection(), acquire_error)  # type: ignore
    return driver


async def _pg_result(connection: _FakeConnection):
    return await _make_pg_driver(connection).execute("SELECT n FROM t", {})


_PG_CONNECTIVITY_ERRORS = [
    OSError("connection reset"),
    TimeoutError(),
    asyncpg.InterfaceError("connection is closed"),
    asyncpg.ConnectionFailureError("server closed the connection"),
    asyncpg.CannotConnectNowError("starting up"),
    asyncpg.TooManyConnectionsError("too many"),
]


# --- construction ---------------------------------------------------------------


@pytest.mark.unit
def test_pg_driver_verifies_certificate_by_default():
    assert AsyncpgDriver("h", 5432, "db", "u", "p")._ssl is True


@pytest.mark.unit
def test_pg_driver_trust_certificate_accepts_what_the_server_offers():
    assert AsyncpgDriver("h", 5432, "db", "u", "p", trust_certificate=True)._ssl == "prefer"


@pytest.mark.unit
def test_pg_driver_builds_from_server_parameters():
    server = _make_server(
        host="pg-host", port="5433", trust_server_certificate=True, server_type=ServerType.postgre.name
    )
    server.timeout = 30
    server.login_timeout = 5

    driver = AsyncpgDriver._driver(server, pool_size=12)

    assert driver._host == "pg-host"
    assert driver._port == 5433
    assert driver._ssl == "prefer"
    assert driver._login_timeout == 5
    assert driver._statement_timeout_ms == 30_000
    assert driver._pool_size == 12


# --- connect / disconnect ---------------------------------------------------------


@pytest.mark.anyio
async def test_pg_connect_creates_a_read_only_pool_with_timeouts():
    create_pool = AsyncMock(return_value=Mock())
    driver = AsyncpgDriver("h", 5432, "db", "u", "p", login_timeout=7, statement_timeout_secs=12)

    with patch("Pytre_Core.sql_query.query_drivers_server.asyncpg.create_pool", create_pool):
        await driver.connect()

    kwargs = create_pool.call_args.kwargs
    assert kwargs["ssl"] is True
    assert kwargs["timeout"] == 7
    assert kwargs["server_settings"]["default_transaction_read_only"] == "on"
    assert kwargs["server_settings"]["statement_timeout"] == "12000"
    assert kwargs["server_settings"]["application_name"].startswith("Pytre_Server_")
    assert (kwargs["min_size"], kwargs["max_size"]) == (2, 30)


@pytest.mark.anyio
@pytest.mark.parametrize("pool_size, min_size", [(12, 2), (1, 1)])
async def test_pg_connect_sizes_the_pool(pool_size, min_size):
    create_pool = AsyncMock(return_value=Mock())
    driver = AsyncpgDriver("h", 5432, "db", "u", "p", pool_size=pool_size)

    with patch("Pytre_Core.sql_query.query_drivers_server.asyncpg.create_pool", create_pool):
        await driver.connect()

    kwargs = create_pool.call_args.kwargs
    assert kwargs["max_size"] == pool_size
    assert kwargs["min_size"] == min_size  # asyncpg refuses a min_size above max_size


@pytest.mark.anyio
@pytest.mark.parametrize(
    "error",
    [
        OSError("refused"),
        TimeoutError(),
        asyncpg.InvalidPasswordError("bad password"),
        asyncpg.InvalidCatalogNameError("no such database"),
        asyncpg.InterfaceError("closed"),
    ],
)
async def test_pg_connect_translates_every_failure_to_a_connection_error(error):
    driver = AsyncpgDriver("h", 5432, "db", "u", "p")

    with patch("Pytre_Core.sql_query.query_drivers_server.asyncpg.create_pool", AsyncMock(side_effect=error)):
        with pytest.raises(AsyncSqlConnectionError) as exc_info:
            await driver.connect()

    assert exc_info.value.__cause__ is error
    assert str(exc_info.value)  # an empty TimeoutError message falls back to the class name


@pytest.mark.anyio
async def test_pg_disconnect_closes_the_pool_and_is_idempotent():
    driver = _make_pg_driver()
    pool = driver._pool

    await driver.disconnect()
    await driver.disconnect()

    assert pool.closed is True  # type: ignore
    assert driver._pool is None


# --- execute ------------------------------------------------------------------


@pytest.mark.anyio
async def test_pg_execute_requires_a_connected_driver():
    driver = AsyncpgDriver("h", 5432, "db", "u", "p")

    with pytest.raises(AsyncSqlConnectionError):
        await driver.execute("SELECT 1", {})


@pytest.mark.anyio
async def test_pg_execute_translates_template_and_unescapes_percent():
    connection = _FakeConnection(columns=["n"], param_types=["int4", "text"])
    driver = _make_pg_driver(connection)

    result = await driver.execute("SELECT %(@ID)s, %(@Name)s WHERE x LIKE 'a%%'", {"@ID": "5", "@Name": "foo"})

    assert connection.prepared_sql == "SELECT $1, $2 WHERE x LIKE 'a%'"
    assert connection.statement.cursor_args == (5, "foo")
    await result.close()


@pytest.mark.anyio
async def test_pg_execute_acquires_with_a_timeout():
    driver = _make_pg_driver()

    result = await driver.execute("SELECT 1", {})

    assert driver._pool.acquire_timeout == AsyncpgDriver._acquire_timeout_secs  # type: ignore
    await result.close()


@pytest.mark.anyio
async def test_pg_execute_opens_a_cursor_in_a_read_only_transaction():
    """A cursor only lives in a transaction. No row is read before the first fetch()."""
    connection = _FakeConnection(columns=["n"], records=[_FakeRecord(n=1)])

    result = await _pg_result(connection)

    assert connection.readonly is True
    assert connection.steps == ["begin", "prepare", "cursor"]
    await result.close()


@pytest.mark.anyio
async def test_pg_execute_keeps_the_connection_until_the_result_is_closed():
    connection = _FakeConnection(columns=["n"])
    driver = _make_pg_driver(connection)

    result = await driver.execute("SELECT n FROM t", {})
    assert driver._pool.released == []  # type: ignore

    await result.close()
    assert driver._pool.released == [connection]  # type: ignore


@pytest.mark.anyio
async def test_pg_execute_returns_columns_even_without_rows():
    result = await _pg_result(_FakeConnection(columns=["a", "b"], records=[]))

    assert result.columns() == ["a", "b"]
    assert await result.fetch(10) == []
    await result.close()


@pytest.mark.anyio
@pytest.mark.parametrize("error", _PG_CONNECTIVITY_ERRORS)
async def test_pg_execute_translates_acquire_errors(error):
    """TimeoutError: pool saturated."""
    driver = _make_pg_driver(acquire_error=error)

    with pytest.raises(AsyncSqlConnectionError) as exc_info:
        await driver.execute("SELECT 1", {})

    assert exc_info.value.__cause__ is error
    assert driver._pool.released == []  # type: ignore  # nothing was acquired


@pytest.mark.anyio
@pytest.mark.parametrize("error", _PG_CONNECTIVITY_ERRORS)
@pytest.mark.parametrize("stage", ["begin", "prepare", "cursor"])
async def test_pg_execute_translates_connectivity_errors_and_releases_the_connection(error, stage):
    connection = _FakeConnection(errors={stage: error})
    driver = _make_pg_driver(connection)

    with pytest.raises(AsyncSqlConnectionError) as exc_info:
        await driver.execute("SELECT 1", {})

    assert exc_info.value.__cause__ is error
    assert driver._pool.released == [connection]  # type: ignore


@pytest.mark.anyio
@pytest.mark.parametrize(
    "error",
    [
        asyncpg.PostgresSyntaxError("syntax error"),
        asyncpg.QueryCanceledError("statement timeout"),
        asyncpg.ReadOnlySQLTransactionError("read-only transaction"),
        asyncpg.DataError("invalid input"),
    ],
)
@pytest.mark.parametrize("stage", ["prepare", "cursor"])
async def test_pg_execute_translates_sql_errors_and_rolls_back(error, stage):
    connection = _FakeConnection(errors={stage: error})

    with pytest.raises(AsyncSqlExecutionError) as exc_info:
        await _pg_result(connection)

    assert exc_info.value.__cause__ is error
    assert connection.steps[-2:] == ["rollback", "release"]


@pytest.mark.anyio
async def test_pg_execute_invalid_parameter_value_rolls_back_and_releases():
    connection = _FakeConnection(param_types=["int4"])

    with pytest.raises(AsyncSqlExecutionError, match=r"\$1"):
        await _make_pg_driver(connection).execute("SELECT %(@i)s", {"@i": "abc"})

    assert connection.steps == ["begin", "prepare", "rollback", "release"]


@pytest.mark.anyio
async def test_pg_cancelled_execute_rolls_back_and_releases():
    connection = _FakeConnection(blocked="prepare")
    execute_task = asyncio.create_task(_pg_result(connection))
    await connection.entered.wait()

    execute_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await execute_task

    assert connection.steps == ["begin", "prepare", "prepare cancelled", "rollback", "release"]


# --- parameter coercion ---------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "pg_type, value, expected",
    [
        ("int2", "5", 5),
        ("int4", "5", 5),
        ("int8", 5, 5),
        ("float8", "1.5", 1.5),
        ("bool", 1, True),
        ("bool", "0", False),
        ("date", "2024-01-02", date(2024, 1, 2)),
        ("timestamp", "2024-01-02 03:04:05", datetime(2024, 1, 2, 3, 4, 5)),
        ("timestamptz", "2024-01-02 03:04:05", datetime(2024, 1, 2, 3, 4, 5)),
        ("uuid", "9836f28d-cbdb-4d34-95d2-d4110f4d37ff", uuid.UUID("9836f28d-cbdb-4d34-95d2-d4110f4d37ff")),
        ("text", "abc", "abc"),  # asyncpg accepts a str for text/numeric as is
        ("numeric", "1.5", "1.5"),
        ("some_unknown_type", "x", "x"),
        ("date", date(2024, 1, 2), date(2024, 1, 2)),  # already the right type
    ],
)
def test_pg_coerce_args_converts_by_expected_type(pg_type, value, expected):
    assert _make_pg_driver()._coerce_args([_Named(pg_type)], [value]) == [expected]


@pytest.mark.unit
def test_pg_coerce_args_keeps_none():
    assert _make_pg_driver()._coerce_args([_Named("int4"), _Named("date")], [None, None]) == [None, None]


@pytest.mark.unit
def test_pg_coerce_args_reports_the_invalid_parameter():
    with pytest.raises(AsyncSqlExecutionError) as exc_info:
        _make_pg_driver()._coerce_args([_Named("text"), _Named("int4")], ["ok", "abc"])

    assert "$2" in str(exc_info.value)
    assert "int4" in str(exc_info.value)
    assert "'abc'" in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, ValueError)


# --- result -------------------------------------------------------------------


@pytest.mark.anyio
async def test_pg_result_columns_returns_a_copy():
    result = await _pg_result(_FakeConnection(columns=["a"]))

    result.columns().append("b")

    assert result.columns() == ["a"]
    await result.close()


@pytest.mark.anyio
async def test_pg_result_fetches_tuples_batch_by_batch_through_the_cursor():
    connection = _FakeConnection(columns=["id", "label"], records=[_FakeRecord(id=i, label=f"r{i}") for i in range(5)])
    result = await _pg_result(connection)

    batches = [await result.fetch(2) for _ in range(4)]

    assert batches == [[(0, "r0"), (1, "r1")], [(2, "r2"), (3, "r3")], [(4, "r4")], []]
    assert connection.statement.cursor_obj.fetch_sizes == [2, 2, 2, 2]  # type: ignore
    await result.close()


@pytest.mark.anyio
async def test_pg_result_makes_values_json_safe():
    record = _FakeRecord(u=uuid.UUID(int=1), raw=b"\xde\xad", n=1)
    result = await _pg_result(_FakeConnection(columns=["u", "raw", "n"], records=[record]))

    assert await result.fetch(10) == [("00000000-0000-0000-0000-000000000001", "\\xdead", 1)]
    await result.close()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "error, expected",
    [
        (asyncpg.QueryCanceledError("canceling statement due to statement timeout"), AsyncSqlExecutionError),
        (
            asyncpg.ReadOnlySQLTransactionError("cannot execute CREATE TABLE in a read-only transaction"),
            AsyncSqlExecutionError,
        ),
        (asyncpg.DivisionByZeroError("division by zero"), AsyncSqlExecutionError),
        (asyncpg.InterfaceError("connection is closed"), AsyncSqlConnectionError),
        (ConnectionResetError("connection reset"), AsyncSqlConnectionError),
    ],
)
async def test_pg_fetch_errors_are_translated(error, expected):
    """The query runs as rows are fetched: statement timeout, read-only refusal... surface in fetch()."""
    result = await _pg_result(_FakeConnection(columns=["n"], errors={"fetch": error}))

    with pytest.raises(expected) as exc_info:
        await result.fetch(10)

    assert exc_info.value.__cause__ is error
    await result.close()


@pytest.mark.anyio
async def test_pg_close_rolls_back_then_releases_and_can_be_called_several_times():
    connection = _FakeConnection(columns=["n"], records=[_FakeRecord(n=1)])
    driver = _make_pg_driver(connection)
    result = await driver.execute("SELECT n FROM t", {})

    await result.close()
    await result.close()

    assert connection.steps == ["begin", "prepare", "cursor", "rollback", "release"]
    assert driver._pool.released == [connection]  # type: ignore  # released once only


@pytest.mark.anyio
async def test_pg_fetch_after_close_returns_no_row():
    connection = _FakeConnection(columns=["n"], records=[_FakeRecord(n=1)])
    result = await _pg_result(connection)

    await result.close()

    assert await result.fetch(10) == []
    assert "fetch" not in connection.steps


@pytest.mark.anyio
async def test_pg_cancelled_fetch_cancels_the_query():
    """Client gone: stream_result_ndjson cancels its fetch task, which must cancel the query on PostgreSQL
    right away (asyncpg does on a cancelled call) - not leave it running until close()."""
    connection = _FakeConnection(columns=["n"], records=[_FakeRecord(n=1)], blocked="fetch")
    result = await _pg_result(connection)
    fetch_task = asyncio.create_task(result.fetch(10))
    await connection.entered.wait()

    fetch_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await fetch_task

    assert connection.steps[-1] == "fetch cancelled"
    await result.close()
    assert connection.steps[-2:] == ["rollback", "release"]


@pytest.mark.anyio
async def test_pg_close_cancels_the_running_fetch_before_rolling_back():
    connection = _FakeConnection(columns=["n"], records=[_FakeRecord(n=1)], blocked="fetch")
    result = await _pg_result(connection)
    fetch_task = asyncio.create_task(result.fetch(10))
    await connection.entered.wait()

    await result.close()

    assert connection.steps[-4:] == ["fetch", "fetch cancelled", "rollback", "release"]
    with pytest.raises(asyncio.CancelledError):
        await fetch_task


@pytest.mark.anyio
async def test_pg_cancelled_close_keeps_closing():
    """A caller cancelled while waiting in close() (e.g. the request task) can't leave the connection taken."""
    connection = _FakeConnection(columns=["n"], blocked="rollback")
    driver = _make_pg_driver(connection)
    result = await driver.execute("SELECT n FROM t", {})
    close_task = asyncio.create_task(result.close())
    await connection.entered.wait()

    close_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await close_task
    connection.gate.set()
    await result.close()  # waits for the closing already under way

    assert connection.steps[-2:] == ["rollback", "release"]
    assert driver._pool.released == [connection]  # type: ignore


@pytest.mark.anyio
@pytest.mark.parametrize(
    "error",
    [
        asyncpg.InterfaceError("connection is closed"),
        ConnectionResetError("connection reset"),
        asyncpg.InternalServerError("unexpected"),
    ],
)
async def test_pg_failed_rollback_terminates_the_connection(error):
    """A connection that can't be rolled back can't be reused - the pool replaces a terminated one."""
    connection = _FakeConnection(columns=["n"], errors={"rollback": error})
    result = await _pg_result(connection)

    await result.close()  # must not raise

    assert connection.steps[-3:] == ["rollback", "terminate", "release"]


# ================================================================================
# _json_safe
# ================================================================================


@pytest.mark.unit
@pytest.mark.parametrize(
    "value",
    [1, "x", True, None, 1.5, Decimal("1.50"), datetime(2024, 1, 2, 3, 4, 5), date(2024, 1, 2)],
)
def test_json_safe_keeps_values_encode_row_value_handles(value):
    assert _json_safe(value) is value


@pytest.mark.unit
@pytest.mark.parametrize(
    "value, expected",
    [
        (uuid.UUID("9836f28d-cbdb-4d34-95d2-d4110f4d37ff"), "9836f28d-cbdb-4d34-95d2-d4110f4d37ff"),
        (time(13, 5, 9), "13:05:09"),
        (timedelta(days=1), "1 day, 0:00:00"),
        (ipaddress.ip_address("10.0.0.1"), "10.0.0.1"),
        (b"\xde\xad", "\\xdead"),
    ],
)
def test_json_safe_makes_json_unsafe_values_strings(value, expected):
    assert _json_safe(value) == expected


@pytest.mark.unit
def test_json_safe_converts_array_elements_which_encode_row_value_would_not_reach():
    """encode_row_value only handles top-level values - a Decimal/date inside an array would break json.dumps."""
    assert _json_safe([Decimal("1.5"), Decimal("2")]) == ["1.5", "2"]
    assert _json_safe([date(2024, 1, 2)]) == ["2024-01-02"]
    assert _json_safe([1, 2]) == [1, 2]
    assert _json_safe([[uuid.UUID(int=1)]]) == [["00000000-0000-0000-0000-000000000001"]]


@pytest.mark.unit
def test_json_safe_values_are_always_json_serializable_after_encode_row_value():
    values = [
        uuid.uuid4(),
        time(1, 2, 3),
        timedelta(hours=1),
        ipaddress.ip_address("::1"),
        b"\x00",
        Decimal("3.14"),
        datetime(2024, 1, 2),
        date(2024, 1, 2),
        [Decimal("1"), None],
        None,
    ]

    json.dumps([encode_row_value(_json_safe(value)) for value in values])  # must not raise
