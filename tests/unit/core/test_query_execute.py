"""
Tests for Pytre/sql_query/query_execute.py
"""

import pytest
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

import pymssql

from Pytre_Core.context import AppContext, AppMode
from Pytre_Core.remotes import RemoteCursor, RemoteExecutionError, RemoteHTTPStatusError, RemoteRequestError
from Pytre_Core.sql_query.query_drivers import ServerConnectionError
from Pytre_Core.sql_query.query_execute import QueryExecute
from Pytre_Core.sql_query.execute_config import LocalExecuteCfg, RemoteExecuteCfg
from Pytre_Core.user_prefs import CsvFormat, DisplayFormat


# ================================================================================
# Fixtures
# ================================================================================


@pytest.fixture
def mock_settings():
    """Mock Settings for QueryExecute"""
    return Mock()


@pytest.fixture
def mock_cursor():
    cursor = MagicMock()
    cursor.__enter__ = Mock(return_value=cursor)
    cursor.__exit__ = Mock(return_value=False)
    cursor.description = [("col1",), ("col2",)]
    cursor.__iter__ = Mock(return_value=iter([]))
    return cursor


@pytest.fixture
def mock_conn(mock_cursor):
    conn = MagicMock()
    conn.__enter__ = Mock(return_value=conn)
    conn.__exit__ = Mock(return_value=False)
    conn.cursor = Mock(return_value=mock_cursor)
    return conn


@pytest.fixture
def mock_server():
    server = Mock()
    server.id = "SRV1"
    server.charset = "UTF-8"
    return server


@pytest.fixture(autouse=True)
def patched_get_connection(mock_conn, monkeypatch):
    """_execute_local() gets its connection via the free function get_connection(server)
    (Pytre_Core/sql_query/query_drivers.py, imported into query_execute's own namespace) rather
    than a server.get_connection() method - patch it at that namespace (not query_drivers itself,
    since that's where _execute_local() actually looks the name up) so every test exercising the
    local execution path gets mock_conn without touching a real SQL driver."""
    monkeypatch.setattr("Pytre_Core.sql_query.query_execute.get_connection", Mock(return_value=mock_conn))


@pytest.fixture
def exec_app_ctx(mock_settings, mock_server):
    ctx = Mock()
    ctx.settings = mock_settings
    ctx.servers = Mock()
    ctx.servers.servers_dict = {"SRV1": mock_server}
    return ctx


@pytest.fixture
def exec_user_ctx():
    ctx = Mock()
    ctx.user_logs = Mock()
    ctx.user_logs.db_path = "/fake/path/Pytre_Logs.db"  # must stay picklable (goes through a real mp.Queue)
    ctx.current_user = Mock()
    ctx.current_user.username = "u1"
    ctx.current_user.title = "User One"
    ctx.display_format = DisplayFormat()
    return ctx


@pytest.fixture
def base_config(tmp_path):
    return LocalExecuteCfg(
        query_id="Q1",
        server_id="SRV1",
        cmd_template="SELECT %(p)s",
        cmd_parameters={"p": 1},
        extract_file=Path(),
        params_metadata={},
    )


class _FakeRemote:
    """Minimal stand-in for RemoteServer - QueryExecute only calls .id and .execute()."""

    def __init__(self, columns: tuple[str, ...], rows: list[tuple], error: Exception | None = None):
        self.id = "REMOTE1"
        self._columns = columns
        self._rows = rows
        self._error = error

    @contextmanager
    def execute(self, query_id, template_hash, display_format, display_parameters):
        if self._error is not None:
            raise self._error
        # a real RemoteCursor, not a hand-rolled stand-in - production code does a genuine
        # isinstance(cursor, RemoteCursor) check now, duck-typing alone would fail it
        yield RemoteCursor(self._columns, (row for row in self._rows))


@pytest.fixture
def fake_remote() -> _FakeRemote:
    return _FakeRemote(columns=("col1", "col2"), rows=[("a", "1"), ("b", "2")])


@pytest.fixture
def remote_config(fake_remote) -> RemoteExecuteCfg:
    return RemoteExecuteCfg(
        query_id="Q1",  # display label (extract filename / UserLogs) - distinct from remote_query_id on purpose
        remote_query_id="RQ1",  # collision-proof id actually sent over the wire
        server_id="REMOTE_SQL_SRV",
        remote_server=fake_remote,
        template_hash="hash1",
        display_format=DisplayFormat(date_format="%Y-%m-%d", decimal_separator="."),
        display_parameters={"p": "v"},
        extract_file=Path(),
        params_metadata={},
    )


