import pymssql
import psycopg2
import psycopg2.extensions

from Pytre_Core.about import APP_NAME, full_version
from Pytre_Core.servers import ServerType, Server
from Pytre_Core.sql_query.query_drivers_mssql import connect_mssql, error_msg as mssql_error_msg

APP_ID = f"{APP_NAME}_{full_version()}"


Connection = pymssql.Connection | psycopg2.extensions.connection
LocalCursor = pymssql.Cursor | psycopg2.extensions.cursor


class DriverErrors:
    ProgrammingError = (pymssql._pymssql.ProgrammingError, psycopg2.ProgrammingError)
    Error = (pymssql._pymssql.Error, psycopg2.Error)


class ServerConnectionError(Exception):
    """Connection error to server"""


def get_connection(server: Server) -> Connection:
    if server.type in (ServerType.mssql.name, ServerType.fastmssql.name):
        return _conn_mssql(server)
    elif server.type == ServerType.postgre.name:
        return _conn_postgre(server)
    else:
        raise ValueError("Unknown server type, getting a connection is not possible")


def _conn_mssql(server: Server) -> pymssql.Connection:
    try:
        return connect_mssql(server, APP_ID)
    except pymssql._pymssql.OperationalError as err:
        raise ServerConnectionError(mssql_error_msg(err)) from err


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
            application_name=APP_ID,
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
