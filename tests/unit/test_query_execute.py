"""
Tests for Pytre/sql_query/query_execute.py
"""

import pytest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

from Pytre_Core.sql_query.query_execute import QueryExecute
from Pytre_Core.sql_query.execute_config import ExecuteConfig


# ================================================================================
# Fixtures
# ================================================================================


@pytest.fixture
def mock_settings():
    """Mock Settings providing valid defaults for SqlParam/CmdBuilder/QueryExecute"""
    mock = Mock()
    mock.date_format = "%d/%m/%Y"
    mock.decimal_separator = ","
    mock.field_separator = ";"
    mock.extract_encoding = "utf-8-sig"
    return mock


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
def mock_server(mock_conn):
    server = Mock()
    server.id = "SRV1"
    server.charset = "UTF-8"
    server.get_connection = Mock(return_value=mock_conn)
    return server


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
    return ctx


@pytest.fixture
def base_config(tmp_path):
    return ExecuteConfig(
        query_name="Q1",
        server_id="SRV1",
        cmd_template="SELECT %(p)s",
        cmd_parameters={"p": 1},
        extract_file=Path(),
        extract_encoding="utf-8-sig",
        params_metadata={},
    )


# ================================================================================
# QueryExecute Tests
# ================================================================================


@pytest.mark.unit
def test_query_execute_get_server_found(exec_app_ctx, exec_user_ctx, mock_server):
    qe = QueryExecute(exec_app_ctx, exec_user_ctx)

    result = qe.get_server("SRV1")

    assert result is mock_server


@pytest.mark.unit
def test_query_execute_get_server_not_found(exec_app_ctx, exec_user_ctx):
    qe = QueryExecute(exec_app_ctx, exec_user_ctx)

    result = qe.get_server("UNKNOWN")

    assert result is None


@pytest.mark.unit
def test_load_infos_raises_if_extract_file_exists(exec_app_ctx, exec_user_ctx, base_config, tmp_path):
    qe = QueryExecute(exec_app_ctx, exec_user_ctx)
    existing = tmp_path / "existing.csv"
    existing.write_text("data")
    config = replace(base_config, extract_file=existing)

    with pytest.raises(FileExistsError):
        qe.load_infos(config)


@pytest.mark.unit
def test_load_infos_returns_false_if_server_not_found(exec_app_ctx, exec_user_ctx, base_config):
    qe = QueryExecute(exec_app_ctx, exec_user_ctx)
    config = replace(base_config, server_id="UNKNOWN")

    assert qe.load_infos(config) is False


@pytest.mark.unit
def test_load_infos_returns_false_if_no_command(exec_app_ctx, exec_user_ctx, base_config):
    qe = QueryExecute(exec_app_ctx, exec_user_ctx)
    config = replace(base_config, cmd_template="")

    assert qe.load_infos(config) is False


@pytest.mark.unit
def test_load_infos_succeeds(exec_app_ctx, exec_user_ctx, base_config):
    qe = QueryExecute(exec_app_ctx, exec_user_ctx)

    assert qe.load_infos(base_config) is True
    assert qe.config is base_config
    assert qe.server is not None


@pytest.mark.unit
def test_execute_returns_error_if_load_infos_fails(exec_app_ctx, exec_user_ctx, base_config):
    qe = QueryExecute(exec_app_ctx, exec_user_ctx)
    bad_config = replace(base_config, server_id="UNKNOWN")

    result = qe.execute(bad_config)

    assert result == (-1, None)


@pytest.mark.unit
def test_execute_returns_error_if_stop_requested(exec_app_ctx, exec_user_ctx, base_config):
    qe = QueryExecute(exec_app_ctx, exec_user_ctx)
    qe.stop_requested.set()

    result = qe.execute(base_config)

    assert result == (-1, None)


@pytest.mark.unit
def test_execute_handles_programming_error(exec_app_ctx, exec_user_ctx, base_config, mock_cursor):
    import pymssql

    qe = QueryExecute(exec_app_ctx, exec_user_ctx)
    mock_cursor.execute = Mock(side_effect=pymssql._pymssql.ProgrammingError("bad sql"))

    result = qe.execute(base_config)

    assert result == (-1, None)


@pytest.mark.unit
def test_execute_handles_unexpected_error(exec_app_ctx, exec_user_ctx, base_config, mock_cursor):
    qe = QueryExecute(exec_app_ctx, exec_user_ctx)
    mock_cursor.execute = Mock(side_effect=ValueError("boom"))

    result = qe.execute(base_config)

    assert result == (-1, None)


@pytest.mark.unit
def test_execute_returns_list_when_no_extract_file(exec_app_ctx, exec_user_ctx, base_config, mock_cursor):
    qe = QueryExecute(exec_app_ctx, exec_user_ctx)
    mock_cursor.__iter__ = Mock(return_value=iter([("a", 1), ("b", 2)]))

    rows_count, output = qe.execute(base_config)

    assert rows_count == 2
    assert isinstance(output, list)
    assert output[0] == ["col1", "col2"]
    assert len(output) == 3  # header + 2 rows


@pytest.mark.unit
def test_execute_writes_to_file_and_logs(exec_app_ctx, exec_user_ctx, base_config, mock_cursor, tmp_path):
    qe = QueryExecute(exec_app_ctx, exec_user_ctx)
    mock_cursor.__iter__ = Mock(return_value=iter([("a", 1)]))

    extract_file = tmp_path / "out.csv"
    config = replace(base_config, extract_file=extract_file)

    rows_count, output_path = qe.execute(config)

    assert rows_count == 1
    assert output_path == extract_file
    assert extract_file.exists()
    assert exec_user_ctx.user_logs.insert_exec.called


