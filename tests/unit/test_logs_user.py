"""
Tests for Pytre/logs_user.py

Basic tests for user logging infrastructure.
"""

import pytest
import sqlite3
import contextlib
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import patch

from Pytre_Core.logs_user import UserLogs, LogRecord, LogStats, LATEST_VERSION


# ================================================================================
# Fixtures
# ================================================================================


@pytest.fixture
def temp_user_db(tmp_path):
    """Create temporary user database path"""
    user_db = tmp_path / "Pytre_Logs.db"
    return user_db


@pytest.fixture
def user_db(temp_user_db):
    """Create UserLogs instance with temp database"""
    # Reset singleton

    db = UserLogs(user_db=temp_user_db, log_max=2500)
    return db


# ================================================================================
# LogRecord Tests
# ================================================================================


@pytest.mark.unit
def test_log_record_can_be_created():
    """LogRecord should be instantiable"""
    record = LogRecord()

    # Just verify it creates without error and can set attributes
    record.id = 1
    record.server = "TEST_SERVER"
    record.query = "TEST_QUERY"

    assert record.id == 1
    assert record.server == "TEST_SERVER"
    assert record.query == "TEST_QUERY"


@pytest.mark.unit
def test_log_record_str():
    """LogRecord __str__ should match __repr__"""
    record = LogRecord()
    record.server = "TEST_SERVER"
    record.query = "TEST_QUERY"
    record.start = datetime.now()
    record.duration = 100
    record.nb_rows = 50
    record.exported = 0

    assert str(record) == repr(record)


@pytest.mark.unit
def test_log_record_repr():
    """LogRecord should have string representation"""
    record = LogRecord()
    record.server = "TEST_SERVER"
    record.query = "TEST_QUERY"
    record.start = datetime.now()
    record.duration = 100
    record.nb_rows = 50
    record.exported = 0

    result = repr(record)

    assert isinstance(result, str)
    assert "TEST_SERVER" in result
    assert "TEST_QUERY" in result


# ================================================================================
# LogStats Tests
# ================================================================================


@pytest.mark.unit
def test_log_stats_can_be_created():
    """LogStats should be instantiable"""
    stats = LogStats()

    # Just verify it creates without error and can set attributes
    stats.server = "TEST_SERVER"
    stats.query = "TEST_QUERY"
    stats.nb_run = 10

    assert stats.server == "TEST_SERVER"
    assert stats.query == "TEST_QUERY"
    assert stats.nb_run == 10


@pytest.mark.unit
def test_log_stats_str():
    """LogStats __str__ should match __repr__"""
    stats = LogStats()
    stats.server = "TEST_SERVER"
    stats.query = "TEST_QUERY"
    stats.nb_run = 10
    stats.min_run = 5
    stats.max_run = 20
    stats.last_run = datetime.now()

    assert str(stats) == repr(stats)


@pytest.mark.unit
def test_log_stats_repr():
    """LogStats should have string representation"""
    stats = LogStats()
    stats.server = "TEST_SERVER"
    stats.query = "TEST_QUERY"
    stats.nb_run = 10
    stats.min_run = 5
    stats.max_run = 20
    stats.last_run = datetime.now()

    result = repr(stats)

    assert isinstance(result, str)
    assert "TEST_SERVER" in result
    assert "TEST_QUERY" in result


# ================================================================================
# UserLogs Initialization Tests
# ================================================================================


@pytest.mark.unit
def test_user_db_initialization(temp_user_db):
    """UserLogs should initialize with database path"""

    db = UserLogs(user_db=temp_user_db, log_max=2500)

    assert db.db_path == temp_user_db
    assert db.log_max == 2500
    assert db.version == -1
    assert db.latest_version == LATEST_VERSION
    assert db.update_already_run is False


@pytest.mark.unit
def test_user_db_instances_are_independent():
    """UserLogs no longer uses the singleton pattern (removed, see AppContext refactor)"""

    db1 = UserLogs()
    db2 = UserLogs()

    assert db1 is not db2


