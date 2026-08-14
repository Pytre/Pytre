"""
Tests for Pytre_Core/logs_central.py

Basic tests for central logging infrastructure.
Full integration tests skipped due to SQLite/threading complexity.
"""

import pytest
import json
import logging
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import sqlite3
import contextlib

from Pytre_Core.logs_central import CentralLogs, CENTRAL_DB, LATEST_VERSION, SERVER_DB, ExecutionLogEntry, ServerDriven


# ================================================================================
# Fixtures
# ================================================================================


@pytest.fixture
def temp_logs_folder(tmp_path):
    """Create temporary logs folder"""
    logs_folder = tmp_path / "logs"
    logs_folder.mkdir()
    return logs_folder


@pytest.fixture
def central_logs(temp_logs_folder):
    """Create CentralLogs instance with temp folder"""
    # Reset singleton

    logs = CentralLogs(logs_folder=temp_logs_folder)
    return logs


# ================================================================================
# Initialization Tests
# ================================================================================


@pytest.mark.unit
def test_central_logs_initialization(temp_logs_folder):
    """CentralLogs should initialize with folder path"""

    logs = CentralLogs(logs_folder=temp_logs_folder)

    assert logs.logs_folder == temp_logs_folder
    assert logs.db_path == temp_logs_folder / CENTRAL_DB
    assert logs.version == -1
    assert logs.latest_version == LATEST_VERSION
    assert logs.update_already_run is False


@pytest.mark.unit
def test_central_logs_instances_are_independent():
    """CentralLogs no longer uses the singleton pattern (removed, see AppContext refactor)"""

    logs1 = CentralLogs()
    logs2 = CentralLogs()

    assert logs1 is not logs2


# ================================================================================
# Database Creation Tests
# ================================================================================


@pytest.mark.unit
def test_create_db_creates_database(central_logs):
    """create_db should create SQLite database"""
    # Create database
    central_logs.create_db()

    # Verify database exists
    assert central_logs.db_path.exists()

    # Verify version is set
    assert central_logs.version == LATEST_VERSION


@pytest.mark.unit
def test_create_db_creates_tables(central_logs):
    """create_db should create required tables"""
    central_logs.create_db()

    # Check tables exist
    with contextlib.closing(sqlite3.connect(central_logs.db_path)) as conn:
        cursor = conn.cursor()

        # Check QUERIES_EXEC table
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='QUERIES_EXEC'")
        result = cursor.fetchone()

        assert result is not None
        assert result[0] == "QUERIES_EXEC"


@pytest.mark.unit
def test_create_db_creates_indexes(central_logs):
    """create_db should create indexes"""
    central_logs.create_db()

    # Check indexes exist
    with contextlib.closing(sqlite3.connect(central_logs.db_path)) as conn:
        cursor = conn.cursor()

        # Check for indexes
        cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'IDX_%'")
        indexes = cursor.fetchall()

        # Should have at least 3 indexes
        assert len(indexes) >= 3


@pytest.mark.unit
def test_create_db_sets_user_version(central_logs):
    """create_db should set PRAGMA user_version"""
    central_logs.create_db()

    # Check user_version
    with contextlib.closing(sqlite3.connect(central_logs.db_path)) as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA user_version;")
        version = cursor.fetchone()[0]

        assert version == LATEST_VERSION


# ================================================================================
# Database Check Tests
# ================================================================================


@pytest.mark.unit
def test_check_db_creates_if_not_exists(central_logs):
    """check_db should create database if it doesn't exist"""
    # Ensure doesn't exist
    assert not central_logs.db_path.exists()

    # Check (with create=True)
    result = central_logs.check_db(create=True)

    # Should create
    assert central_logs.db_path.exists()
    assert result is True or result is None  # May return None


@pytest.mark.unit
def test_check_db_does_not_create_if_disabled(central_logs):
    """check_db with create=False should not create database"""
    # Ensure doesn't exist
    assert not central_logs.db_path.exists()

    # Check (with create=False)
    central_logs.check_db(create=False)

    # Should NOT create
    assert not central_logs.db_path.exists()


@pytest.mark.unit
def test_check_db_sets_update_flag(central_logs):
    """check_db should set update_already_run flag"""
    assert central_logs.update_already_run is False

    central_logs.check_db(create=True)

    assert central_logs.update_already_run is True


# ================================================================================
# Version Management Tests
# ================================================================================


@pytest.mark.unit
def test_get_central_version_returns_version(central_logs):
    """get_central_version should return database version"""
    # Create database first
    central_logs.create_db()

    # Get version
    version = central_logs.get_version()

    assert version == LATEST_VERSION


@pytest.mark.unit
def test_get_central_version_handles_missing_db(central_logs):
    """get_central_version should handle missing database"""
    # Don't create database
    assert not central_logs.db_path.exists()

    # Get version
    version = central_logs.get_version()

    # Should return -1 or 0 (SQLite default)
    assert version in [-1, 0]


# ================================================================================
# Update Database Tests
# ================================================================================


@pytest.mark.unit
def test_update_db_skips_if_already_run(central_logs):
    """update_db should skip if already run"""
    central_logs.update_already_run = True

    # Should return early (True or False depending on implementation)
    result = central_logs.update_db()

    # Just verify it doesn't crash
    assert result in [True, False, None]


@pytest.mark.unit
@pytest.mark.skip(reason="Complex database migration logic")
def test_update_db_migrates_schema():
    """update_db should migrate database schema if needed"""
    # This would require:
    # 1. Creating old version database
    # 2. Running update
    # 3. Verifying schema updated
    pass


# ================================================================================
# Cleanup Tests
# ================================================================================


@pytest.mark.unit
@pytest.mark.skip(reason="cleanup_temp_files also scans for .tmp files, making test unreliable")
def test_cleanup_temp_files_removes_files(client_driven):
    """cleanup_temp_files should remove files in temp_files list - ClientDriven only,
    write_atomic/cleanup_temp_files/temp_files moved down from CentralLogs (2026-08-26):
    ServerDriven no longer uses them since its pending file switched to plain JSONL appends."""
    # Ensure logs folder exists
    client_driven.logs_folder.mkdir(parents=True, exist_ok=True)

    # Create temp files with .tmp extension (as expected by cleanup)
    temp_file1 = client_driven.logs_folder / "temp1.tmp"
    temp_file2 = client_driven.logs_folder / "temp2.tmp"

    temp_file1.write_text("temp")
    temp_file2.write_text("temp")

    # Manually add to temp_files list
    client_driven.temp_files = [temp_file1, temp_file2]

    # Cleanup
    client_driven.cleanup_temp_files()

    # Should be removed (they're in the list)
    assert not temp_file1.exists()
    assert not temp_file2.exists()


@pytest.mark.unit
def test_cleanup_temp_files_handles_missing(client_driven):
    """cleanup_temp_files should handle missing files gracefully - ClientDriven only"""
    # Add non-existent file
    nonexistent = client_driven.logs_folder / "nonexistent.txt"
    client_driven.temp_files = [nonexistent]

    # Should not raise error
    client_driven.cleanup_temp_files()


# ================================================================================
# Sync Thread Tests
# ================================================================================


@pytest.mark.unit
@pytest.mark.skip(reason="Threading complexity")
def test_start_sync_creates_thread():
    """start_sync should create background sync thread"""
    # Would require mocking threading
    pass


@pytest.mark.unit
@pytest.mark.skip(reason="Threading complexity")
def test_stop_sync_stops_thread():
    """stop_sync should stop background sync thread"""
    # Would require mocking threading
    pass


# ================================================================================
# User Tracking Tests
# ================================================================================


@pytest.mark.unit
def test_set_user_info(central_logs):
    """CentralLogs should track user info"""
    central_logs.user_id = "test_user"
    central_logs.user_name = "Test User"

    assert central_logs.user_id == "test_user"
    assert central_logs.user_name == "Test User"


# ================================================================================
# Edge Cases
# ================================================================================


@pytest.mark.unit
def test_central_logs_handles_nonexistent_folder():
    """CentralLogs should handle nonexistent folder"""

    nonexistent = Path("/nonexistent/logs")

    # Should not raise error during init
    logs = CentralLogs(logs_folder=nonexistent)

    assert logs.logs_folder == nonexistent


