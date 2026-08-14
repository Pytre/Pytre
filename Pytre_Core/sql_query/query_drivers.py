import pymssql
import psycopg2
import psycopg2.extensions

from Pytre_Core.about import APP_NAME, APP_VERSION
from Pytre_Core.servers import ServerType, Server


Connection = pymssql.Connection | psycopg2.extensions.connection
LocalCursor = pymssql.Cursor | psycopg2.extensions.cursor


class DriverErrors:
    ProgrammingError = (pymssql._pymssql.ProgrammingError, psycopg2.ProgrammingError)
    Error = (pymssql._pymssql.Error, psycopg2.Error)


class ServerConnectionError(Exception):
    """Connection error to server"""


def get_connection(server: Server) -> Connection:
    if server.type == ServerType.mssql.name:
        return _conn_mssql(server)
    elif server.type == ServerType.postgre.name:
        return _conn_postgre(server)
    else:
        raise ValueError("Unknown server type, getting a connection is not possible")


def _conn_mssql(server: Server) -> pymssql.Connection:
    # Standard recommended set for connection properties plus an explicit DATEFORMAT
    conn_properties = """
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

    try:
        conn: pymssql.Connection = pymssql.connect(
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
            appname=f"{APP_NAME}_{APP_VERSION}",
            conn_properties=conn_properties,
        )
    except pymssql._pymssql.OperationalError as err:
        err_msg = err.args[0][1].decode("utf-8", errors="replace")
        raise ServerConnectionError(err_msg) from err

    return conn


def _conn_postgre(server: Server) -> psycopg2.extensions.connection:
    try:
        conn: psycopg2.extensions.connection = psycopg2.connect(
            hostaddr=server.server,
            host=server.host,
            user=server.user,
            password=server.password,
            dbname=server.database,
            connect_timeout=server.login_timeout,
            client_encoding=server.charset,
            port=server.port,
            application_name=f"{APP_NAME}_{APP_VERSION}",
        )
        cursor = conn.cursor()
        cursor.execute("SET statement_timeout = %s", (server.timeout * 1000,))  # needs to be milliseconds
        cursor.execute("SET default_transaction_read_only = on")
        cursor.close()
    except psycopg2.OperationalError as err:
        err_msg = "".join(err.args[0])
        raise ServerConnectionError(err_msg) from err
    except UnicodeDecodeError as err:
        err_msg = err.args[1].decode(err.args[0], errors="replace")
        raise ServerConnectionError(err_msg) from err

    return conn