# ================================================================================
# Database Creation Tests
# ================================================================================


@pytest.mark.unit
def test_create_db_creates_database(user_db):
    """create_db should create SQLite database"""
    result = user_db.create_db()

    # Verify database exists
    assert user_db.db_path.exists()
    assert result is True


@pytest.mark.unit
def test_create_db_creates_tables(user_db):
    """create_db should create required tables"""
    user_db.create_db()

    # Check tables exist
    with contextlib.closing(sqlite3.connect(user_db.db_path)) as conn:
        cursor = conn.cursor()

        # Check QUERIES_EXEC table
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='QUERIES_EXEC'")
        result = cursor.fetchone()

        assert result is not None
        assert result[0] == "QUERIES_EXEC"


@pytest.mark.unit
def test_create_db_creates_indexes(user_db):
    """create_db should create indexes"""
    user_db.create_db()

    # Check indexes exist
    with contextlib.closing(sqlite3.connect(user_db.db_path)) as conn:
        cursor = conn.cursor()

        # Check for indexes
        cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'IDX_%'")
        indexes = cursor.fetchall()

        # Should have at least 3 indexes
        assert len(indexes) >= 3


@pytest.mark.unit
def test_create_db_sets_user_version(user_db):
    """create_db should set PRAGMA user_version"""
    user_db.create_db()

    # Check user_version
    with contextlib.closing(sqlite3.connect(user_db.db_path)) as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA user_version;")
        version = cursor.fetchone()[0]

        assert version == LATEST_VERSION


# ================================================================================
# Database Check Tests
# ================================================================================


@pytest.mark.unit
def test_check_db_creates_if_not_exists(user_db):
    """check_db should create database if it doesn't exist"""
    # Ensure doesn't exist
    assert not user_db.db_path.exists()

    # Check (with create=True)
    result = user_db.check_db(create=True)

    # Should create
    assert user_db.db_path.exists()
    assert result is True


@pytest.mark.unit
def test_check_db_does_not_create_if_disabled(user_db):
    """check_db with create=False should not create database"""
    # Ensure doesn't exist
    assert not user_db.db_path.exists()

    # Check (with create=False)
    user_db.check_db(create=False)

    # Should NOT create
    assert not user_db.db_path.exists()


@pytest.mark.unit
def test_check_db_sets_update_flag(user_db):
    """check_db should set update_already_run flag"""
    assert user_db.update_already_run is False

    user_db.check_db(create=True)

    assert user_db.update_already_run is True


# ================================================================================
# Version Management Tests
# ================================================================================


@pytest.mark.unit
def test_get_user_version_returns_version(user_db):
    """get_user_version should return database version"""
    # Create database first
    user_db.create_db()

    # Get version
    version = user_db.get_version()

    assert version == LATEST_VERSION


@pytest.mark.unit
def test_get_user_version_handles_missing_db(user_db):
    """get_user_version should handle missing database"""
    # Don't create database
    assert not user_db.db_path.exists()

    # Get version
    version = user_db.get_version()

    # Should return -1 or 0 (SQLite default for missing database)
    assert version in [-1, 0]


# ================================================================================
# Update Database Tests
# ================================================================================


@pytest.mark.unit
def test_update_db_skips_if_already_run(user_db):
    """update_db should skip if already run"""
    user_db.update_already_run = True

    # Should return early
    result = user_db.update_db()

    assert result is True


def _create_v0_user_database(db_path):
    """Create a database matching the pre-migration (version 0) user db schema"""
    with contextlib.closing(sqlite3.connect(db_path)) as conn:
        conn.execute("PRAGMA user_version = 0;")
        conn.execute(
            """
                CREATE TABLE QUERIES_EXEC (
                    QUERY            TEXT        NOT NULL,
                    START            TEXT        NOT NULL,
                    END              TEXT,
                    DURATION_SECS    INTEGER,
                    NB_ROWS          INTEGER,
                    PARAMETERS       TEXT,
                    FILE             TEXT,
                    EXPORTED         INTEGER     NOT NULL DEFAULT 0
                );
            """
        )
        conn.commit()