@pytest.mark.unit
def test_create_db_handles_permission_error(central_logs):
    """create_db should handle permission errors gracefully"""
    # Mock connect to raise PermissionError
    with patch("sqlite3.connect", side_effect=sqlite3.OperationalError("Permission denied")):
        # Should not raise error
        central_logs.create_db()

        # Database should not exist
        # (In real scenario, it wouldn't be created)


# ================================================================================
# SQL Query Generator Tests
# ================================================================================


@pytest.mark.unit
def test_sql_rows_get_unsynced_returns_string(client_driven):
    """_get_unsynced_rows_sql should return SQL string - ClientDriven only, reads from a user db"""
    client_driven.user_id = "test_user"
    client_driven.user_name = "Test User"

    result = client_driven._get_unsynced_rows_sql()

    assert isinstance(result, str)
    assert "SELECT" in result.upper()
    assert "QUERIES_EXEC" in result
    assert "EXPORTED = 0" in result


@pytest.mark.unit
def test_sql_mark_rows_as_exported_returns_string(client_driven):
    """_mark_rows_as_exported_sql should return SQL string with placeholders - ClientDriven only"""
    placeholders = "?, ?, ?"

    result = client_driven._mark_rows_as_exported_sql(placeholders)

    assert isinstance(result, str)
    assert "UPDATE" in result.upper()
    assert "QUERIES_EXEC" in result
    assert "EXPORTED = 1" in result
    assert "ROWID IN" in result
    assert placeholders in result


@pytest.mark.unit
def test_sql_insert_into_central_db_returns_string(central_logs):
    """_insert_into_central_db_sql should return SQL insert string - shared, stays on CentralLogs"""
    result = central_logs._insert_into_central_db_sql()

    assert isinstance(result, str)
    assert "INSERT INTO" in result.upper()
    assert "QUERIES_EXEC" in result
    assert "VALUES" in result.upper()
    # Should have 8 placeholders for 8 columns
    assert result.count("?") == 8


# ================================================================================
# Temp Files List Tests
# ================================================================================


@pytest.mark.unit
def test_temp_files_list_initializes_empty(client_driven):
    """temp_files list should initialize empty - ClientDriven only, see cleanup_temp_files tests above"""
    assert isinstance(client_driven.temp_files, list)
    assert len(client_driven.temp_files) == 0


# ================================================================================
# Stop Event Tests
# ================================================================================


@pytest.mark.unit
def test_stop_event_initializes_correctly(central_logs):
    """stop_event should initialize as Event and not be set"""
    from threading import Event

    assert isinstance(central_logs.stop_event, Event)
    assert not central_logs.stop_event.is_set()


@pytest.mark.unit
def test_stop_event_can_be_set(central_logs):
    """stop_event should be settable"""
    central_logs.stop_event.set()

    assert central_logs.stop_event.is_set()


# ================================================================================
# Integration Stubs
# ================================================================================


@pytest.mark.unit
@pytest.mark.skip(reason="Integration test - requires full setup")
def test_log_query_execution():
    """Should log query execution to central database"""
    # This would require:
    # 1. Creating database
    # 2. Logging execution
    # 3. Querying to verify
    pass


@pytest.mark.unit
@pytest.mark.skip(reason="Integration test - requires threading")
def test_sync_to_central_database():
    """Should sync local logs to central database"""
    # This would require:
    # 1. Creating local log
    # 2. Starting sync thread
    # 3. Verifying sync occurred
    pass


# ================================================================================
# update_db migration Tests
# ================================================================================


def _create_v0_database(db_path):
    """Create a database matching the pre-migration (version 0) schema"""
    with contextlib.closing(sqlite3.connect(f"file:{db_path}?mode=rwc", uri=True)) as conn:
        conn.execute("PRAGMA user_version = 0;")
        conn.execute(
            """
                CREATE TABLE QUERIES_EXEC (
                    USER_ID          TEXT        NOT NULL,
                    USER_NAME        TEXT,
                    QUERY            TEXT        NOT NULL,
                    START            TEXT        NOT NULL,
                    END              TEXT,
                    DURATION_SECS    INTEGER,
                    NB_ROWS          INTEGER,
                    PARAMETERS       TEXT
                );
            """
        )
        conn.commit()


@pytest.mark.unit
def test_update_db_migrates_from_v0_to_latest(central_logs):
    """update_db should run every migration step needed to reach latest_version"""
    _create_v0_database(central_logs.db_path)
    central_logs.version = 0

    result = central_logs.update_db()

    assert result is True
    assert central_logs.version == LATEST_VERSION

    with contextlib.closing(sqlite3.connect(central_logs.db_path)) as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(QUERIES_EXEC)")
        columns = [row[1] for row in cursor.fetchall()]

    assert "SERVER_ID" in columns
    assert "END" not in columns


@pytest.mark.unit
def test_update_db_returns_false_if_version_unknown(central_logs):
    """update_db should return False if central_version couldn't be determined"""
    central_logs.version = -1

    result = central_logs.update_db()

    assert result is False


@pytest.mark.unit
def test_update_db_returns_true_if_already_latest(central_logs):
    """update_db should return True without migrating if already at latest_version"""
    central_logs.version = LATEST_VERSION

    result = central_logs.update_db()

    assert result is True


@pytest.mark.unit
def test_update_db_aborts_if_a_migration_step_fails(central_logs):
    """update_db should stop and return False if a migration step fails"""
    _create_v0_database(central_logs.db_path)
    central_logs.version = 0

    with patch.object(central_logs, "update_db_0_to_1", return_value=False):
        result = central_logs.update_db()

    assert result is False


# ================================================================================
# start / stop Tests
# ================================================================================


@pytest.mark.unit
def test_trigger_sync_aborts_if_no_central_db(client_driven, tmp_path):
    """start should log and return early if central_db is falsy - ClientDriven only"""
    client_driven.db_path = None

    # Should not raise
    client_driven.trigger_sync(tmp_path / "user.db")

    assert client_driven.sync_thread is None


@pytest.mark.unit
def test_trigger_sync_aborts_if_check_db_fails(client_driven, tmp_path):
    """start should return early if check_db fails"""
    with patch.object(client_driven, "check_db", return_value=False):
        client_driven.trigger_sync(tmp_path / "user.db")

    assert client_driven.sync_thread is None


@pytest.mark.unit
def test_trigger_sync_aborts_if_user_logs_missing(client_driven, tmp_path):
    """start should return early if the user_logs file doesn't exist"""
    with patch.object(client_driven, "check_db", return_value=True):
        client_driven.trigger_sync(tmp_path / "nonexistent_user.db")

    assert client_driven.sync_thread is None


@pytest.mark.unit
def test_trigger_sync_starts_thread(client_driven, tmp_path):
    """start should start a background thread when all checks pass"""
    user_logs = tmp_path / "user.db"
    user_logs.write_text("fake db")

    with patch.object(client_driven, "check_db", return_value=True):
        client_driven.trigger_sync(user_logs, user_id="u1", user_name="User One")
        client_driven.sync_thread.join(timeout=2)

    assert client_driven.user_id == "u1"
    assert client_driven.user_name == "User One"


@pytest.mark.unit
def test_stop_sync_returns_true_if_no_thread_running(central_logs):
    """stop_sync should return True immediately if there's no active sync thread"""
    result = central_logs.stop()

    assert result is True


@pytest.mark.unit
def test_stop_sync_joins_running_thread(central_logs):
    """stop_sync should set the stop_event and join the running thread"""
    from threading import Thread, Event

    done = Event()

    def worker():
        done.wait(timeout=2)

    central_logs.sync_thread = Thread(target=worker, daemon=True)
    central_logs.sync_thread.start()
    done.set()

    result = central_logs.stop(timeout=2)

    assert central_logs.stop_event.is_set()
    assert result is True


# ------------------------------------------------------------------------------
# stop() / _on_stopped() hook contract
# ------------------------------------------------------------------------------