@pytest.mark.unit
def test_execute_removes_file_on_permission_error(exec_app_ctx, exec_user_ctx, base_config, mock_cursor, tmp_path):
    qe = QueryExecute(exec_app_ctx, exec_user_ctx)
    mock_cursor.__iter__ = Mock(return_value=iter([("a", 1)]))

    extract_file = tmp_path / "out.csv"
    config = replace(base_config, extract_file=extract_file)

    with patch.object(qe, "_extract_to_file", side_effect=PermissionError()):
        result = qe.execute(config)

    assert result == (-1, None)
    assert not extract_file.exists()


@pytest.mark.unit
def test_execute_handles_file_not_found_error(exec_app_ctx, exec_user_ctx, base_config, tmp_path):
    qe = QueryExecute(exec_app_ctx, exec_user_ctx)
    extract_file = tmp_path / "sub" / "out.csv"
    config = replace(base_config, extract_file=extract_file)

    with patch.object(qe, "_extract_to_file", side_effect=FileNotFoundError("[Errno 2] No such file or directory")):
        result = qe.execute(config)

    assert result == (-1, None)


@pytest.mark.unit
def test_execute_end_writes_user_log_and_central_log_if_enabled(exec_app_ctx, exec_user_ctx, base_config):
    from datetime import datetime
    import queue as queue_module

    qe = QueryExecute(exec_app_ctx, exec_user_ctx)
    qe.load_infos(base_config)
    qe.queue = queue_module.Queue()  # type: ignore  # avoid multiprocessing.Queue's async pickling for this in-process check
    exec_app_ctx.settings.logs_are_on = True

    start = datetime(2026, 1, 1, 10, 0, 0)
    end = datetime(2026, 1, 1, 10, 0, 5)
    qe._execute_end(start, end, 5)

    exec_user_ctx.user_logs.insert_exec.assert_called_once()
    # central_log message queued since logs_are_on is True
    messages = []
    while not qe.queue.empty():
        messages.append(qe.queue.get())
    assert any(m[0] == "central_log" for m in messages)


@pytest.mark.unit
def test_execute_end_skips_central_log_if_disabled(exec_app_ctx, exec_user_ctx, base_config):
    from datetime import datetime
    import queue as queue_module

    qe = QueryExecute(exec_app_ctx, exec_user_ctx)
    qe.load_infos(base_config)
    qe.queue = queue_module.Queue()  # type: ignore
    exec_app_ctx.settings.logs_are_on = False

    qe._execute_end(datetime(2026, 1, 1), datetime(2026, 1, 1, 0, 0, 1), 1)

    messages = []
    while not qe.queue.empty():
        messages.append(qe.queue.get())
    assert not any(m[0] == "central_log" for m in messages)


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
    qe.config = ExecuteConfig()  # extract_file defaults to Path()

    with pytest.raises(ValueError, match="not possible"):
        qe._extract_to_file(mock_cursor, "utf-8")


@pytest.mark.unit
def test_extract_to_file_falls_back_on_invalid_encoding(exec_app_ctx, exec_user_ctx, base_config, mock_cursor, tmp_path):
    qe = QueryExecute(exec_app_ctx, exec_user_ctx)
    qe.server = exec_app_ctx.servers.servers_dict["SRV1"]
    extract_file = tmp_path / "out.csv"
    qe.config = replace(base_config, extract_file=extract_file)
    mock_cursor.__iter__ = Mock(return_value=iter([("a", 1)]))

    rows_count, path = qe._extract_to_file(mock_cursor, "not-a-real-encoding")

    assert rows_count == 1
    assert path == extract_file


@pytest.mark.unit
def test_extract_to_file_removes_empty_file(exec_app_ctx, exec_user_ctx, base_config, mock_cursor, tmp_path):
    qe = QueryExecute(exec_app_ctx, exec_user_ctx)
    qe.server = exec_app_ctx.servers.servers_dict["SRV1"]
    extract_file = tmp_path / "out.csv"
    qe.config = replace(base_config, extract_file=extract_file)
    mock_cursor.__iter__ = Mock(return_value=iter([]))

    rows_count, path = qe._extract_to_file(mock_cursor, "utf-8-sig")

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

    rows_count, path = qe._extract_to_file(mock_cursor, "utf-8-sig")

    assert rows_count == 0
    assert path is None


@pytest.mark.unit
def test_sql_record_to_text_converts_values(exec_app_ctx, exec_user_ctx):
    qe = QueryExecute(exec_app_ctx, exec_user_ctx)
    qe.server = exec_app_ctx.servers.servers_dict["SRV1"]

    result = qe._sql_record_to_text([1, "hello", None])

    # from_result() renders everything as display strings
    assert result == ["1", "hello", ""]


@pytest.mark.unit
def test_sql_record_to_text_raises_without_server(exec_app_ctx, exec_user_ctx):
    qe = QueryExecute(exec_app_ctx, exec_user_ctx)

    with pytest.raises(RuntimeError, match="Server must be initialized"):
        qe._sql_record_to_text([1])


@pytest.mark.unit
def test_sql_record_to_text_handles_decode_error(exec_app_ctx, exec_user_ctx):
    qe = QueryExecute(exec_app_ctx, exec_user_ctx)
    server = Mock()
    server.charset = "ascii"  # too narrow, forces encode/decode fallback
    qe.server = server

    result = qe._sql_record_to_text(["café"])

    assert result == ["café"]