@pytest.mark.unit
def test_update_db_migrates_schema(user_db):
    """update_db should run every migration step needed to reach latest_version"""
    _create_v0_user_database(user_db.db_path)
    user_db.version = 0

    result = user_db.update_db()

    assert result is True
    assert user_db.version == LATEST_VERSION

    with contextlib.closing(sqlite3.connect(user_db.db_path)) as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(QUERIES_EXEC)")
        columns = [row[1] for row in cursor.fetchall()]

    assert "SERVER_ID" in columns
    assert "END" not in columns


@pytest.mark.unit
def test_update_db_returns_false_if_version_unknown(user_db):
    """update_db should return False if user_version couldn't be determined"""
    user_db.version = -1

    assert user_db.update_db() is False


@pytest.mark.unit
def test_update_db_returns_true_if_already_latest(user_db):
    """update_db should return True without migrating if already at latest_version"""
    user_db.version = LATEST_VERSION

    assert user_db.update_db() is True


@pytest.mark.unit
def test_update_db_skips_already_applied_steps(user_db):
    """update_db should skip migration steps already applied and only run the remaining ones"""
    user_db.create_db()  # already at latest schema
    with contextlib.closing(sqlite3.connect(user_db.db_path)) as conn:
        conn.execute("PRAGMA user_version = 1;")
        conn.execute("ALTER TABLE QUERIES_EXEC ADD COLUMN END TEXT;")  # simulate a v1 db
        conn.commit()
    user_db.version = 1

    result = user_db.update_db()

    assert result is True
    assert user_db.version == LATEST_VERSION


@pytest.mark.unit
def test_update_db_aborts_if_a_migration_step_fails(user_db):
    """update_db should stop and return False if a migration step fails"""
    _create_v0_user_database(user_db.db_path)
    user_db.version = 0

    with patch.object(user_db, "update_db_0_to_1", return_value=False):
        result = user_db.update_db()

    assert result is False


# ================================================================================
# Insert Execution Tests
# ================================================================================


@pytest.mark.unit
def test_insert_exec_creates_db_if_needed(user_db):
    """insert_exec should create database if it doesn't exist"""
    # Database doesn't exist yet
    assert not user_db.db_path.exists()

    # Insert execution
    user_db.insert_exec(
        server_id="TEST_SERVER",
        query="TEST_QUERY",
        start=datetime.now(),
        end=datetime.now(),
        nb_rows=10,
    )

    # Database should now exist
    assert user_db.db_path.exists()


@pytest.mark.unit
def test_insert_exec_logs_to_database(user_db):
    """insert_exec should log execution to database"""
    user_db.create_db()

    # Insert execution
    start_time = datetime.now()
    end_time = datetime.now()

    user_db.insert_exec(
        server_id="TEST_SERVER",
        query="TEST_QUERY",
        start=start_time,
        end=end_time,
        nb_rows=100,
    )

    # Verify logged
    with contextlib.closing(sqlite3.connect(user_db.db_path)) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM QUERIES_EXEC WHERE QUERY = 'TEST_QUERY'")
        result = cursor.fetchone()

        assert result is not None


@pytest.mark.unit
def test_insert_exec_handles_optional_params(user_db):
    """insert_exec should handle optional parameters"""
    user_db.create_db()

    # Insert with minimal parameters
    user_db.insert_exec(
        server_id="TEST_SERVER",
        query="TEST_QUERY",
        start=datetime.now(),
    )

    # Verify logged
    with contextlib.closing(sqlite3.connect(user_db.db_path)) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM QUERIES_EXEC WHERE QUERY = 'TEST_QUERY'")
        result = cursor.fetchone()

        assert result is not None


# ================================================================================
# Select Methods Tests
# ================================================================================


@pytest.mark.unit
def test_get_last_files_returns_list(user_db):
    """get_last_files should return list of file paths"""
    user_db.create_db()

    # Insert some executions with files
    user_db.insert_exec(
        server_id="TEST",
        query="QUERY1",
        start=datetime.now(),
        end=datetime.now(),
        file="/path/to/file1.txt",
    )

    # Get last files
    result = user_db.get_last_files(nb_files=10)

    assert isinstance(result, list)


