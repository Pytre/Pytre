"""
Tests for Pytre_Server/query_logs_reader.py, against real temporary databases built by ServerDriven
"""

import contextlib
import sqlite3
import time
from datetime import datetime, timezone

import pytest

from Pytre_Core.logs_central import ServerDriven
from Pytre_Server import query_logs_reader
from Pytre_Server.query_logs_reader import (
    QueryLogsSource,
    QueryLogsUnavailable,
    last_run_by_user,
    read_source,
    start_to_timestamp,
    usage_by_query,
)


ROWS = [
    ("srv", "DOM\\alice", "Alice", "Q1", "2026-10-03T09:00:00+00:00", 2, 10, None),
    ("srv", "DOM\\alice", "Alice", "Q2", "2026-10-05T09:00:00+00:00", 4, 20, None),
    ("srv", "DOM\\bob", "", "Q1", "2026-11-01T09:00:00+00:00", 1, 5, None),
]


@pytest.fixture
def logs(tmp_path) -> ServerDriven:
    server_logs = ServerDriven(logs_folder=tmp_path)
    assert server_logs.create_db()
    assert server_logs.insert_into_central_db(ROWS)
    return server_logs


# ================================================================================
# Reads
# ================================================================================


@pytest.mark.unit
def test_last_run_by_user(logs):
    assert last_run_by_user(logs.db_uri) == {
        "DOM\\alice": "2026-10-05T09:00:00+00:00",
        "DOM\\bob": "2026-11-01T09:00:00+00:00",
    }


@pytest.mark.unit
def test_usage_by_query(logs):
    assert usage_by_query(logs.db_uri) == {
        "Q1": (2, "2026-11-01T09:00:00+00:00"),
        "Q2": (1, "2026-10-05T09:00:00+00:00"),
    }


@pytest.mark.unit
@pytest.mark.parametrize("source", list(QueryLogsSource))
def test_read_source_reads_every_source(logs, source):
    columns, rows = read_source(logs.db_uri, source, limit=10)

    assert columns
    assert rows
    assert all(len(row) == len(columns) for row in rows)


@pytest.mark.unit
def test_read_source_lists_executions_newest_first(logs):
    columns, rows = read_source(logs.db_uri, QueryLogsSource.QUERIES_EXEC, limit=10)

    starts = [row[columns.index("START")] for row in rows]
    assert starts == sorted(starts, reverse=True)


@pytest.mark.unit
def test_read_source_paginates(logs):
    _, first_page = read_source(logs.db_uri, QueryLogsSource.QUERIES_EXEC, limit=2)
    _, second_page = read_source(logs.db_uri, QueryLogsSource.QUERIES_EXEC, limit=2, offset=2)

    assert len(first_page) == 2
    assert len(second_page) == 1
    assert second_page[0] not in first_page


@pytest.mark.unit
def test_read_source_accepts_a_plain_string_and_still_sorts(logs):
    """A plain str would make `source is QueryLogsSource.QUERIES_EXEC` false, losing the sort"""
    columns, rows = read_source(logs.db_uri, "QUERIES_EXEC", limit=10)  # type: ignore

    starts = [row[columns.index("START")] for row in rows]
    assert starts == sorted(starts, reverse=True)


@pytest.mark.unit
def test_read_source_never_puts_an_unknown_source_in_the_sql(logs):
    with pytest.raises(ValueError):
        read_source(logs.db_uri, "QUERIES_EXEC; DROP TABLE QUERIES_EXEC", limit=10)  # type: ignore

    assert len(read_source(logs.db_uri, QueryLogsSource.QUERIES_EXEC, limit=10)[1]) == len(ROWS)


@pytest.mark.unit
def test_sources_match_the_database_views(logs):
    """The enum value is what ends up in the SQL: it must be the view name, not just the member name"""
    with contextlib.closing(sqlite3.connect(logs.db_path)) as conn:
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")}

    assert {source.value for source in QueryLogsSource} <= names


@pytest.mark.unit
def test_every_source_has_a_label():
    assert all(source.label for source in QueryLogsSource)


# ================================================================================
# Unavailable database
# ================================================================================


@pytest.mark.unit
def test_missing_database_is_reported_as_unavailable(tmp_path):
    missing = ServerDriven(logs_folder=tmp_path / "nowhere")

    with pytest.raises(QueryLogsUnavailable):
        last_run_by_user(missing.db_uri)


@pytest.mark.unit
def test_reading_never_creates_the_database(tmp_path):
    missing = ServerDriven(logs_folder=tmp_path)

    with pytest.raises(QueryLogsUnavailable):
        usage_by_query(missing.db_uri)

    assert not missing.db_path.exists()


@pytest.mark.unit
def test_database_without_the_views_is_reported_as_unavailable(tmp_path):
    """A database not migrated yet has the table but none of the stats views"""
    logs = ServerDriven(logs_folder=tmp_path)
    with contextlib.closing(sqlite3.connect(logs.db_path)) as conn:
        conn.execute("CREATE TABLE QUERIES_EXEC (QUERY TEXT, START TEXT)")
        conn.commit()

    with pytest.raises(QueryLogsUnavailable):
        usage_by_query(logs.db_uri)


@pytest.mark.unit
def test_locked_database_is_reported_as_unavailable_after_the_read_timeout(logs, monkeypatch):
    monkeypatch.setattr(query_logs_reader, "READ_TIMEOUT_SECS", 0.2)

    with contextlib.closing(sqlite3.connect(logs.db_path)) as writer:
        writer.execute("BEGIN EXCLUSIVE")
        start = time.perf_counter()
        with pytest.raises(QueryLogsUnavailable, match="locked"):
            last_run_by_user(logs.db_uri)
        elapsed = time.perf_counter() - start
        writer.rollback()

    assert elapsed < 5
    assert last_run_by_user(logs.db_uri)  # readable again once the write is over


# ================================================================================
# start_to_timestamp
# ================================================================================


@pytest.mark.unit
def test_start_to_timestamp_with_a_time_zone():
    start = datetime(2026, 11, 7, 9, 0, tzinfo=timezone.utc)

    assert start_to_timestamp(start.isoformat()) == int(start.timestamp())


@pytest.mark.unit
def test_start_to_timestamp_reads_a_naive_start_as_server_local_time():
    """Executions logged before the time zone was added are naive server local times"""
    start = datetime(2026, 11, 7, 10, 0)

    assert start_to_timestamp(start.isoformat()) == int(start.astimezone().timestamp())


@pytest.mark.unit
@pytest.mark.parametrize("start", [None, ""])
def test_start_to_timestamp_without_start(start):
    assert start_to_timestamp(start) is None