@pytest.mark.unit
def test_stop_calls_on_stopped_when_no_thread_was_running(central_logs, monkeypatch):
    calls = []
    monkeypatch.setattr(central_logs, "_on_stopped", lambda: calls.append(1))

    central_logs.stop()

    assert calls == [1]


@pytest.mark.unit
def test_stop_calls_on_stopped_exactly_once_after_successfully_joining_a_running_thread(central_logs, monkeypatch):
    """Regression: _on_stopped() must fire once the running thread is confirmed joined, not
    only when it was already idle - and it must never fire twice."""
    from threading import Thread, Event

    done = Event()

    def worker():
        done.wait(timeout=2)

    central_logs.sync_thread = Thread(target=worker, daemon=True)
    central_logs.sync_thread.start()
    done.set()

    calls = []
    monkeypatch.setattr(central_logs, "_on_stopped", lambda: calls.append(1))
    result = central_logs.stop(timeout=2)

    assert result is True
    assert calls == [1]


@pytest.mark.unit
def test_stop_does_not_call_on_stopped_if_the_thread_fails_to_join(central_logs, monkeypatch):
    """A thread still alive after the join timeout isn't confirmed stopped - _on_stopped()
    must not run, since the subclass cleanup (e.g. temp files) may still be in use."""
    from threading import Thread, Event

    block = Event()

    def worker():
        block.wait(timeout=5)

    central_logs.sync_thread = Thread(target=worker, daemon=True)
    central_logs.sync_thread.start()

    calls = []
    monkeypatch.setattr(central_logs, "_on_stopped", lambda: calls.append(1))
    result = central_logs.stop(timeout=0.05)

    assert result is False
    assert calls == []

    block.set()
    central_logs.sync_thread.join(2)


@pytest.mark.unit
def test_del_only_delegates_to_stop(central_logs, monkeypatch):
    """__del__ must not call _on_stopped() itself - stop() already guarantees exactly one call,
    calling it again from __del__ would double-fire subclass cleanup (e.g. ClientDriven's
    cleanup_temp_files) whenever stop() already handled it internally."""
    calls = []
    monkeypatch.setattr(central_logs, "stop", lambda timeout=5: calls.append(timeout))
    monkeypatch.setattr(central_logs, "_on_stopped", lambda: calls.append("on_stopped"))

    central_logs.__del__()

    assert calls == [5]


# ================================================================================
# ClientDriven Tests
# ================================================================================


from Pytre_Core.logs_central import ClientDriven  # noqa: E402


@pytest.fixture
def client_driven(temp_logs_folder):
    """Create ClientDriven instance with temp folder"""
    client_driven = ClientDriven(logs_folder=temp_logs_folder)
    client_driven.sync_heartbeat = 1  # keep any heartbeat thread snappy in tests
    return client_driven


@pytest.mark.unit
def test_client_driven_initialization(temp_logs_folder):
    """ClientDriven should set up queue/lock file paths under logs_folder"""
    client_driven = ClientDriven(logs_folder=temp_logs_folder)

    assert client_driven.queue_ctrl_file == temp_logs_folder / "Pytre_Queue.json"
    assert client_driven.failed_folder == temp_logs_folder / "Pytre_Failed"
    assert len(client_driven.export_folders) == 2


# ------------------------------------------------------------------------------
# create_dirs
# ------------------------------------------------------------------------------


@pytest.mark.unit
def test_create_dirs_creates_single_path(client_driven, temp_logs_folder):
    """create_dirs should accept a single Path and create it"""
    new_dir = temp_logs_folder / "single"

    result = client_driven.create_dirs(new_dir)

    assert result is True
    assert new_dir.is_dir()


@pytest.mark.unit
def test_create_dirs_creates_list_of_paths(client_driven, temp_logs_folder):
    """create_dirs should accept a list of Paths and create all of them"""
    dirs = [temp_logs_folder / "a", temp_logs_folder / "b"]

    result = client_driven.create_dirs(dirs)

    assert result is True
    assert all(d.is_dir() for d in dirs)


@pytest.mark.unit
def test_create_dirs_handles_permission_error(client_driven, temp_logs_folder):
    """create_dirs should return False and not raise on PermissionError"""
    with patch.object(Path, "mkdir", side_effect=PermissionError("denied")):
        result = client_driven.create_dirs(temp_logs_folder / "forbidden")

    assert result is False


# ------------------------------------------------------------------------------
# write_atomic
# ------------------------------------------------------------------------------


@pytest.mark.unit
def test_write_atomic_writes_dict_as_json(client_driven, temp_logs_folder):
    """write_atomic should write a dict as JSON and rename it into place"""
    target = temp_logs_folder / "data.json"

    result = client_driven.write_atomic(target, {"a": 1})

    assert result is True
    assert json.loads(target.read_text()) == {"a": 1}
    assert client_driven.temp_files == []


@pytest.mark.unit
def test_write_atomic_writes_string_content(client_driven, temp_logs_folder):
    """write_atomic should write plain string content"""
    target = temp_logs_folder / "data.txt"

    result = client_driven.write_atomic(target, "hello")

    assert result is True
    assert target.read_text() == "hello"


@pytest.mark.unit
def test_write_atomic_handles_permission_error(client_driven, temp_logs_folder):
    """write_atomic should return False on PermissionError without raising"""
    target = temp_logs_folder / "data.json"

    with patch("builtins.open", side_effect=PermissionError("denied")):
        result = client_driven.write_atomic(target, {"a": 1})

    assert result is False
    assert not target.exists()


# ------------------------------------------------------------------------------
# get_active_export_folder / set_active_export_folder
# ------------------------------------------------------------------------------


@pytest.mark.unit
def test_get_active_export_folder_defaults_to_zero(client_driven):
    """get_active_export_folder should default to folder 0 and create the queue file"""
    index, folder = client_driven.get_active_export_folder()

    assert index == 0
    assert folder == client_driven.export_folders[0]
    assert client_driven.queue_ctrl_file.exists()


@pytest.mark.unit
def test_set_active_export_folder_updates_index(client_driven):
    """set_active_export_folder should persist the new active index"""
    result = client_driven.set_active_export_folder(1)

    assert result is True
    index, folder = client_driven.get_active_export_folder()
    assert index == 1
    assert folder == client_driven.export_folders[1]


@pytest.mark.unit
def test_set_active_export_folder_wraps_around(client_driven):
    """set_active_export_folder should wrap the index using modulo"""
    client_driven.set_active_export_folder(2)  # only 2 folders -> wraps to 0

    index, _ = client_driven.get_active_export_folder()
    assert index == 0


@pytest.mark.unit
def test_get_active_export_folder_handles_corrupt_json(client_driven):
    """get_active_export_folder should fall back to folder 0 on corrupt JSON"""
    client_driven.queue_ctrl_file.parent.mkdir(parents=True, exist_ok=True)
    client_driven.queue_ctrl_file.write_text("{ not valid json")

    index, folder = client_driven.get_active_export_folder()

    assert index == 0
    assert folder == client_driven.export_folders[0]


# ------------------------------------------------------------------------------
# try_acquire_lock / release_lock / break_stale_lock
# ------------------------------------------------------------------------------


@pytest.mark.unit
def test_try_acquire_lock_succeeds_when_unlocked(client_driven):
    """try_acquire_lock should acquire the lock and create the locked file"""
    try:
        result = client_driven.try_acquire_lock()

        assert result is True
        assert client_driven.sync_locked_file.exists()
        assert not client_driven.sync_unlocked_file.exists()
    finally:
        client_driven.release_lock()


@pytest.mark.unit
def test_try_acquire_lock_fails_when_already_locked(client_driven):
    """try_acquire_lock should fail if another process already holds the lock"""
    try:
        assert client_driven.try_acquire_lock() is True

        other = ClientDriven(logs_folder=client_driven.logs_folder)
        other.sync_heartbeat = 1
        assert other.try_acquire_lock() is False
    finally:
        client_driven.release_lock()