@pytest.fixture
def mock_query(base_config):
    """Minimal stand-in for Query - QueryExecute only calls update_values()/values_ok()/
    get_exec_config(). Override get_exec_config's return value to exercise a different
    LocalExecuteCfg/RemoteExecuteCfg."""
    query = Mock()
    query.update_values = Mock(return_value=True)
    query.values_ok = Mock(return_value=True)
    query.get_exec_config = Mock(return_value=base_config)
    query.display_format = DisplayFormat()
    return query


@pytest.fixture
def qe(exec_app_ctx, exec_user_ctx, mock_query):
    """A QueryExecute wired to mock_query by default."""
    return QueryExecute(exec_app_ctx, exec_user_ctx, query=mock_query)


# ================================================================================
# QueryExecute._get_server
# ================================================================================


@pytest.mark.unit
def test_query_execute_get_server_found(exec_app_ctx, exec_user_ctx, mock_server):
    qe = QueryExecute(exec_app_ctx, exec_user_ctx)

    result = qe._get_server("SRV1")

    assert result is mock_server


@pytest.mark.unit
def test_query_execute_get_server_not_found(exec_app_ctx, exec_user_ctx):
    qe = QueryExecute(exec_app_ctx, exec_user_ctx)

    result = qe._get_server("UNKNOWN")

    assert result is None


# ================================================================================
# QueryExecute._load_exec_config
# ================================================================================


@pytest.mark.unit
def test_load_exec_config_requires_a_query(exec_app_ctx, exec_user_ctx):
    qe = QueryExecute(exec_app_ctx, exec_user_ctx)

    with pytest.raises(ValueError, match="query is required"):
        qe._load_exec_config("SRV1", True)


@pytest.mark.unit
def test_load_exec_config_local_sets_server_and_config(qe, base_config):
    qe._load_exec_config("SRV1", True)

    assert qe.config is base_config
    assert qe.server is not None
    assert qe.remote is None


@pytest.mark.unit
def test_load_exec_config_local_unknown_server_leaves_server_none(qe, mock_query, base_config):
    mock_query.get_exec_config.return_value = replace(base_config, server_id="UNKNOWN")

    qe._load_exec_config("UNKNOWN", True)

    assert qe.server is None
    assert qe.config is not None


@pytest.mark.unit
def test_load_exec_config_remote_sets_remote_and_skips_local_lookup(qe, mock_query, remote_config, fake_remote):
    mock_query.get_exec_config.return_value = remote_config

    qe._load_exec_config("", True)

    assert qe.remote is fake_remote
    assert qe.server is None
    assert qe.config is remote_config


# ================================================================================
# QueryExecute._check_exec_config
# ================================================================================


@pytest.mark.unit
def test_check_exec_config_raises_if_config_missing(exec_app_ctx, exec_user_ctx):
    qe = QueryExecute(exec_app_ctx, exec_user_ctx)

    with pytest.raises(ValueError, match="manquante"):
        qe._check_exec_config()


@pytest.mark.unit
def test_check_exec_config_raises_if_extract_file_exists(qe, base_config, tmp_path):
    existing = tmp_path / "existing.csv"
    existing.write_text("data")
    qe.config = replace(base_config, extract_file=existing)

    with pytest.raises(FileExistsError):
        qe._check_exec_config()


@pytest.mark.unit
def test_check_exec_config_raises_if_local_server_not_set(qe, base_config):
    qe.config = base_config
    qe.server = None

    with pytest.raises(ValueError, match="serveur"):
        qe._check_exec_config()


@pytest.mark.unit
def test_check_exec_config_raises_if_local_cmd_template_empty(qe, mock_server, base_config):
    qe.config = replace(base_config, cmd_template="")
    qe.server = mock_server

    with pytest.raises(ValueError, match="cmd_template"):
        qe._check_exec_config()


@pytest.mark.unit
def test_check_exec_config_raises_if_remote_not_set(qe, remote_config):
    qe.config = remote_config
    qe.remote = None

    with pytest.raises(ValueError, match="remote"):
        qe._check_exec_config()


