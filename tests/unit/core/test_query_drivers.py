"""
Tests for Pytre_Core/sql_query/query_drivers.py
"""

from unittest.mock import Mock, patch

import pymssql
import psycopg2
import pytest

from Pytre_Core.servers import Server, ServerType
from Pytre_Core.sql_query.query_drivers import APP_ID, ServerConnectionError, get_connection


# ================================================================================
# get_connection / _conn_mssql / _conn_postgre
# ================================================================================


@pytest.mark.unit
def test_get_connection_mssql_calls_pymssql_connect():
    """get_connection should build an mssql connection for type 'mssql'"""
    server = Server(Mock())
    server.type = ServerType.mssql.name
    server.server = "SRV"
    server.host = "localhost"
    server.user = "sa"
    server.password = "pwd"
    server.database = "db"

    fake_conn = Mock()
    with patch("Pytre_Core.sql_query.query_drivers.pymssql.connect", return_value=fake_conn) as mock_connect:
        result = get_connection(server)

    assert result is fake_conn
    assert mock_connect.call_args.kwargs["user"] == "sa"
    assert mock_connect.call_args.kwargs["database"] == "db"
    assert mock_connect.call_args.kwargs["read_only"] is True


@pytest.mark.unit
def test_get_connection_fastmssql_falls_back_to_pymssql():
    """fastmssql is a server-only type: the client connects to such a server with pymssql."""
    server = Server(Mock())
    server.type = ServerType.fastmssql.name

    fake_conn = Mock()
    with patch("Pytre_Core.sql_query.query_drivers.pymssql.connect", return_value=fake_conn):
        assert get_connection(server) is fake_conn


@pytest.mark.unit
def test_conn_mssql_names_the_client_application():
    server = Server(Mock())
    server.type = ServerType.mssql.name

    with patch("Pytre_Core.sql_query.query_drivers.pymssql.connect", return_value=Mock()) as mock_connect:
        get_connection(server)

    assert mock_connect.call_args.kwargs["appname"] == APP_ID


@pytest.mark.unit
def test_conn_mssql_forces_ymd_dateformat():
    """_conn_mssql should force SET DATEFORMAT ymd - Pytre always sends dates as YYYY-MM-DD,
    and the legacy `datetime` type's string parsing otherwise depends on the SQL login's
    default language, which pymssql itself doesn't control either (see SqlTypeConverter)."""
    server = Server(Mock())
    server.type = ServerType.mssql.name

    fake_conn = Mock()
    with patch("Pytre_Core.sql_query.query_drivers.pymssql.connect", return_value=fake_conn) as mock_connect:
        get_connection(server)

    conn_properties = mock_connect.call_args.kwargs["conn_properties"]
    assert "SET DATEFORMAT ymd;" in conn_properties


@pytest.mark.unit
def test_get_connection_postgre_calls_psycopg2_connect():
    """get_connection should build a postgres connection for type 'postgre'"""
    server = Server(Mock())
    server.type = ServerType.postgre.name
    server.server = "SRV"
    server.host = "localhost"
    server.user = "postgres"
    server.password = "pwd"
    server.database = "db"
    server.timeout = 30

    fake_cursor = Mock()
    fake_conn = Mock()
    fake_conn.cursor = Mock(return_value=fake_cursor)

    with patch("Pytre_Core.sql_query.query_drivers.psycopg2.connect", return_value=fake_conn) as mock_connect:
        result = get_connection(server)

    assert result is fake_conn
    assert mock_connect.call_args.kwargs["user"] == "postgres"
    assert mock_connect.call_args.kwargs["dbname"] == "db"
    # statement_timeout is set in milliseconds
    fake_cursor.execute.assert_any_call("SET statement_timeout = %s", (30000,))
    fake_cursor.close.assert_called_once()


@pytest.mark.unit
def test_get_connection_unknown_type_raises_error():
    """get_connection should raise ValueError for an unknown server type"""
    server = Server(Mock())
    server.type = "unknown_type"

    with pytest.raises(ValueError, match="Unknown server type"):
        get_connection(server)


# ================================================================================
# Connection error handling
# ================================================================================


@pytest.mark.unit
def test_conn_mssql_raises_server_connection_error():
    """_conn_mssql should convert pymssql.OperationalError into ServerConnectionError"""
    server = Server(Mock())
    server.type = ServerType.mssql.name

    with patch(
        "Pytre_Core.sql_query.query_drivers.pymssql.connect",
        side_effect=pymssql._pymssql.OperationalError((1, b"connection failed")),
    ):
        with pytest.raises(ServerConnectionError, match="connection failed"):
            get_connection(server)


@pytest.mark.unit
def test_conn_postgre_raises_server_connection_error():
    """_conn_postgre should convert psycopg2.OperationalError into ServerConnectionError"""
    server = Server(Mock())
    server.type = ServerType.postgre.name

    with patch("Pytre_Core.sql_query.query_drivers.psycopg2.connect", side_effect=psycopg2.OperationalError("boom")):
        with pytest.raises(ServerConnectionError, match="boom"):
            get_connection(server)


@pytest.mark.unit
def test_conn_postgre_converts_unicode_decode_error():
    """_conn_postgre should convert UnicodeDecodeError into a ServerConnectionError"""
    server = Server(Mock())
    server.type = ServerType.postgre.name

    unicode_err = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
    fake_cursor = Mock()
    fake_cursor.execute = Mock(side_effect=unicode_err)
    fake_conn = Mock()
    fake_conn.cursor = Mock(return_value=fake_cursor)

    with patch("Pytre_Core.sql_query.query_drivers.psycopg2.connect", return_value=fake_conn):
        with pytest.raises(ServerConnectionError):
            get_connection(server)
