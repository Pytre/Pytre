"""
Tests for Pytre_Core/sql_query/query_driver_mssql.py
"""

from unittest.mock import Mock, patch

import pymssql
import pytest

from Pytre_Core.servers import Server
from Pytre_Core.sql_query.query_driver_mssql import CONN_PROPERTIES, connect_mssql, error_msg


# ================================================================================
# connect_mssql
# ================================================================================


@pytest.mark.unit
def test_connect_mssql_forwards_the_server_parameters():
    server = Server(Mock())
    server.server, server.host, server.port = "SRV", "localhost", "1444"
    server.user, server.password, server.database = "sa", "pwd", "db"
    server.timeout, server.login_timeout, server.charset = 30, 5, "UTF-8"

    fake_conn = Mock()
    with patch("Pytre_Core.sql_query.query_driver_mssql.pymssql.connect", return_value=fake_conn) as mock_connect:
        assert connect_mssql(server, "Some_App") is fake_conn

    assert mock_connect.call_args.kwargs == {
        "server": "SRV",
        "host": "localhost",
        "user": "sa",
        "password": "pwd",
        "database": "db",
        "timeout": 30,
        "login_timeout": 5,
        "charset": "UTF-8",
        "as_dict": False,
        "port": "1444",
        "read_only": True,
        "appname": "Some_App",
        "conn_properties": CONN_PROPERTIES,
    }


@pytest.mark.unit
def test_conn_properties_force_ymd_dateformat():
    """Pytre always sends dates as YYYY-MM-DD, whatever the SQL login's default language."""
    assert "SET DATEFORMAT ymd;" in CONN_PROPERTIES


# ================================================================================
# error_msg
# ================================================================================


@pytest.mark.unit
@pytest.mark.parametrize(
    "error",
    [
        pymssql.OperationalError((208, b"Invalid object name 'caf\xc3\xa9'.")),  # from connect(): one tuple
        pymssql.ProgrammingError(208, b"Invalid object name 'caf\xc3\xa9'."),  # from a cursor: two arguments
    ],
)
def test_error_msg_decodes_sql_server_messages(error):
    assert error_msg(error) == "Invalid object name 'café'."


@pytest.mark.unit
def test_error_msg_replaces_undecodable_bytes():
    assert error_msg(pymssql.OperationalError((1, b"bad \xff byte"))) == "bad � byte"


@pytest.mark.unit
@pytest.mark.parametrize(
    "error, expected",
    [
        (pymssql.OperationalError("Statement not executed"), "Statement not executed"),
        (pymssql.InterfaceError("Connection is closed."), "Connection is closed."),
        (pymssql.OperationalError((1, "not bytes")), "(1, 'not bytes')"),
        (pymssql.OperationalError(), ""),
    ],
)
def test_error_msg_falls_back_to_the_error_text(error, expected):
    assert error_msg(error) == expected