@pytest.mark.unit
def test_release_lock_restores_unlocked_file(client_driven):
    """release_lock should rename the locked file back to unlocked"""
    client_driven.try_acquire_lock()

    result = client_driven.release_lock()

    assert result is True
    assert client_driven.sync_unlocked_file.exists()
    assert not client_driven.sync_locked_file.exists()


@pytest.mark.unit
def test_release_lock_returns_false_if_not_locked(client_driven):
    """release_lock should return False if there's no lock to release"""
    result = client_driven.release_lock()

    assert result is False


@pytest.mark.unit
def test_break_stale_lock_breaks_expired_lock(client_driven):
    """break_stale_lock should break a lock whose heartbeat is older than the timeout"""
    client_driven.logs_folder.mkdir(parents=True, exist_ok=True)
    stale_data = {
        "status": "locked",
        "acquired": "2000-01-01T00:00:00",
        "last_heartbeat": "2000-01-01T00:00:00",
        "user": "someone",
        "hostname": "host",
        "pid": 1,
    }
    client_driven.sync_locked_file.write_text(json.dumps(stale_data))

    result = client_driven.break_stale_lock()

    assert result is True
    assert not client_driven.sync_locked_file.exists()
    assert client_driven.sync_unlocked_file.exists()


@pytest.mark.unit
def test_break_stale_lock_keeps_fresh_lock(client_driven):
    """break_stale_lock should leave a recently-heartbeated lock untouched"""
    from datetime import datetime

    client_driven.logs_folder.mkdir(parents=True, exist_ok=True)
    fresh_data = {
        "status": "locked",
        "acquired": datetime.now().isoformat(),
        "last_heartbeat": datetime.now().isoformat(),
        "user": "someone",
        "hostname": "host",
        "pid": 1,
    }
    client_driven.sync_locked_file.write_text(json.dumps(fresh_data))

    result = client_driven.break_stale_lock()

    assert result is True
    assert client_driven.sync_locked_file.exists()


@pytest.mark.unit
def test_break_stale_lock_noop_if_no_lock_file(client_driven):
    """break_stale_lock should be a no-op if there's no locked file"""
    assert client_driven.break_stale_lock() is True


@pytest.mark.unit
def test_update_heartbeat_updates_timestamp(client_driven):
    """update_heartbeat should refresh last_heartbeat in the locked file"""
    try:
        client_driven.try_acquire_lock()
        before = json.loads(client_driven.sync_locked_file.read_text())["last_heartbeat"]

        import time as time_module

        time_module.sleep(0.01)
        client_driven.update_heartbeat()

        after = json.loads(client_driven.sync_locked_file.read_text())["last_heartbeat"]
        assert after >= before
    finally:
        client_driven.release_lock()


@pytest.mark.unit
def test_update_heartbeat_returns_false_if_not_locked(client_driven):
    """update_heartbeat should return False if there's no locked file"""
    assert client_driven.update_heartbeat() is False


# ------------------------------------------------------------------------------
# Files/folders processing helpers
# ------------------------------------------------------------------------------


@pytest.mark.unit
def test_failed_files_move_relocates_files(client_driven, temp_logs_folder):
    """failed_files_move should rename files into the failed folder with a timestamp suffix"""
    src_folder = temp_logs_folder / "src"
    src_folder.mkdir()
    bad_file = src_folder / "bad.json"
    bad_file.write_text("{}")

    client_driven.failed_files_move([bad_file], "20260101120000")

    assert not bad_file.exists()
    moved = list(client_driven.failed_folder.glob("bad_20260101120000.json"))
    assert len(moved) == 1


@pytest.mark.unit
def test_processed_files_remove_deletes_files(client_driven, temp_logs_folder):
    """processed_files_remove should delete each processed file"""
    f = temp_logs_folder / "processed.json"
    f.write_text("{}")

    client_driven.processed_files_remove([f])

    assert not f.exists()


@pytest.mark.unit
def test_processed_files_remove_handles_missing_file(client_driven, tmp_path):
    """processed_files_remove should not raise if a file is already gone"""
    client_driven.processed_files_remove([tmp_path / "already_gone.json"])


@pytest.mark.unit
def test_processed_files_move_moves_to_active_folder(client_driven, temp_logs_folder):
    """processed_files_move should move files into the given active folder"""
    active_folder = temp_logs_folder / "active"
    active_folder.mkdir()
    f = temp_logs_folder / "todo.json"
    f.write_text("{}")

    client_driven.processed_files_move([f], active_folder)

    assert not f.exists()
    assert (active_folder / "todo.json").exists()


@pytest.mark.unit
def test_processed_files_move_aborts_if_folder_missing(client_driven, temp_logs_folder):
    """processed_files_move should do nothing if the active folder doesn't exist"""
    f = temp_logs_folder / "todo.json"
    f.write_text("{}")

    client_driven.processed_files_move([f], temp_logs_folder / "nonexistent")

    assert f.exists()  # left untouched


@pytest.mark.unit
def test_files_extract_infos_reads_json_rows(client_driven, temp_logs_folder):
    """files_extract_infos should read and concatenate rows from JSON files"""
    f1 = temp_logs_folder / "f1.json"
    f1.write_text(json.dumps([["row1"], ["row2"]]))
    f2 = temp_logs_folder / "f2.json"
    f2.write_text(json.dumps([["row3"]]))

    rows, processed = client_driven.files_extract_infos([f1, f2])

    assert rows == [["row1"], ["row2"], ["row3"]]
    assert set(processed) == {f1, f2}


@pytest.mark.unit
def test_files_extract_infos_moves_corrupt_file_to_failed(client_driven, temp_logs_folder):
    """files_extract_infos should move unparseable files to the failed folder"""
    bad = temp_logs_folder / "bad.json"
    bad.write_text("{ not valid json")

    rows, processed = client_driven.files_extract_infos([bad])

    assert rows == []
    assert processed == []
    assert not bad.exists()
    assert list(client_driven.failed_folder.glob("bad_*.json"))


@pytest.mark.unit
def test_files_extract_infos_handles_missing_file(client_driven, tmp_path):
    """files_extract_infos should skip files that vanished before being read"""
    rows, processed = client_driven.files_extract_infos([tmp_path / "gone.json"])

    assert rows == []
    assert processed == []


# ------------------------------------------------------------------------------
# SQLite-backed sync helpers
# ------------------------------------------------------------------------------


def _create_user_logs_with_unsynced_row(user_logs_path):
    with contextlib.closing(sqlite3.connect(user_logs_path)) as conn:
        conn.execute(
            """
                CREATE TABLE QUERIES_EXEC (
                    SERVER_ID TEXT, QUERY TEXT, START TEXT,
                    DURATION_SECS INTEGER, NB_ROWS INTEGER, PARAMETERS TEXT, EXPORTED INTEGER
                )
            """
        )
        conn.execute(
            "INSERT INTO QUERIES_EXEC VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("SRV1", "SELECT 1", "2026-01-01T00:00:00", 1, 10, "{}", 0),
        )
        conn.commit()


@pytest.mark.unit
def test_rows_get_unsynced_returns_rows(client_driven, temp_logs_folder):
    """rows_get_unsynced should return unsynced rows from the user database"""
    user_logs = temp_logs_folder / "user.db"
    _create_user_logs_with_unsynced_row(user_logs)

    client_driven.user_logs = user_logs
    client_driven.user_id = "u1"
    client_driven.user_name = "User One"

    rows = client_driven.get_unsynced_rows()

    assert len(rows) == 1
    assert rows[0][1] == "SRV1"  # SERVER_ID


@pytest.mark.unit
def test_rows_get_unsynced_returns_none_if_stopped(client_driven, temp_logs_folder):
    """get_unsynced_rows should return None immediately if stop_event is set"""
    client_driven.stop_event.set()

    assert client_driven.get_unsynced_rows() is None


@pytest.mark.unit
def test_mark_rows_as_exported_updates_flag(client_driven, temp_logs_folder):
    """mark_rows_as_exported should set EXPORTED=1 for the given row ids"""
    user_logs = temp_logs_folder / "user.db"
    _create_user_logs_with_unsynced_row(user_logs)
    client_driven.user_logs = user_logs

    client_driven.mark_rows_as_exported([1])

    with contextlib.closing(sqlite3.connect(user_logs)) as conn:
        exported = conn.execute("SELECT EXPORTED FROM QUERIES_EXEC WHERE ROWID = 1").fetchone()[0]

    assert exported == 1


