"""
Tests for Pytre_Core/sql_query/query_drivers_async.py
"""

import ipaddress
import json
import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, Mock, patch

import asyncpg
import fastmssql
import pytest

from Pytre_Core.remotes import encode_row_value
from Pytre_Core.servers import ServerType
from Pytre_Core.sql_query.query_drivers_async import (
    AsyncpgDriver,
    AsyncSqlConnectionError,
    AsyncSqlExecutionError,
    FastMssqlDriver,
    _AsyncpgResult,
    _FastMssqlResult,
    get_driver,
    _json_safe,
    _to_positional_style,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _make_server(
    host: str = "h", port: str = "1433", trust_server_certificate: bool = False, server_type: str = ServerType.mssql.name
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
    """Mirrors the local pymssql path's appname=APP_ID/read_only=True -
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


@pytest.mark.anyio
async def test_execute_wraps_the_query_stream_in_a_result():
    stream = _FakeQueryStream(["n"], [(1,)])
    fake_connection = Mock()
    fake_connection.query = AsyncMock(return_value=stream)

    with patch("Pytre_Core.sql_query.query_drivers_async.fastmssql.Connection", return_value=fake_connection):
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
async def test_get_driver_dispatches_postgre_to_asyncpg_driver(monkeypatch):
    fake_driver = Mock()
    monkeypatch.setattr(AsyncpgDriver, "_driver", Mock(return_value=fake_driver))
    server = _make_server(server_type=ServerType.postgre.name)

    driver = await get_driver(server)

    assert driver is fake_driver


@pytest.mark.anyio
async def test_get_driver_unknown_type_raises_error():
    server = Mock(type="unknown")

    with pytest.raises(ValueError):
        await get_driver(server)


# ================================================================================
# AsyncpgDriver - fakes standing in for an asyncpg pool (behavior established against a real
# PostgreSQL server in tests/integration/test_asyncpg_driver.py)
# ================================================================================


class _Named:
    """Stands in for asyncpg's Attribute (column) and Type (parameter) objects - only .name is read."""

    def __init__(self, name: str) -> None:
        self.name = name


class _FakeRecord(dict):
    """asyncpg.Record exposes .items() like a dict."""


class _FakeStatement:
    def __init__(self, columns=(), param_types=(), records=(), fetch_error=None) -> None:
        self._columns = columns
        self._param_types = param_types
        self._records = records
        self._fetch_error = fetch_error
        self.fetch_args: tuple = ()

    def get_attributes(self):
        return [_Named(name) for name in self._columns]

    def get_parameters(self):
        return [_Named(name) for name in self._param_types]

    async def fetch(self, *args):
        self.fetch_args = args
        if self._fetch_error is not None:
            raise self._fetch_error
        return list(self._records)


class _FakeConnection:
    def __init__(self, statement: _FakeStatement, prepare_error=None) -> None:
        self._statement = statement
        self._prepare_error = prepare_error
        self.prepared_sql = ""

    async def prepare(self, sql: str):
        self.prepared_sql = sql
        if self._prepare_error is not None:
            raise self._prepare_error
        return self._statement


class _FakeAcquire:
    def __init__(self, connection: _FakeConnection, error=None) -> None:
        self._connection = connection
        self._error = error

    async def __aenter__(self):
        if self._error is not None:
            raise self._error
        return self._connection

    async def __aexit__(self, *exc_info):
        return False


class _FakePool:
    def __init__(self, connection: _FakeConnection, acquire_error=None) -> None:
        self.connection = connection
        self._acquire_error = acquire_error
        self.acquire_timeout = None
        self.closed = False

    def acquire(self, timeout=None):
        self.acquire_timeout = timeout
        return _FakeAcquire(self.connection, self._acquire_error)

    async def close(self):
        self.closed = True


def _make_pg_driver(statement=None, prepare_error=None, acquire_error=None, **kwargs) -> AsyncpgDriver:
    driver = AsyncpgDriver(host="h", port=5432, database="db", username="u", password="p", **kwargs)
    driver._pool = _FakePool(_FakeConnection(statement or _FakeStatement(), prepare_error), acquire_error)  # type: ignore
    return driver


# --- construction ---------------------------------------------------------------


@pytest.mark.unit
def test_pg_driver_verifies_certificate_by_default():
    assert AsyncpgDriver("h", 5432, "db", "u", "p")._ssl is True


@pytest.mark.unit
def test_pg_driver_trust_certificate_accepts_what_the_server_offers():
    assert AsyncpgDriver("h", 5432, "db", "u", "p", trust_certificate=True)._ssl == "prefer"


@pytest.mark.unit
def test_pg_driver_builds_from_server_parameters():
    server = _make_server(host="pg-host", port="5433", trust_server_certificate=True, server_type=ServerType.postgre.name)
    server.timeout = 30
    server.login_timeout = 5

    driver = AsyncpgDriver._driver(server)

    assert driver._host == "pg-host"
    assert driver._port == 5433
    assert driver._ssl == "prefer"
    assert driver._login_timeout == 5
    assert driver._statement_timeout_ms == 30_000


# --- connect / disconnect ---------------------------------------------------------


@pytest.mark.anyio
async def test_pg_connect_creates_a_read_only_pool_with_timeouts():
    create_pool = AsyncMock(return_value=Mock())
    driver = AsyncpgDriver("h", 5432, "db", "u", "p", login_timeout=7, statement_timeout_secs=12)

    with patch("Pytre_Core.sql_query.query_drivers_async.asyncpg.create_pool", create_pool):
        await driver.connect()

    kwargs = create_pool.call_args.kwargs
    assert kwargs["ssl"] is True
    assert kwargs["timeout"] == 7
    assert kwargs["server_settings"]["default_transaction_read_only"] == "on"
    assert kwargs["server_settings"]["statement_timeout"] == "12000"
    assert kwargs["server_settings"]["application_name"].startswith("Pytre_Server_")


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

    with patch("Pytre_Core.sql_query.query_drivers_async.asyncpg.create_pool", AsyncMock(side_effect=error)):
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
    statement = _FakeStatement(columns=["n"], param_types=["int4", "text"])
    driver = _make_pg_driver(statement)

    await driver.execute("SELECT %(@ID)s, %(@Name)s WHERE x LIKE 'a%%'", {"@ID": "5", "@Name": "foo"})

    assert driver._pool.connection.prepared_sql == "SELECT $1, $2 WHERE x LIKE 'a%'"  # type: ignore
    assert statement.fetch_args == (5, "foo")


@pytest.mark.anyio
async def test_pg_execute_acquires_with_a_timeout():
    driver = _make_pg_driver()

    await driver.execute("SELECT 1", {})

    assert driver._pool.acquire_timeout == AsyncpgDriver._acquire_timeout_secs  # type: ignore


@pytest.mark.anyio
async def test_pg_execute_returns_columns_even_without_rows():
    driver = _make_pg_driver(_FakeStatement(columns=["a", "b"], records=[]))

    result = await driver.execute("SELECT a, b FROM t WHERE false", {})

    assert result.columns() == ["a", "b"]
    assert await result.fetch(10) == []


@pytest.mark.anyio
async def test_pg_execute_returns_rows_as_tuples():
    statement = _FakeStatement(columns=["id", "label"], records=[_FakeRecord(id=1, label="x"), _FakeRecord(id=2, label="y")])
    driver = _make_pg_driver(statement)

    result = await driver.execute("SELECT id, label FROM t", {})

    assert await result.fetch(10) == [(1, "x"), (2, "y")]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "error",
    [
        OSError("connection reset"),
        TimeoutError(),  # pool saturated
        asyncpg.InterfaceError("connection is closed"),
        asyncpg.ConnectionFailureError("server closed the connection"),
        asyncpg.CannotConnectNowError("starting up"),
        asyncpg.TooManyConnectionsError("too many"),
    ],
)
@pytest.mark.parametrize("stage", ["acquire", "prepare", "fetch"])
async def test_pg_execute_translates_connectivity_errors(error, stage):
    kwargs = {
        "acquire": {"acquire_error": error},
        "prepare": {"prepare_error": error},
        "fetch": {"statement": _FakeStatement(fetch_error=error)},
    }[stage]
    driver = _make_pg_driver(**kwargs)

    with pytest.raises(AsyncSqlConnectionError) as exc_info:
        await driver.execute("SELECT 1", {})

    assert exc_info.value.__cause__ is error


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
async def test_pg_execute_translates_sql_errors(error):
    driver = _make_pg_driver(prepare_error=error)

    with pytest.raises(AsyncSqlExecutionError) as exc_info:
        await driver.execute("SELECT 1", {})

    assert exc_info.value.__cause__ is error


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


# --- result adapter ---------------------------------------------------------------


@pytest.mark.unit
def test_pg_result_columns_returns_a_copy():
    result = _AsyncpgResult(["a"], [])

    result.columns().append("b")

    assert result.columns() == ["a"]


@pytest.mark.anyio
async def test_pg_result_fetches_tuples_batch_by_batch():
    records = [_FakeRecord(id=i, label=f"r{i}") for i in range(5)]
    result = _AsyncpgResult(["id", "label"], records)  # type: ignore

    batches = [await result.fetch(2) for _ in range(4)]

    assert batches == [[(0, "r0"), (1, "r1")], [(2, "r2"), (3, "r3")], [(4, "r4")], []]


@pytest.mark.anyio
async def test_pg_result_makes_values_json_safe():
    record = _FakeRecord(u=uuid.UUID(int=1), raw=b"\xde\xad", n=1)
    result = _AsyncpgResult(["u", "raw", "n"], [record])  # type: ignore

    assert await result.fetch(10) == [("00000000-0000-0000-0000-000000000001", "\\xdead", 1)]


@pytest.mark.anyio
async def test_pg_result_close_releases_records_and_can_be_called_several_times():
    result = _AsyncpgResult(["id"], [_FakeRecord(id=1), _FakeRecord(id=2)])  # type: ignore

    await result.close()
    await result.close()

    assert await result.fetch(10) == []


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
        uuid.uuid4(), time(1, 2, 3), timedelta(hours=1), ipaddress.ip_address("::1"), b"\x00",
        Decimal("3.14"), datetime(2024, 1, 2), date(2024, 1, 2), [Decimal("1"), None], None,
    ]

    json.dumps([encode_row_value(_json_safe(value)) for value in values])  # must not raise