@pytest.mark.unit
def test_check_exec_config_raises_if_template_hash_empty(qe, remote_config, fake_remote):
    qe.config = replace(remote_config, template_hash="")
    qe.remote = fake_remote

    with pytest.raises(ValueError, match="template_hash"):
        qe._check_exec_config()


@pytest.mark.unit
def test_check_exec_config_passes_for_a_valid_local_config(qe, mock_server, base_config):
    qe.config = base_config
    qe.server = mock_server

    qe._check_exec_config()  # must not raise


# ================================================================================
# QueryExecute.execute - local
# ================================================================================


@pytest.mark.unit
def test_execute_requires_a_query(exec_app_ctx, exec_user_ctx):
    qe = QueryExecute(exec_app_ctx, exec_user_ctx)

    with pytest.raises(ValueError, match="query is required"):
        qe.execute()


@pytest.mark.unit
def test_execute_returns_error_if_values_invalid(qe, mock_query):
    mock_query.values_ok.return_value = False

    result = qe.execute()

    assert result == (-1, None)


@pytest.mark.unit
def test_execute_returns_error_if_load_or_check_fails(qe, mock_query, base_config):
    """Covers both _load_exec_config and _check_exec_config failures - execute()'s try/except
    around them doesn't distinguish, any exception there is reported the same way."""
    mock_query.get_exec_config.return_value = replace(base_config, server_id="UNKNOWN")

    result = qe.execute()

    assert result == (-1, None)


@pytest.mark.unit
def test_execute_returns_error_if_stop_requested(qe):
    qe.stop_requested.set()

    result = qe.execute()

    assert result == (-1, None)


@pytest.mark.unit
def test_execute_handles_operational_error(qe, mock_cursor):
    mock_cursor.execute = Mock(side_effect=ServerConnectionError("connection failed"))

    result = qe.execute()

    assert result == (-1, None)


@pytest.mark.unit
def test_execute_handles_programming_error(qe, mock_cursor):
    mock_cursor.execute = Mock(side_effect=pymssql._pymssql.ProgrammingError("bad sql"))

    result = qe.execute()

    assert result == (-1, None)


@pytest.mark.unit
def test_execute_handles_unexpected_driver_error(qe, mock_cursor):
    mock_cursor.execute = Mock(side_effect=pymssql._pymssql.OperationalError("boom"))

    result = qe.execute()

    assert result == (-1, None)


@pytest.mark.unit
def test_execute_handles_any_other_unexpected_error(qe, mock_cursor):
    """execute() now owns the whole orchestration (was split between Query.execute()'s
    catch-all and QueryExecute.execute() previously) - a plain, non-driver exception raised
    while running the query is caught here too, not just driver-specific ones."""
    mock_cursor.execute = Mock(side_effect=ValueError("boom"))

    result = qe.execute()

    assert result == (-1, None)


@pytest.mark.unit
def test_execute_returns_list_when_no_extract_file(qe, mock_cursor):
    mock_cursor.__iter__ = Mock(return_value=iter([("a", 1), ("b", 2)]))

    rows_count, output = qe.execute()

    assert rows_count == 2
    assert isinstance(output, list)
    assert output[0] == ["col1", "col2"]
    assert len(output) == 3  # header + 2 rows


@pytest.mark.unit
def test_execute_writes_to_file_and_logs(qe, mock_query, base_config, mock_cursor, tmp_path):
    mock_cursor.__iter__ = Mock(return_value=iter([("a", 1)]))
    extract_file = tmp_path / "out.csv"
    mock_query.get_exec_config.return_value = replace(base_config, extract_file=extract_file)

    rows_count, output_path = qe.execute()

    assert rows_count == 1
    assert output_path == extract_file
    assert extract_file.exists()
    assert qe.user_ctx.user_logs.insert_exec.called


@pytest.mark.unit
def test_execute_removes_file_on_permission_error(qe, mock_query, base_config, tmp_path):
    extract_file = tmp_path / "out.csv"
    mock_query.get_exec_config.return_value = replace(base_config, extract_file=extract_file)

    with patch.object(qe, "_extract_to_file", side_effect=PermissionError()):
        result = qe.execute()

    assert result == (-1, None)
    assert not extract_file.exists()