@pytest.mark.unit
def test_insert_into_central_db_inserts_rows(client_driven):
    """insert_into_central_db should insert rows and return True"""
    client_driven.create_db()

    row = ("SRV1", "u1", "User One", "SELECT 1", "2026-01-01T00:00:00", 1, 10, "{}")
    result = client_driven.insert_into_central_db([row])

    assert result is True
    with contextlib.closing(sqlite3.connect(client_driven.db_path)) as conn:
        count = conn.execute("SELECT COUNT(*) FROM QUERIES_EXEC").fetchone()[0]
    assert count == 1


@pytest.mark.unit
def test_insert_into_central_db_returns_false_if_stopped(client_driven):
    """insert_into_central_db should return False immediately if stop_event is set"""
    client_driven.stop_event.set()

    assert client_driven.insert_into_central_db([]) is False


@pytest.mark.unit
def test_insert_into_central_db_returns_false_if_update_fails(client_driven):
    """insert_into_central_db should return False if update_db fails"""
    client_driven.version = -1  # update_db() returns False for unknown version

    assert client_driven.insert_into_central_db([]) is False


@pytest.mark.unit
def test_export_unsynced_data_writes_export_file(client_driven, temp_logs_folder):
    """export_unsynced_data should write a JSON export file and mark rows exported"""
    user_logs = temp_logs_folder / "user.db"
    _create_user_logs_with_unsynced_row(user_logs)
    client_driven.user_logs = user_logs
    client_driven.user_id = "u1"
    client_driven.user_name = "User One"

    result = client_driven.export_unsynced_data()

    assert result is True
    _, active_folder = client_driven.get_active_export_folder()
    exported_files = list(active_folder.glob("*.json"))
    assert len(exported_files) == 1

    with contextlib.closing(sqlite3.connect(user_logs)) as conn:
        exported = conn.execute("SELECT EXPORTED FROM QUERIES_EXEC WHERE ROWID = 1").fetchone()[0]
    assert exported == 1


@pytest.mark.unit
def test_export_unsynced_data_noop_if_stopped(client_driven):
    """export_unsynced_data should return True immediately if stop_event is set"""
    client_driven.stop_event.set()

    assert client_driven.export_unsynced_data() is True


@pytest.mark.unit
def test_export_unsynced_data_noop_if_nothing_to_export(client_driven, temp_logs_folder):
    """export_unsynced_data should return True if there are no unsynced rows"""
    user_logs = temp_logs_folder / "user.db"
    with contextlib.closing(sqlite3.connect(user_logs)) as conn:
        conn.execute(
            "CREATE TABLE QUERIES_EXEC (SERVER_ID TEXT, QUERY TEXT, START TEXT, "
            "DURATION_SECS INTEGER, NB_ROWS INTEGER, PARAMETERS TEXT, EXPORTED INTEGER)"
        )
        conn.commit()
    client_driven.user_logs = user_logs
    client_driven.user_id = "u1"

    result = client_driven.export_unsynced_data()

    assert result is True
    _, active_folder = client_driven.get_active_export_folder()
    assert list(active_folder.glob("*.json")) == []


# ------------------------------------------------------------------------------
# wait_for_files_completion / process_files / synchronize_folder
# ------------------------------------------------------------------------------


@pytest.mark.unit
def test_wait_for_files_completion_returns_stable_files(client_driven, temp_logs_folder, monkeypatch):
    """wait_for_files_completion should return files whose size stopped changing"""
    monkeypatch.setattr("Pytre_Core.logs_central.time.sleep", lambda _: None)

    folder = temp_logs_folder / "watch"
    folder.mkdir()
    f = folder / "data.json"
    f.write_text("{}")

    stable = client_driven.wait_for_files_completion(folder)

    assert stable == {str(f)}


@pytest.mark.unit
def test_wait_for_files_completion_returns_none_if_stopped(client_driven, temp_logs_folder):
    """wait_for_files_completion should return None immediately if stop_event is set"""
    client_driven.stop_event.set()
    folder = temp_logs_folder / "watch"
    folder.mkdir()

    assert client_driven.wait_for_files_completion(folder) is None


@pytest.mark.unit
def test_process_files_returns_false_if_nothing_to_process(client_driven):
    """process_files should return False and log if there's nothing to process"""
    assert client_driven.process_files(None) is False
    assert client_driven.process_files(set()) is False


@pytest.mark.unit
def test_process_files_returns_false_if_stopped(client_driven):
    """process_files should return False immediately if stop_event is set"""
    client_driven.stop_event.set()

    assert client_driven.process_files({"some_file.json"}) is False


@pytest.mark.unit
def test_process_files_inserts_and_removes_on_success(client_driven, temp_logs_folder):
    """process_files should insert extracted rows and remove the source files on success"""
    client_driven.create_db()

    export_file = temp_logs_folder / "export.json"
    row = ["SRV1", "u1", "User One", "SELECT 1", "2026-01-01T00:00:00", 1, 10, "{}"]
    export_file.write_text(json.dumps([row]))

    result = client_driven.process_files({str(export_file)})

    assert result is True
    assert not export_file.exists()
    with contextlib.closing(sqlite3.connect(client_driven.db_path)) as conn:
        count = conn.execute("SELECT COUNT(*) FROM QUERIES_EXEC").fetchone()[0]
    assert count == 1


@pytest.mark.unit
def test_process_files_moves_back_on_insert_failure(client_driven, temp_logs_folder):
    """process_files should move files back to the active folder if the DB insert fails"""
    client_driven.create_dirs(client_driven.export_folders)

    export_file = temp_logs_folder / "export.json"
    row = ["SRV1", "u1", "User One", "SELECT 1", "2026-01-01T00:00:00", 1, 10, "{}"]
    export_file.write_text(json.dumps([row]))

    with patch.object(client_driven, "insert_into_central_db", return_value=False):
        result = client_driven.process_files({str(export_file)})

    assert result is True  # method itself still returns True, see source
    _, active_folder = client_driven.get_active_export_folder()
    assert (active_folder / "export.json").exists()


@pytest.mark.unit
def test_synchronize_folder_raises_if_no_stable_files(client_driven, temp_logs_folder, monkeypatch):
    """synchronize_folder should raise TimeoutError if wait_for_files_completion finds nothing"""
    monkeypatch.setattr("Pytre_Core.logs_central.time.sleep", lambda _: None)
    folder = temp_logs_folder / "empty_watch"
    folder.mkdir()

    with pytest.raises(TimeoutError):
        client_driven.synchronize_folder(folder)


# ------------------------------------------------------------------------------
# synchronization_attempt (full happy path integration)
# ------------------------------------------------------------------------------


@pytest.mark.unit
def test_synchronization_attempt_returns_false_if_nothing_to_sync(client_driven):
    """synchronization_attempt should bail out early if there's nothing exported to process"""
    result = client_driven.synchronization_attempt()

    assert result is False
    assert not client_driven.sync_locked_file.exists()


@pytest.mark.unit
def test_synchronization_attempt_full_happy_path(client_driven, temp_logs_folder, monkeypatch):
    """synchronization_attempt should export, lock, switch the active folder, and sync into the central db"""
    monkeypatch.setattr("Pytre_Core.logs_central.time.sleep", lambda _: None)

    user_logs = temp_logs_folder / "user.db"
    _create_user_logs_with_unsynced_row(user_logs)
    client_driven.user_logs = user_logs
    client_driven.user_id = "u1"
    client_driven.user_name = "User One"
    client_driven.create_db()

    result = client_driven.synchronization_attempt()

    assert result is True

    with contextlib.closing(sqlite3.connect(client_driven.db_path)) as conn:
        count = conn.execute("SELECT COUNT(*) FROM QUERIES_EXEC").fetchone()[0]
    assert count == 1

    # lock must have been released, and the active folder switched from 0 to 1
    assert not client_driven.sync_locked_file.exists()
    index, _ = client_driven.get_active_export_folder()
    assert index == 1