@pytest.mark.unit
def test_get_last_files_handles_missing_db(user_db):
    """get_last_files should handle missing database gracefully"""
    # Don't create database
    assert not user_db.db_path.exists()

    # Get last files
    result = user_db.get_last_files(nb_files=10)

    # Should return empty list
    assert result == []


# ================================================================================
# Edge Cases
# ================================================================================


@pytest.mark.unit
def test_user_db_handles_permission_error(user_db):
    """create_db should handle permission errors gracefully"""
    # Mock connect to raise PermissionError
    with patch("sqlite3.connect", side_effect=sqlite3.OperationalError("Permission denied")):
        # Should not raise error
        result = user_db.create_db()

        # Should return False
        assert result is False


# ================================================================================
# insert_exec log_max cleanup
# ================================================================================


@pytest.mark.unit
def test_insert_exec_trims_to_log_max(temp_user_db):
    """insert_exec should only keep the log_max most recent rows"""
    db = UserLogs(user_db=temp_user_db, log_max=2)
    db.create_db()

    base = datetime(2026, 1, 1)
    for i in range(4):
        db.insert_exec(server_id="S", query=f"Q{i}", start=base + timedelta(minutes=i))

    with contextlib.closing(sqlite3.connect(db.db_path)) as conn:
        count = conn.execute("SELECT COUNT(*) FROM QUERIES_EXEC").fetchone()[0]
        remaining = [r[0] for r in conn.execute("SELECT QUERY FROM QUERIES_EXEC ORDER BY START")]

    assert count == 2
    assert remaining == ["Q2", "Q3"]  # the 2 most recent


# ================================================================================
# get_stats / get_last_records / row_to_* Tests
# ================================================================================


@pytest.mark.unit
def test_get_stats_returns_empty_list_if_db_missing(user_db):
    """get_stats should return an empty list if the database doesn't exist"""
    assert not user_db.db_path.exists()

    assert user_db.get_stats() == []


@pytest.mark.unit
def test_get_stats_aggregates_by_server_and_query(user_db):
    """get_stats should group executions by server/query and compute run stats"""
    user_db.create_db()
    base = datetime(2026, 1, 1, 10, 0, 0)

    user_db.insert_exec(server_id="SRV1", query="Q1", start=base, end=base + timedelta(seconds=5))
    user_db.insert_exec(
        server_id="SRV1", query="Q1", start=base + timedelta(minutes=1), end=base + timedelta(minutes=1, seconds=15)
    )

    stats = user_db.get_stats()

    assert len(stats) == 1
    assert isinstance(stats[0], LogStats)
    assert stats[0].server == "SRV1"
    assert stats[0].query == "Q1"
    assert stats[0].nb_run == 2
    assert stats[0].min_run == 5
    assert stats[0].max_run == 15


@pytest.mark.unit
def test_get_stats_filters_by_query_name(user_db):
    """get_stats should only return stats for the requested query when given one"""
    user_db.create_db()
    base = datetime(2026, 1, 1, 10, 0, 0)

    user_db.insert_exec(server_id="SRV1", query="Q1", start=base)
    user_db.insert_exec(server_id="SRV1", query="Q2", start=base)

    stats = user_db.get_stats(query_name="Q1")

    assert len(stats) == 1
    assert stats[0].query == "Q1"


@pytest.mark.unit
def test_get_last_records_returns_empty_list_if_db_missing(user_db):
    """get_last_records should return an empty list if the database doesn't exist"""
    assert user_db.get_last_records() == []