@pytest.mark.unit
def test_execute_handles_file_not_found_error(qe, mock_query, base_config, tmp_path):
    extract_file = tmp_path / "sub" / "out.csv"
    mock_query.get_exec_config.return_value = replace(base_config, extract_file=extract_file)

    with patch.object(qe, "_extract_to_file", side_effect=FileNotFoundError("[Errno 2] No such file or directory")):
        result = qe.execute()

    assert result == (-1, None)


@pytest.mark.unit
def test_execute_end_writes_user_log_and_central_log_if_enabled(qe, exec_app_ctx, exec_user_ctx):
    import queue as queue_module

    qe._load_exec_config("SRV1", True)
    qe.queue = queue_module.Queue()  # avoid multiprocessing.Queue's async pickling for this in-process check
    exec_app_ctx.settings.logs_are_on = True

    start = datetime(2026, 1, 1, 10, 0, 0)
    end = datetime(2026, 1, 1, 10, 0, 5)
    qe._execute_log_results(start, end, 5)

    exec_user_ctx.user_logs.insert_exec.assert_called_once()
    # central_log message queued since logs_are_on is True
    messages = []
    while not qe.queue.empty():
        messages.append(qe.queue.get())
    assert any(m[0] == "central_log" for m in messages)


@pytest.mark.unit
def test_execute_end_skips_central_log_if_disabled(qe, exec_app_ctx, exec_user_ctx):
    import queue as queue_module

    qe._load_exec_config("SRV1", True)
    qe.queue = queue_module.Queue()
    exec_app_ctx.settings.logs_are_on = False

    qe._execute_log_results(datetime(2026, 1, 1), datetime(2026, 1, 1, 0, 0, 1), 1)

    messages = []
    while not qe.queue.empty():
        messages.append(qe.queue.get())
    assert not any(m[0] == "central_log" for m in messages)


# ================================================================================
# QueryExecute._extract_to_list / _extract_to_file / _convert_sql_record
# ================================================================================


@pytest.mark.unit
def test_extract_to_list_stops_if_requested(exec_app_ctx, exec_user_ctx, mock_cursor):
    qe = QueryExecute(exec_app_ctx, exec_user_ctx)
    mock_cursor.__iter__ = Mock(return_value=iter([("a", 1), ("b", 2)]))
    qe.server = exec_app_ctx.servers.servers_dict["SRV1"]
    qe.stop_requested.set()

    rows_count, buffer = qe._extract_to_list(mock_cursor)

    assert rows_count == 0
    assert buffer == []


@pytest.mark.unit
def test_extract_to_file_raises_without_extract_file(exec_app_ctx, exec_user_ctx, mock_cursor):
    qe = QueryExecute(exec_app_ctx, exec_user_ctx)
    qe.config = LocalExecuteCfg()  # extract_file defaults to Path()

    with pytest.raises(ValueError, match="not possible"):
        qe._extract_to_file(mock_cursor)


@pytest.mark.unit
def test_extract_to_file_uses_csv_format_from_config(exec_app_ctx, exec_user_ctx, base_config, mock_cursor, tmp_path):
    qe = QueryExecute(exec_app_ctx, exec_user_ctx)
    qe.server = exec_app_ctx.servers.servers_dict["SRV1"]
    extract_file = tmp_path / "out.csv"
    csv_format = CsvFormat(delimiter="|", quotechar="'", encoding="latin-1")
    qe.config = replace(base_config, extract_file=extract_file, csv_format=csv_format)
    mock_cursor.__iter__ = Mock(return_value=iter([("é|x", 1)]))

    rows_count, _path = qe._extract_to_file(mock_cursor)

    assert rows_count == 1
    assert extract_file.read_bytes().decode("latin-1") == "col1|col2\r\n'é|x'|1\r\n"


@pytest.mark.unit
def test_extract_to_file_uses_default_csv_format(exec_app_ctx, exec_user_ctx, base_config, mock_cursor, tmp_path):
    qe = QueryExecute(exec_app_ctx, exec_user_ctx)
    qe.server = exec_app_ctx.servers.servers_dict["SRV1"]
    extract_file = tmp_path / "out.csv"
    qe.config = replace(base_config, extract_file=extract_file)
    mock_cursor.__iter__ = Mock(return_value=iter([("a;b", "é")]))

    rows_count, _path = qe._extract_to_file(mock_cursor)

    assert rows_count == 1
    assert extract_file.read_bytes().decode("utf-8-sig") == 'col1;col2\r\n"a;b";é\r\n'


