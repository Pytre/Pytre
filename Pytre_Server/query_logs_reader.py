"""
Read-only access to the server's query execution logs (ServerDriven's database), for the admin webui.

Each call opens its own short connection and closes it before returning, so a read never holds a lock
while a page is rendered: the log writer thread waits on its busy_timeout during these short reads.
"""

import sqlite3
from contextlib import closing
from datetime import datetime
from enum import StrEnum


READ_TIMEOUT_SECS = 2


class QueryLogsUnavailable(Exception):
    """The logs database can't be read: missing, not migrated yet, or locked for too long."""


class QueryLogsSource(StrEnum):
    QUERIES_EXEC = "QUERIES_EXEC"
    MONTHLY_STATS = "MONTHLY_STATS"
    RUNS_PER_USER = "RUNS_PER_USER"
    TOP_QUERIES = "TOP_QUERIES"

    @property
    def label(self) -> str:
        """Name shown in the admin webui."""
        return {
            QueryLogsSource.QUERIES_EXEC: "Query executions",
            QueryLogsSource.MONTHLY_STATS: "Monthly stats",
            QueryLogsSource.RUNS_PER_USER: "Runs per user",
            QueryLogsSource.TOP_QUERIES: "Top queries",
        }[self]


def _fetch(db_uri: str, sql: str, params: tuple = ()) -> tuple[list[str], list[tuple]]:
    try:
        with closing(sqlite3.connect(f"{db_uri}?mode=ro", uri=True, timeout=READ_TIMEOUT_SECS)) as conn:
            cursor = conn.execute(sql, params)
            columns = [description[0] for description in cursor.description]
            return columns, cursor.fetchall()
    except sqlite3.Error as e:
        raise QueryLogsUnavailable(str(e)) from e


def start_to_timestamp(start: str | None) -> int | None:
    """Epoch seconds of a logged START, for display in the browser's own time zone."""
    return int(datetime.fromisoformat(start).timestamp()) if start else None


def last_run_by_user(db_uri: str) -> dict[str, str]:
    """USER_ID -> START of the user's most recent execution."""
    _, rows = _fetch(db_uri, "SELECT USER_ID, MAX(START) FROM QUERIES_EXEC GROUP BY USER_ID;")
    return {user_id: last_run for user_id, last_run in rows}


def usage_by_query(db_uri: str) -> dict[str, tuple[int, str]]:
    """Query id -> (number of executions, START of the most recent one)."""
    _, rows = _fetch(db_uri, "SELECT QUERY, NB, LAST_RUN FROM TOP_QUERIES;")
    return {query: (nb, last_run) for query, nb, last_run in rows}


def read_source(db_uri: str, source: QueryLogsSource, limit: int, offset: int = 0) -> tuple[list[str], list[tuple]]:
    """Columns and rows of `source`, the raw executions being sorted newest first."""

    source = QueryLogsSource(source)  # convert explicitly to an enum, source is StrEnum and ends up in the SQL
    order_by = "ORDER BY START DESC" if source is QueryLogsSource.QUERIES_EXEC else ""
    return _fetch(db_uri, f"SELECT * FROM {source} {order_by} LIMIT ? OFFSET ?", (limit, offset))
