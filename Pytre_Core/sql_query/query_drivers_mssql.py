"""SQL Server connection through pymssql, shared by the client (query_drivers_client) and the server
(query_drivers_server) - depends on pymssql only."""

import pymssql

from Pytre_Core.servers import Server


# Standard recommended set for connection properties plus an explicit DATEFORMAT
CONN_PROPERTIES = """
        SET ARITHABORT ON;
        SET CONCAT_NULL_YIELDS_NULL ON;
        SET ANSI_NULLS ON;
        SET ANSI_NULL_DFLT_ON ON;
        SET ANSI_PADDING ON;
        SET ANSI_WARNINGS ON;
        SET CURSOR_CLOSE_ON_COMMIT ON;
        SET QUOTED_IDENTIFIER ON;
        SET TEXTSIZE 2147483647;
        SET DATEFORMAT ymd;
    """


def connect_mssql(server: Server, app_id: str) -> pymssql.Connection:
    """Opens a read-only connection to `server`, named `app_id` on the SQL Server side (sys.dm_exec_sessions)."""

    return pymssql.connect(
        server=server.server,
        host=server.host,
        user=server.user,
        password=server.password,
        database=server.database,
        timeout=server.timeout,
        login_timeout=server.login_timeout,
        charset=server.charset,
        as_dict=False,
        port=server.port,
        read_only=True,
        appname=app_id,
        conn_properties=CONN_PROPERTIES,
    )


def error_msg(error: pymssql.Error) -> str:
    """Text of a pymssql error. Errors from SQL Server carry their number and their message as bytes,
    as one (number, message) argument from connect(), as two arguments from a cursor."""
    details = error.args[0] if len(error.args) == 1 else error.args
    if isinstance(details, tuple) and len(details) == 2 and isinstance(details[1], bytes):
        return details[1].decode("utf-8", errors="replace")
    return str(error)