@pytest.mark.unit
def test_extract_to_file_removes_empty_file(exec_app_ctx, exec_user_ctx, base_config, mock_cursor, tmp_path):
    qe = QueryExecute(exec_app_ctx, exec_user_ctx)
    qe.server = exec_app_ctx.servers.servers_dict["SRV1"]
    extract_file = tmp_path / "out.csv"
    qe.config = replace(base_config, extract_file=extract_file)
    mock_cursor.__iter__ = Mock(return_value=iter([]))

    rows_count, path = qe._extract_to_file(mock_cursor)

    assert rows_count == 0
    assert not extract_file.exists()


@pytest.mark.unit
def test_extract_to_file_stops_if_requested(exec_app_ctx, exec_user_ctx, base_config, mock_cursor, tmp_path):
    qe = QueryExecute(exec_app_ctx, exec_user_ctx)
    qe.server = exec_app_ctx.servers.servers_dict["SRV1"]
    extract_file = tmp_path / "out.csv"
    qe.config = replace(base_config, extract_file=extract_file)
    mock_cursor.__iter__ = Mock(return_value=iter([("a", 1), ("b", 2)]))
    qe.stop_requested.set()

    rows_count, path = qe._extract_to_file(mock_cursor)

    assert rows_count == 0
    assert path is None


@pytest.mark.unit
def test_sql_record_to_text_converts_values(exec_app_ctx, exec_user_ctx):
    qe = QueryExecute(exec_app_ctx, exec_user_ctx)
    qe.server = exec_app_ctx.servers.servers_dict["SRV1"]

    result = qe._convert_sql_record((1, "hello", None))

    # from_result() renders everything as display strings
    assert result == ["1", "hello", ""]


@pytest.mark.unit
def test_sql_record_to_text_without_server_skips_charset_fixup(exec_app_ctx, exec_user_ctx):
    """Mirrors the remote execution path: self.server is None (only self.remote is set in
    practice), so the pymssql/psycopg2 charset fixup must be skipped rather than raising -
    fastmssql already returns clean UTF-8, so there is nothing to fix up for a remote record."""
    qe = QueryExecute(exec_app_ctx, exec_user_ctx)

    result = qe._convert_sql_record((1, "hello", None))

    assert result == ["1", "hello", ""]


@pytest.mark.unit
def test_sql_record_to_text_handles_decode_error(exec_app_ctx, exec_user_ctx):
    qe = QueryExecute(exec_app_ctx, exec_user_ctx)
    server = Mock()
    server.charset = "ascii"  # too narrow, forces encode/decode fallback
    qe.server = server

    result = qe._convert_sql_record(("café",))

    assert result == ["café"]


# ================================================================================
# QueryExecute.execute - remote
# ================================================================================


@pytest.mark.unit
def test_execute_remote_returns_list_when_no_extract_file(qe, mock_query, remote_config):
    mock_query.get_exec_config.return_value = remote_config

    rows_count, output = qe.execute()

    assert rows_count == 2
    assert output == [["col1", "col2"], ["a", "1"], ["b", "2"]]


@pytest.mark.unit
def test_execute_remote_sends_remote_query_id_not_query_id(qe, mock_query, remote_config):
    """Regression: the wire call must use RemoteExecuteCfg.remote_query_id (collision-proof
    Query.file_id) - not query_id (Query.name, the display label used for the extract filename
    and UserLogs, kept separate on purpose)."""
    captured = {}

    @contextmanager
    def spy_execute(query_id, template_hash, display_format, display_parameters):
        captured["query_id"] = query_id
        yield RemoteCursor(("col1", "col2"), (row for row in [("a", "1")]))

    remote_config.remote_server.execute = spy_execute
    mock_query.get_exec_config.return_value = remote_config

    qe.execute()

    assert captured["query_id"] == "RQ1"