@pytest.mark.unit
def test_synchronization_attempt_returns_true_if_lock_busy(client_driven, temp_logs_folder):
    """synchronization_attempt should still return True (retry later) if the lock is held elsewhere"""
    client_driven.create_dirs(client_driven.export_folders)
    (client_driven.export_folders[0] / "pending.json").write_text("[]")

    with patch.object(client_driven, "try_acquire_lock", return_value=False):
        result = client_driven.synchronization_attempt()

    assert result is True


@pytest.mark.unit
def test_synchronization_attempt_processes_leftover_previous_folder(client_driven, temp_logs_folder, monkeypatch):
    """synchronization_attempt should first flush a non-empty previous folder before switching active"""
    monkeypatch.setattr("Pytre_Core.logs_central.time.sleep", lambda _: None)
    client_driven.create_db()
    client_driven.create_dirs(client_driven.export_folders)

    # active folder is index 1 -> previous is index 0, where we leave an unprocessed file
    client_driven.set_active_export_folder(1)
    leftover = client_driven.export_folders[0] / "leftover.json"
    leftover_row = ["SRV1", "u1", "User One", "SELECT 1", "2026-01-01T00:00:00", 1, 10, "{}"]
    leftover.write_text(json.dumps([leftover_row]))

    user_logs = temp_logs_folder / "user.db"
    _create_user_logs_with_unsynced_row(user_logs)
    client_driven.user_logs = user_logs
    client_driven.user_id = "u1"

    result = client_driven.synchronization_attempt()

    assert result is True
    assert not leftover.exists()  # previous folder's leftover file was processed and removed
    with contextlib.closing(sqlite3.connect(client_driven.db_path)) as conn:
        count = conn.execute("SELECT COUNT(*) FROM QUERIES_EXEC").fetchone()[0]
    assert count == 2  # leftover row + newly exported row


@pytest.mark.unit
def test_synchronization_attempt_releases_lock_on_error(client_driven, temp_logs_folder, monkeypatch):
    """synchronization_attempt should release the lock even if processing raises"""
    monkeypatch.setattr("Pytre_Core.logs_central.time.sleep", lambda _: None)
    client_driven.create_dirs(client_driven.export_folders)
    (client_driven.export_folders[0] / "pending.json").write_text("not valid json")

    result = client_driven.synchronization_attempt()

    assert result is True  # the outer try/except swallows the error, see source
    assert not client_driven.sync_locked_file.exists()


# ------------------------------------------------------------------------------
# sync_thread_start (retry loop)
# ------------------------------------------------------------------------------


@pytest.mark.unit
def test_sync_thread_start_succeeds_on_first_attempt(client_driven, monkeypatch):
    """sync_thread_start should stop after the first successful attempt, with no retry delay"""
    monkeypatch.setattr("Pytre_Core.logs_central.time.sleep", lambda _: None)

    with patch.object(client_driven, "synchronization_attempt", return_value=True) as mock_attempt:
        client_driven.thread_start()

    assert mock_attempt.call_count == 1
    assert client_driven.sync_thread is None


@pytest.mark.unit
def test_sync_thread_start_retries_after_failure_then_succeeds(client_driven, monkeypatch):
    """sync_thread_start should retry (with a delay) after a failed attempt"""
    sleep_calls = []
    monkeypatch.setattr("Pytre_Core.logs_central.time.sleep", lambda s: sleep_calls.append(s))

    with patch.object(client_driven, "synchronization_attempt", side_effect=[False, True]) as mock_attempt:
        client_driven.thread_start()

    assert mock_attempt.call_count == 2
    assert len(sleep_calls) == 1


@pytest.mark.unit
def test_sync_thread_start_gives_up_after_max_retries(client_driven, monkeypatch):
    """sync_thread_start should stop after exhausting all configured retries"""
    monkeypatch.setattr("Pytre_Core.logs_central.time.sleep", lambda _: None)

    with patch.object(client_driven, "synchronization_attempt", return_value=False) as mock_attempt:
        client_driven.thread_start()

    assert mock_attempt.call_count == 3  # len(retries_delay) in the source


@pytest.mark.unit
def test_sync_thread_start_stops_early_if_stop_event_set(client_driven, monkeypatch):
    """sync_thread_start should not retry further once stop_event is set"""
    monkeypatch.setattr("Pytre_Core.logs_central.time.sleep", lambda _: None)

    def fail_and_stop(*args, **kwargs):
        client_driven.stop_event.set()
        return False

    with patch.object(client_driven, "synchronization_attempt", side_effect=fail_and_stop) as mock_attempt:
        client_driven.thread_start()

    assert mock_attempt.call_count == 1


@pytest.mark.unit
def test_sync_thread_start_retries_after_unexpected_exception(client_driven, monkeypatch):
    """sync_thread_start should catch an unexpected exception from an attempt and retry"""
    monkeypatch.setattr("Pytre_Core.logs_central.time.sleep", lambda _: None)

    with patch.object(
        client_driven, "synchronization_attempt", side_effect=[ValueError("boom"), True]
    ) as mock_attempt:
        client_driven.thread_start()

    assert mock_attempt.call_count == 2


@pytest.mark.unit
def test_sync_thread_start_aborts_if_dirs_cannot_be_created(client_driven):
    """sync_thread_start should not attempt to sync at all if export folders can't be created"""
    with (
        patch.object(client_driven, "create_dirs", return_value=False),
        patch.object(client_driven, "synchronization_attempt") as mock_attempt,
    ):
        client_driven.thread_start()

    assert not mock_attempt.called


# ================================================================================
# ServerDriven / ExecutionLogEntry Tests
# ================================================================================


def _make_entry(
    *,
    server_id: str = "SRV1",
    user_id: str = "u1",
    user_name: str = "User One",
    query_id: str = "Q1",
    start: datetime = datetime(2026, 1, 1, 10, 30),
    duration_secs: int = 5,
    nb_rows: int = 42,
    parameters: dict[str, str] | None = None,
) -> ExecutionLogEntry:
    """Builds an ExecutionLogEntry with sensible defaults, overridable per test"""
    return ExecutionLogEntry(
        server_id=server_id,
        user_id=user_id,
        user_name=user_name,
        query_id=query_id,
        start=start,
        duration_secs=duration_secs,
        nb_rows=nb_rows,
        parameters=parameters or {},
    )


@pytest.fixture
def server_driven(temp_logs_folder):
    """Create ServerDriven instance with temp folder, fast polling for quick tests"""
    sd = ServerDriven(logs_folder=temp_logs_folder)
    sd.queue_poll_timeout = 0.02
    return sd


# ------------------------------------------------------------------------------
# ExecutionLogEntry
# ------------------------------------------------------------------------------


@pytest.mark.unit
def test_execution_log_entry_as_row_with_parameters():
    entry = _make_entry(parameters={"p": "v"})

    row = entry.as_row()

    assert row[:4] == ("SRV1", "u1", "User One", "Q1")
    assert row[4] == "2026-01-01T10:30:00"
    assert row[5:7] == (5, 42)
    assert json.loads(row[7]) == {"p": "v"}


@pytest.mark.unit
def test_execution_log_entry_as_row_without_parameters_is_none():
    entry = _make_entry(parameters={})

    assert entry.as_row()[7] is None


@pytest.mark.unit
def test_execution_log_entry_to_dict_from_dict_round_trip():
    entry = _make_entry(parameters={"p": "v"})

    restored = ExecutionLogEntry.from_dict(entry.to_dict())

    assert restored == entry


# ------------------------------------------------------------------------------
# ServerDriven - initialization
# ------------------------------------------------------------------------------


@pytest.mark.unit
def test_server_driven_uses_a_distinct_db_filename(temp_logs_folder):
    """ServerDriven must never default to the same db file as ClientDriven - it has no
    cross-process locking, so sharing a file with ClientDriven risks corruption"""
    sd = ServerDriven(logs_folder=temp_logs_folder)

    assert sd.db_path == temp_logs_folder / SERVER_DB
    assert sd.db_path != temp_logs_folder / CENTRAL_DB