@pytest.mark.unit
def test_get_last_records_returns_logrecords(user_db):
    """get_last_records should return LogRecord objects ordered by most recent first"""
    user_db.create_db()
    base = datetime(2026, 1, 1, 10, 0, 0)

    user_db.insert_exec(server_id="SRV1", query="Q1", start=base, end=base + timedelta(seconds=5), nb_rows=3)
    user_db.insert_exec(server_id="SRV1", query="Q2", start=base + timedelta(minutes=1))

    records = user_db.get_last_records()

    assert len(records) == 2
    assert all(isinstance(r, LogRecord) for r in records)
    assert records[0].query == "Q2"  # most recent first
    assert records[1].nb_rows == 3


@pytest.mark.unit
def test_get_last_records_filters_by_query_name(user_db):
    """get_last_records should only return records for the requested query"""
    user_db.create_db()
    base = datetime(2026, 1, 1, 10, 0, 0)

    user_db.insert_exec(server_id="SRV1", query="Q1", start=base)
    user_db.insert_exec(server_id="SRV1", query="Q2", start=base)

    records = user_db.get_last_records(query_name="Q1")

    assert len(records) == 1
    assert records[0].query == "Q1"


@pytest.mark.unit
def test_get_last_files_returns_actual_files(user_db):
    """get_last_files should return the FILE paths in most-recent-first order"""
    user_db.create_db()
    base = datetime(2026, 1, 1, 10, 0, 0)

    user_db.insert_exec(server_id="S", query="Q1", start=base, file="/tmp/a.csv")
    user_db.insert_exec(server_id="S", query="Q2", start=base + timedelta(minutes=1), file="/tmp/b.csv")
    user_db.insert_exec(server_id="S", query="Q3", start=base + timedelta(minutes=2), file=None)

    files = user_db.get_last_files(nb_files=10)

    assert files == [Path("/tmp/b.csv"), Path("/tmp/a.csv")]


@pytest.mark.unit
def test_row_to_stats_handles_null_last_run():
    """row_to_stats should convert a NULL LAST_RUN into None"""
    db = UserLogs()
    row = {"SERVER_ID": "S", "QUERY": "Q", "NB_RUN": 0, "MIN_RUN": None, "MAX_RUN": None, "LAST_RUN": None}

    stat = db.row_to_stats(row)

    assert stat.last_run is None


@pytest.mark.unit
def test_row_to_logrecord_handles_null_file():
    """row_to_logrecord should convert a NULL FILE into None"""
    db = UserLogs()
    row = {
        "ROWID": 1,
        "SERVER_ID": "S",
        "QUERY": "Q",
        "START": "2026-01-01T00:00:00",
        "DURATION_SECS": None,
        "NB_ROWS": None,
        "PARAMETERS": None,
        "FILE": None,
        "EXPORTED": 0,
    }

    record = db.row_to_logrecord(row)

    assert record.file is None
    assert record.start == datetime(2026, 1, 1)


# ================================================================================
# Error Handling Edge Cases
# ================================================================================


@pytest.mark.unit
def test_get_user_version_handles_operational_error(user_db):
    """get_user_version should return -1 if the query itself fails"""
    user_db.create_db()

    with patch("sqlite3.connect", side_effect=sqlite3.OperationalError("boom")):
        assert user_db.get_version() == -1


@pytest.mark.unit
def test_update_db_0_to_1_handles_error(user_db):
    """update_db_0_to_1 should return False and not raise on error"""
    _create_v0_user_database(user_db.db_path)
    user_db.version = 0

    with patch("sqlite3.connect", side_effect=sqlite3.OperationalError("boom")):
        assert user_db.update_db_0_to_1() is False


@pytest.mark.unit
def test_update_db_1_to_2_handles_error(user_db):
    """update_db_1_to_2 should return False and not raise on error"""
    user_db.create_db()
    user_db.version = 1

    with patch("sqlite3.connect", side_effect=sqlite3.OperationalError("boom")):
        assert user_db.update_db_1_to_2() is False


@pytest.mark.unit
def test_insert_exec_handles_operational_error(user_db):
    """insert_exec should log and swallow a sqlite3.OperationalError"""
    user_db.create_db()

    with patch("sqlite3.connect", side_effect=sqlite3.OperationalError("boom")):
        # Should not raise
        user_db.insert_exec(server_id="S", query="Q", start=datetime.now())