@pytest.mark.unit
def test_execute_remote_sends_display_format_with_display_parameters(qe, mock_query, remote_config):
    """display_parameters are sent along with the display format they were rendered with,
    so the remote parses them with the client's format and not its own."""
    captured = {}

    @contextmanager
    def spy_execute(query_id, template_hash, display_format, display_parameters):
        captured["display_format"] = display_format
        captured["display_parameters"] = display_parameters
        yield RemoteCursor(("col1",), (row for row in [("a",)]))

    remote_config.remote_server.execute = spy_execute
    mock_query.get_exec_config.return_value = remote_config

    qe.execute()

    assert captured["display_format"] == DisplayFormat(date_format="%Y-%m-%d", decimal_separator=".")
    assert captured["display_parameters"] == {"p": "v"}


@pytest.mark.unit
def test_execute_remote_writes_to_file_and_logs(qe, mock_query, remote_config, tmp_path):
    extract_file = tmp_path / "out.csv"
    mock_query.get_exec_config.return_value = replace(remote_config, extract_file=extract_file)

    rows_count, output_path = qe.execute()

    assert rows_count == 2
    assert output_path == extract_file
    assert extract_file.exists()
    assert qe.user_ctx.user_logs.insert_exec.called
    # logged against the remote's id, not a local server
    logged_id = qe.user_ctx.user_logs.insert_exec.call_args[0][0]
    assert logged_id == "REMOTE1"


@pytest.mark.unit
def test_execute_remote_in_client_mode_never_touches_the_settings_database(
    exec_user_ctx, mock_query, remote_config, tmp_path
):
    """CLIENT mode has no KeePass database: a real AppContext raises on any access to settings/servers,
    so the remote execution must complete without needing them (central logs only concern local executions)"""
    extract_file = tmp_path / "out.csv"
    mock_query.get_exec_config.return_value = replace(remote_config, extract_file=extract_file)
    qe = QueryExecute(AppContext(mode=AppMode.CLIENT), exec_user_ctx, query=mock_query)

    rows_count, output_path = qe.execute()

    assert rows_count == 2
    assert output_path == extract_file


@pytest.mark.unit
def test_execute_remote_handles_connection_error(qe, mock_query, remote_config):
    """RemoteServer.execute() never raises a raw httpx2 exception - RemoteRequestError is what
    an unreachable-remote failure actually looks like (see Remote.check_health())."""
    mock_query.get_exec_config.return_value = replace(
        remote_config, remote_server=_FakeRemote(columns=(), rows=[], error=RemoteRequestError("boom"))
    )

    result = qe.execute()

    assert result == (-1, None)


@pytest.mark.unit
def test_execute_remote_handles_server_error(qe, mock_query, remote_config):
    """A RemoteHTTPStatusError (the remote server responded with an error status) is caught by
    the broader `except RemoteError` branch in _execute_remote(), distinct from
    RemoteRequestError (remote unreachable) which has its own branch."""
    mock_query.get_exec_config.return_value = replace(
        remote_config, remote_server=_FakeRemote(columns=(), rows=[], error=RemoteHTTPStatusError("boom"))
    )

    result = qe.execute()

    assert result == (-1, None)


@pytest.mark.unit
def test_execute_remote_handles_empty_result(qe, mock_query, remote_config):
    """fastmssql can't report columns for 0 rows (confirmed against the real driver) -
    Pytre_Server falls back to an empty columns list, so the header row here is empty too."""
    mock_query.get_exec_config.return_value = replace(remote_config, remote_server=_FakeRemote(columns=(), rows=[]))

    rows_count, output = qe.execute()

    assert rows_count == 0
    assert output == []


@pytest.mark.unit
def test_execute_remote_row_conversion_error_bubbles_up(qe, mock_query, remote_config):
    """A RemoteExecutionError (server-reported __error__ line) propagates all the way out of
    execute() uncaught - it isn't a driver error and isn't ServerConnectionError, so neither
    of execute()'s specific except clauses matches it... but the final `except Exception` does,
    so this documents that it is in fact swallowed and reported as (-1, None), not re-raised."""

    def failing_rows():
        raise RemoteExecutionError("ValueError: boom")
        yield  # pragma: no cover

    class _FailingRemote:
        id = "REMOTE1"

        @contextmanager
        def execute(self, *a, **k):
            yield RemoteCursor(("col1",), failing_rows())

    mock_query.get_exec_config.return_value = replace(remote_config, remote_server=_FailingRemote())

    result = qe.execute()

    assert result == (-1, None)