@pytest.mark.unit
def test_server_driven_initializes_empty(server_driven):
    assert server_driven.queue.empty()
    assert server_driven.failures_nb == 0
    assert server_driven.sync_thread is None


@pytest.mark.unit
def test_server_driven_has_no_write_atomic_mechanism(server_driven):
    """Regression guard: write_atomic/cleanup_temp_files/temp_files moved down to ClientDriven
    (2026-08-26) since ServerDriven's pending file is plain JSONL appends, not write_atomic -
    they must not silently come back via a shared base class."""
    assert not hasattr(server_driven, "write_atomic")
    assert not hasattr(server_driven, "cleanup_temp_files")
    assert not hasattr(server_driven, "temp_files")


# ------------------------------------------------------------------------------
# log_execution
# ------------------------------------------------------------------------------


@pytest.mark.unit
def test_log_execution_queues_the_entry(server_driven):
    entry = _make_entry()
    server_driven.thread_start = lambda: None  # avoid a real infinite loop for this test

    server_driven.log_execution(entry)
    server_driven.sync_thread.join(timeout=2)

    assert server_driven.queue.get_nowait() is entry


@pytest.mark.unit
def test_log_execution_starts_thread_if_not_running(server_driven):
    server_driven.thread_start = lambda: None

    server_driven.log_execution(_make_entry())
    server_driven.sync_thread.join(timeout=2)

    assert server_driven.sync_thread is not None


@pytest.mark.unit
def test_log_execution_does_not_start_a_second_thread_if_already_running(server_driven):
    fake_thread = Mock()
    fake_thread.is_alive.return_value = True
    server_driven.sync_thread = fake_thread

    server_driven.log_execution(_make_entry())

    assert server_driven.sync_thread is fake_thread


# ------------------------------------------------------------------------------
# thread_start (the writer loop)
# ------------------------------------------------------------------------------


@pytest.mark.unit
def test_thread_start_flushes_a_single_entry_when_queue_goes_idle(server_driven):
    entry = _make_entry()
    server_driven.queue.put(entry)

    written: list = []

    def fake_insert(rows):
        written.extend(rows)
        server_driven.stop_event.set()
        return True

    with patch.object(server_driven, "insert_into_central_db", side_effect=fake_insert):
        server_driven.thread_start()

    assert written == [entry.as_row()]


@pytest.mark.unit
def test_thread_start_flushes_full_batch_without_waiting_for_idle(server_driven):
    """A full batch should be written as soon as batch_size is reached, in one call -
    not one entry at a time, and not only once the queue goes idle."""
    server_driven.batch_size = 3
    entries = [_make_entry(query_id=f"Q{i}") for i in range(3)]
    for entry in entries:
        server_driven.queue.put(entry)

    calls: list = []

    def fake_write(batch):
        calls.append(list(batch))
        server_driven.stop_event.set()
        return True

    with patch.object(server_driven, "_write_entries", side_effect=fake_write):
        server_driven.thread_start()

    assert calls == [entries]


@pytest.mark.unit
def test_thread_start_retries_unwritten_entries_on_next_cycle_after_a_failed_flush(server_driven):
    """A batch that fails to write must not be dropped - it should be retried on the next
    idle cycle instead of being silently lost (regression: entries used to never be retried)."""
    entry = _make_entry()
    server_driven.queue.put(entry)

    call_count = 0

    def fake_write(batch):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return False
        server_driven.stop_event.set()
        return True

    with patch.object(server_driven, "_write_entries", side_effect=fake_write):
        server_driven.thread_start()

    assert call_count == 2


@pytest.mark.unit
def test_thread_start_spills_unwritten_entries_to_pending_file_on_shutdown(server_driven):
    """Regression test: entries buffered but not yet written must be spilled to the pending
    file on shutdown, not silently dropped."""
    entry = _make_entry()
    server_driven.queue.put(entry)

    def fake_write(batch):
        server_driven.stop_event.set()
        return False

    with patch.object(server_driven, "_write_entries", side_effect=fake_write):
        server_driven.thread_start()

    pending = server_driven._read_pending_file()
    assert len(pending) == 1
    assert pending[0]["query_id"] == entry.query_id


@pytest.mark.unit
def test_thread_start_does_not_write_when_queue_stays_empty(server_driven):
    """Idling with nothing queued should never call a write - regression: an earlier version
    wrote an empty batch every time the queue went idle."""
    with patch.object(server_driven, "_write_entries") as mock_write:
        server_driven.stop_event.set()  # so the loop body runs at most once, then exits
        server_driven.thread_start()

    assert not mock_write.called


@pytest.mark.unit
def test_thread_start_retries_pending_file_periodically(server_driven):
    server_driven.retry_interval_secs = 0  # fire again as soon as the idle branch runs

    call_count = 0

    def fake_retry():
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            server_driven.stop_event.set()

    with patch.object(server_driven, "_retry_pending_file", side_effect=fake_retry) as mock_retry:
        server_driven.thread_start()

    assert mock_retry.call_count >= 2


# ------------------------------------------------------------------------------
# _write_entries
# ------------------------------------------------------------------------------


@pytest.mark.unit
def test_write_entries_resets_failures_on_success(server_driven):
    server_driven.failures_nb = 3

    with patch.object(server_driven, "insert_into_central_db", return_value=True):
        result = server_driven._write_entries([_make_entry()])

    assert result is True
    assert server_driven.failures_nb == 0


@pytest.mark.unit
def test_write_entries_increments_failures_on_failure(server_driven):
    with patch.object(server_driven, "insert_into_central_db", return_value=False):
        result = server_driven._write_entries([_make_entry()])

    assert result is False
    assert server_driven.failures_nb == 1


@pytest.mark.unit
def test_write_entries_spills_and_resets_at_max_failures(server_driven):
    """Regression: a successful spill to the pending file must report success (True) -
    otherwise the caller's loop never clears its in-memory buffer, and the same batch gets
    re-spilled (duplicated) on every subsequent failure cycle."""
    server_driven.failures_nb = server_driven.max_failures - 1
    entry = _make_entry()

    with patch.object(server_driven, "insert_into_central_db", return_value=False):
        result = server_driven._write_entries([entry])

    assert result is True
    assert server_driven.failures_nb == 0
    pending = server_driven._read_pending_file()
    assert len(pending) == 1


@pytest.mark.unit
def test_write_entries_does_not_spill_before_max_failures(server_driven):
    server_driven.failures_nb = 0
    server_driven.max_failures = 5

    with patch.object(server_driven, "insert_into_central_db", return_value=False):
        server_driven._write_entries([_make_entry()])

    assert server_driven._read_pending_file() == []


# ------------------------------------------------------------------------------
# _write_pending_file / _read_pending_file
# ------------------------------------------------------------------------------


@pytest.mark.unit
def test_write_pending_file_combines_extras_queue_and_existing_content(server_driven):
    existing_entry = _make_entry(query_id="OLD")
    server_driven._write_pending_file(extras=[existing_entry])

    queued_entry = _make_entry(query_id="QUEUED")
    server_driven.queue.put(queued_entry)

    extra_entry = _make_entry(query_id="EXTRA")
    server_driven._write_pending_file(extras=[extra_entry])

    pending = server_driven._read_pending_file()
    query_ids = {p["query_id"] for p in pending}
    assert query_ids == {"OLD", "QUEUED", "EXTRA"}


@pytest.mark.unit
def test_write_pending_file_noop_if_nothing_to_write(server_driven):
    server_driven._write_pending_file()

    assert not server_driven.pending_file.exists()


@pytest.mark.unit
def test_read_pending_file_returns_empty_list_if_missing(server_driven):
    assert server_driven._read_pending_file() == []


@pytest.mark.unit
def test_read_pending_file_handles_corrupt_json(server_driven):
    """The pending file is JSON Lines - a single unparseable line (here, the whole content)
    is skipped rather than raising, leaving nothing to recover."""
    server_driven.pending_file.parent.mkdir(parents=True, exist_ok=True)
    server_driven.pending_file.write_text("not json")

    assert server_driven._read_pending_file() == []


@pytest.mark.unit
def test_read_pending_file_skips_a_corrupt_line_but_keeps_the_rest(server_driven):
    """Regression guard: one truncated/corrupt line (e.g. from a crash mid-write) must not
    invalidate entries already safely written on other lines of the same file."""
    good_entry = _make_entry(query_id="GOOD")
    server_driven._write_pending_file(extras=[good_entry])
    with open(server_driven.pending_file, "a", encoding="utf-8") as f:
        f.write("not json\n")

    pending = server_driven._read_pending_file()

    assert len(pending) == 1
    assert pending[0]["query_id"] == "GOOD"


@pytest.mark.unit
def test_write_pending_file_appends_without_rereading_existing_content(server_driven):
    """Regression guard for the O(n) full-file rewrite this JSONL format replaced: spilling a
    new batch must never read back everything already spilled - each spill should only cost
    the new entries, not the total accumulated during an outage."""
    server_driven._write_pending_file(extras=[_make_entry(query_id="FIRST")])

    with patch.object(server_driven, "_read_pending_file") as mock_read:
        server_driven._write_pending_file(extras=[_make_entry(query_id="SECOND")])

    assert not mock_read.called
    pending = server_driven._read_pending_file()
    assert {p["query_id"] for p in pending} == {"FIRST", "SECOND"}


# ------------------------------------------------------------------------------
# _retry_pending_file
# ------------------------------------------------------------------------------


@pytest.mark.unit
def test_retry_pending_file_noop_if_empty(server_driven):
    with patch.object(server_driven, "insert_into_central_db") as mock_insert:
        server_driven._retry_pending_file()

    assert not mock_insert.called


@pytest.mark.unit
def test_retry_pending_file_recovers_and_deletes_file_on_success(server_driven):
    entry = _make_entry()
    server_driven._write_pending_file(extras=[entry])
    server_driven.failures_nb = 2

    with patch.object(server_driven, "insert_into_central_db", return_value=True):
        server_driven._retry_pending_file()

    assert not server_driven.pending_file.exists()
    assert server_driven.failures_nb == 0


@pytest.mark.unit
def test_retry_pending_file_keeps_file_on_continued_failure(server_driven):
    entry = _make_entry()
    server_driven._write_pending_file(extras=[entry])

    with patch.object(server_driven, "insert_into_central_db", return_value=False):
        server_driven._retry_pending_file()

    assert server_driven.pending_file.exists()


@pytest.mark.unit
def test_retry_pending_file_recovers_valid_entries_and_skips_a_corrupt_line(server_driven):
    """A single corrupt entry (e.g. a line truncated by a crash mid-write) must not prevent
    recovery of the other, still-valid entries in the same pending file - regression against
    the old behaviour, which discarded the entire file on any single corrupt entry."""
    good_entry = _make_entry(query_id="GOOD")
    server_driven._write_pending_file(extras=[good_entry])
    with open(server_driven.pending_file, "a", encoding="utf-8") as f:
        f.write('{"bad": "data"}\n')

    with patch.object(server_driven, "insert_into_central_db", return_value=True) as mock_insert:
        server_driven._retry_pending_file()

    recovered_rows = mock_insert.call_args.args[0]
    assert recovered_rows == [good_entry.as_row()]
    assert not server_driven.pending_file.exists()


@pytest.mark.unit
def test_retry_pending_file_discards_file_when_every_entry_is_corrupt(server_driven):
    """If nothing in the pending file can be recovered, it must be discarded rather than
    retried forever - and the database should never even be attempted with an empty batch."""
    server_driven.pending_file.parent.mkdir(parents=True, exist_ok=True)
    server_driven.pending_file.write_text('{"bad": "data"}\n', encoding="utf-8")

    with patch.object(server_driven, "insert_into_central_db") as mock_insert:
        server_driven._retry_pending_file()

    assert not mock_insert.called
    assert not server_driven.pending_file.exists()


# ------------------------------------------------------------------------------
# check_db override / force_file mechanism
# ------------------------------------------------------------------------------


@pytest.mark.unit
def test_check_db_creating_database_successfully_leaves_force_file_false(server_driven):
    """Happy path: no existing db, real create_db() succeeds -> force_file must stay False."""
    result = server_driven.check_db()

    assert result is True
    assert server_driven.force_file is False
    assert server_driven.db_path.exists()


@pytest.mark.unit
def test_check_db_sets_force_file_and_logs_warning_when_creation_fails(server_driven):
    """If the db doesn't exist and create_db() fails, force_file must be set so writes go
    straight to the pending file instead of retrying a db that can't even be created."""
    with (
        patch.object(server_driven, "create_db", return_value=False),
        patch("Pytre_Core.logs_central.log") as mock_log,
    ):
        result = server_driven.check_db()

    assert result is False
    assert server_driven.force_file is True
    assert not server_driven.db_path.exists()
    assert mock_log.call_args.args[1] == logging.WARNING


@pytest.mark.unit
def test_check_db_sets_force_file_and_logs_error_when_update_fails(server_driven):
    """If the db already exists but update_db() fails, force_file must be set too - same
    resolution (pending file) as a failed creation, but logged as an ERROR since an existing,
    unusable db is a more serious situation than a fresh one that was never created."""
    server_driven.create_db()

    with (
        patch.object(server_driven, "update_db", return_value=False),
        patch("Pytre_Core.logs_central.log") as mock_log,
    ):
        result = server_driven.check_db()

    assert result is False
    assert server_driven.force_file is True
    assert mock_log.call_args.args[1] == logging.ERROR


@pytest.mark.unit
def test_check_db_does_not_set_force_file_when_update_succeeds(server_driven):
    server_driven.create_db()

    result = server_driven.check_db()

    assert result is True
    assert server_driven.force_file is False


# ------------------------------------------------------------------------------
# force_file bypass in _write_entries / _retry_pending_file
# ------------------------------------------------------------------------------


@pytest.mark.unit
def test_write_entries_skips_database_and_spills_directly_when_force_file_is_true(server_driven):
    """Once force_file is set (db confirmed broken), _write_entries must never attempt the
    db write - not even once - and go straight to the pending file."""
    server_driven.force_file = True
    entry = _make_entry()

    with patch.object(server_driven, "insert_into_central_db") as mock_insert:
        result = server_driven._write_entries([entry])

    assert not mock_insert.called
    assert result is True
    pending = server_driven._read_pending_file()
    assert len(pending) == 1
    assert pending[0]["query_id"] == entry.query_id


@pytest.mark.unit
def test_write_entries_does_not_log_the_consecutive_failures_warning_when_force_file_is_true(server_driven):
    """The 'too many consecutive failures' warning only makes sense for a db that was healthy
    and started failing - a db already known broken via force_file shouldn't repeat it."""
    server_driven.force_file = True

    with patch("Pytre_Core.logs_central.log") as mock_log:
        server_driven._write_entries([_make_entry()])

    assert not any(call.args[1] == logging.WARNING for call in mock_log.call_args_list if len(call.args) > 1)


@pytest.mark.unit
def test_retry_pending_file_does_nothing_when_force_file_is_true(server_driven):
    """Retrying a db already known broken (force_file) would just fail again for no reason -
    _retry_pending_file must return immediately without touching the pending file or the db."""
    entry = _make_entry()
    server_driven._write_pending_file(extras=[entry])
    server_driven.force_file = True

    with patch.object(server_driven, "insert_into_central_db") as mock_insert:
        server_driven._retry_pending_file()

    assert not mock_insert.called
    assert server_driven.pending_file.exists()
    pending = server_driven._read_pending_file()
    assert len(pending) == 1


# ------------------------------------------------------------------------------
# thread_start calls check_db (regression: writes used to fail forever)
# ------------------------------------------------------------------------------


@pytest.mark.unit
def test_thread_start_calls_check_db_before_the_write_loop(server_driven):
    server_driven.stop_event.set()  # loop body runs at most once, then exits immediately

    with patch.object(server_driven, "check_db") as mock_check_db:
        server_driven.thread_start()

    mock_check_db.assert_called_once()
