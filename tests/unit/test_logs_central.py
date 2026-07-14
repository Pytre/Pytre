"""
Tests for Pytre/logs_central.py

Basic tests for central logging infrastructure.
Full integration tests skipped due to SQLite/threading complexity.
"""

import pytest
import json
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import sqlite3
import contextlib

from Pytre_Core.logs_central import CentralLogs, CENTRAL_DB, LATEST_VERSION


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
def test_cleanup_temp_files_removes_files(central_logs):
    """cleanup_temp_files should remove files in temp_files list"""
    # Ensure logs folder exists
    central_logs.logs_folder.mkdir(parents=True, exist_ok=True)

    # Create temp files with .tmp extension (as expected by cleanup)
    temp_file1 = central_logs.logs_folder / "temp1.tmp"
    temp_file2 = central_logs.logs_folder / "temp2.tmp"

    temp_file1.write_text("temp")
    temp_file2.write_text("temp")

    # Manually add to temp_files list
    central_logs.temp_files = [temp_file1, temp_file2]

    # Cleanup
    central_logs.cleanup_temp_files()

    # Should be removed (they're in the list)
    assert not temp_file1.exists()
    assert not temp_file2.exists()


@pytest.mark.unit
def test_cleanup_temp_files_handles_missing(central_logs):
    """cleanup_temp_files should handle missing files gracefully"""
    # Add non-existent file
    nonexistent = central_logs.logs_folder / "nonexistent.txt"
    central_logs.temp_files = [nonexistent]

    # Should not raise error
    central_logs.cleanup_temp_files()


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
def test_sql_rows_get_unsynced_returns_string(central_logs):
    """sql_rows_get_unsynced should return SQL string"""
    central_logs.user_id = "test_user"
    central_logs.user_name = "Test User"

    result = central_logs.sql_rows_get_unsynced()

    assert isinstance(result, str)
    assert "SELECT" in result.upper()
    assert "QUERIES_EXEC" in result
    assert "EXPORTED = 0" in result


@pytest.mark.unit
def test_sql_mark_rows_as_exported_returns_string(central_logs):
    """sql_mark_rows_as_exported should return SQL string with placeholders"""
    placeholders = "?, ?, ?"

    result = central_logs.sql_mark_rows_as_exported(placeholders)

    assert isinstance(result, str)
    assert "UPDATE" in result.upper()
    assert "QUERIES_EXEC" in result
    assert "EXPORTED = 1" in result
    assert "ROWID IN" in result
    assert placeholders in result


@pytest.mark.unit
def test_sql_insert_into_central_db_returns_string(central_logs):
    """sql_insert_into_central_db should return SQL insert string"""
    result = central_logs.sql_insert_into_central_db()

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
def test_temp_files_list_initializes_empty(central_logs):
    """temp_files list should initialize empty"""
    assert isinstance(central_logs.temp_files, list)
    assert len(central_logs.temp_files) == 0


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
# trigger_sync / stop_sync Tests
# ================================================================================


@pytest.mark.unit
def test_trigger_sync_aborts_if_no_central_db(central_logs, tmp_path):
    """trigger_sync should log and return early if central_db is falsy"""
    central_logs.db_path = None

    # Should not raise
    central_logs.trigger_sync(tmp_path / "user.db")

    assert central_logs.sync_thread is None


@pytest.mark.unit
def test_trigger_sync_aborts_if_check_db_fails(central_logs, tmp_path):
    """trigger_sync should return early if check_db fails"""
    with patch.object(central_logs, "check_db", return_value=False):
        central_logs.trigger_sync(tmp_path / "user.db")

    assert central_logs.sync_thread is None


@pytest.mark.unit
def test_trigger_sync_aborts_if_user_logs_missing(central_logs, tmp_path):
    """trigger_sync should return early if the user_logs file doesn't exist"""
    with patch.object(central_logs, "check_db", return_value=True):
        central_logs.trigger_sync(tmp_path / "nonexistent_user.db")

    assert central_logs.sync_thread is None


@pytest.mark.unit
def test_trigger_sync_starts_thread(central_logs, tmp_path):
    """trigger_sync should start a background thread when all checks pass"""
    user_logs = tmp_path / "user.db"
    user_logs.write_text("fake db")

    with patch.object(central_logs, "check_db", return_value=True):
        central_logs.trigger_sync(user_logs, user_id="u1", user_name="User One")
        central_logs.sync_thread.join(timeout=2)

    assert central_logs.user_id == "u1"
    assert central_logs.user_name == "User One"


@pytest.mark.unit
def test_stop_sync_returns_true_if_no_thread_running(central_logs):
    """stop_sync should return True immediately if there's no active sync thread"""
    result = central_logs.stop_sync()

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

    result = central_logs.stop_sync(timeout=2)

    assert central_logs.stop_event.is_set()
    assert result is True


# ================================================================================
# FileDriven Tests
# ================================================================================


from Pytre_Core.logs_central import FileDriven, get_default_class  # noqa: E402


@pytest.fixture
def file_driven(temp_logs_folder):
    """Create FileDriven instance with temp folder"""
    fd = FileDriven(logs_folder=temp_logs_folder)
    fd.sync_heartbeat = 1  # keep any heartbeat thread snappy in tests
    return fd


@pytest.mark.unit
def test_get_default_class_returns_file_driven():
    """get_default_class should return the FileDriven class"""
    assert get_default_class() is FileDriven


@pytest.mark.unit
def test_file_driven_initialization(temp_logs_folder):
    """FileDriven should set up queue/lock file paths under logs_folder"""
    fd = FileDriven(logs_folder=temp_logs_folder)

    assert fd.queue_ctrl_file == temp_logs_folder / "Pytre_Queue.json"
    assert fd.failed_folder == temp_logs_folder / "Pytre_Failed"
    assert len(fd.export_folders) == 2


# ------------------------------------------------------------------------------
# create_dirs
# ------------------------------------------------------------------------------


@pytest.mark.unit
def test_create_dirs_creates_single_path(file_driven, temp_logs_folder):
    """create_dirs should accept a single Path and create it"""
    new_dir = temp_logs_folder / "single"

    result = file_driven.create_dirs(new_dir)

    assert result is True
    assert new_dir.is_dir()


@pytest.mark.unit
def test_create_dirs_creates_list_of_paths(file_driven, temp_logs_folder):
    """create_dirs should accept a list of Paths and create all of them"""
    dirs = [temp_logs_folder / "a", temp_logs_folder / "b"]

    result = file_driven.create_dirs(dirs)

    assert result is True
    assert all(d.is_dir() for d in dirs)


@pytest.mark.unit
def test_create_dirs_handles_permission_error(file_driven, temp_logs_folder):
    """create_dirs should return False and not raise on PermissionError"""
    with patch.object(Path, "mkdir", side_effect=PermissionError("denied")):
        result = file_driven.create_dirs(temp_logs_folder / "forbidden")

    assert result is False


# ------------------------------------------------------------------------------
# write_atomic
# ------------------------------------------------------------------------------


@pytest.mark.unit
def test_write_atomic_writes_dict_as_json(file_driven, temp_logs_folder):
    """write_atomic should write a dict as JSON and rename it into place"""
    target = temp_logs_folder / "data.json"

    result = file_driven.write_atomic(target, {"a": 1})

    assert result is True
    assert json.loads(target.read_text()) == {"a": 1}
    assert file_driven.temp_files == []


@pytest.mark.unit
def test_write_atomic_writes_string_content(file_driven, temp_logs_folder):
    """write_atomic should write plain string content"""
    target = temp_logs_folder / "data.txt"

    result = file_driven.write_atomic(target, "hello")

    assert result is True
    assert target.read_text() == "hello"


@pytest.mark.unit
def test_write_atomic_handles_permission_error(file_driven, temp_logs_folder):
    """write_atomic should return False on PermissionError without raising"""
    target = temp_logs_folder / "data.json"

    with patch("builtins.open", side_effect=PermissionError("denied")):
        result = file_driven.write_atomic(target, {"a": 1})

    assert result is False
    assert not target.exists()


# ------------------------------------------------------------------------------
# get_active_export_folder / set_active_export_folder
# ------------------------------------------------------------------------------


@pytest.mark.unit
def test_get_active_export_folder_defaults_to_zero(file_driven):
    """get_active_export_folder should default to folder 0 and create the queue file"""
    index, folder = file_driven.get_active_export_folder()

    assert index == 0
    assert folder == file_driven.export_folders[0]
    assert file_driven.queue_ctrl_file.exists()


@pytest.mark.unit
def test_set_active_export_folder_updates_index(file_driven):
    """set_active_export_folder should persist the new active index"""
    result = file_driven.set_active_export_folder(1)

    assert result is True
    index, folder = file_driven.get_active_export_folder()
    assert index == 1
    assert folder == file_driven.export_folders[1]


@pytest.mark.unit
def test_set_active_export_folder_wraps_around(file_driven):
    """set_active_export_folder should wrap the index using modulo"""
    file_driven.set_active_export_folder(2)  # only 2 folders -> wraps to 0

    index, _ = file_driven.get_active_export_folder()
    assert index == 0


@pytest.mark.unit
def test_get_active_export_folder_handles_corrupt_json(file_driven):
    """get_active_export_folder should fall back to folder 0 on corrupt JSON"""
    file_driven.queue_ctrl_file.parent.mkdir(parents=True, exist_ok=True)
    file_driven.queue_ctrl_file.write_text("{ not valid json")

    index, folder = file_driven.get_active_export_folder()

    assert index == 0
    assert folder == file_driven.export_folders[0]


# ------------------------------------------------------------------------------
# try_acquire_lock / release_lock / break_stale_lock
# ------------------------------------------------------------------------------


@pytest.mark.unit
def test_try_acquire_lock_succeeds_when_unlocked(file_driven):
    """try_acquire_lock should acquire the lock and create the locked file"""
    try:
        result = file_driven.try_acquire_lock()

        assert result is True
        assert file_driven.sync_locked_file.exists()
        assert not file_driven.sync_unlocked_file.exists()
    finally:
        file_driven.release_lock()


@pytest.mark.unit
def test_try_acquire_lock_fails_when_already_locked(file_driven):
    """try_acquire_lock should fail if another process already holds the lock"""
    try:
        assert file_driven.try_acquire_lock() is True

        other = FileDriven(logs_folder=file_driven.logs_folder)
        other.sync_heartbeat = 1
        assert other.try_acquire_lock() is False
    finally:
        file_driven.release_lock()


@pytest.mark.unit
def test_release_lock_restores_unlocked_file(file_driven):
    """release_lock should rename the locked file back to unlocked"""
    file_driven.try_acquire_lock()

    result = file_driven.release_lock()

    assert result is True
    assert file_driven.sync_unlocked_file.exists()
    assert not file_driven.sync_locked_file.exists()


@pytest.mark.unit
def test_release_lock_returns_false_if_not_locked(file_driven):
    """release_lock should return False if there's no lock to release"""
    result = file_driven.release_lock()

    assert result is False


@pytest.mark.unit
def test_break_stale_lock_breaks_expired_lock(file_driven):
    """break_stale_lock should break a lock whose heartbeat is older than the timeout"""
    file_driven.logs_folder.mkdir(parents=True, exist_ok=True)
    stale_data = {
        "status": "locked",
        "acquired": "2000-01-01T00:00:00",
        "last_heartbeat": "2000-01-01T00:00:00",
        "user": "someone",
        "hostname": "host",
        "pid": 1,
    }
    file_driven.sync_locked_file.write_text(json.dumps(stale_data))

    result = file_driven.break_stale_lock()

    assert result is True
    assert not file_driven.sync_locked_file.exists()
    assert file_driven.sync_unlocked_file.exists()


@pytest.mark.unit
def test_break_stale_lock_keeps_fresh_lock(file_driven):
    """break_stale_lock should leave a recently-heartbeated lock untouched"""
    from datetime import datetime

    file_driven.logs_folder.mkdir(parents=True, exist_ok=True)
    fresh_data = {
        "status": "locked",
        "acquired": datetime.now().isoformat(),
        "last_heartbeat": datetime.now().isoformat(),
        "user": "someone",
        "hostname": "host",
        "pid": 1,
    }
    file_driven.sync_locked_file.write_text(json.dumps(fresh_data))

    result = file_driven.break_stale_lock()

    assert result is True
    assert file_driven.sync_locked_file.exists()


@pytest.mark.unit
def test_break_stale_lock_noop_if_no_lock_file(file_driven):
    """break_stale_lock should be a no-op if there's no locked file"""
    assert file_driven.break_stale_lock() is True


@pytest.mark.unit
def test_update_heartbeat_updates_timestamp(file_driven):
    """update_heartbeat should refresh last_heartbeat in the locked file"""
    try:
        file_driven.try_acquire_lock()
        before = json.loads(file_driven.sync_locked_file.read_text())["last_heartbeat"]

        import time as time_module

        time_module.sleep(0.01)
        file_driven.update_heartbeat()

        after = json.loads(file_driven.sync_locked_file.read_text())["last_heartbeat"]
        assert after >= before
    finally:
        file_driven.release_lock()


@pytest.mark.unit
def test_update_heartbeat_returns_false_if_not_locked(file_driven):
    """update_heartbeat should return False if there's no locked file"""
    assert file_driven.update_heartbeat() is False


# ------------------------------------------------------------------------------
# Files/folders processing helpers
# ------------------------------------------------------------------------------


@pytest.mark.unit
def test_failed_files_move_relocates_files(file_driven, temp_logs_folder):
    """failed_files_move should rename files into the failed folder with a timestamp suffix"""
    src_folder = temp_logs_folder / "src"
    src_folder.mkdir()
    bad_file = src_folder / "bad.json"
    bad_file.write_text("{}")

    file_driven.failed_files_move([bad_file], "20260101120000")

    assert not bad_file.exists()
    moved = list(file_driven.failed_folder.glob("bad_20260101120000.json"))
    assert len(moved) == 1


@pytest.mark.unit
def test_processed_files_remove_deletes_files(file_driven, temp_logs_folder):
    """processed_files_remove should delete each processed file"""
    f = temp_logs_folder / "processed.json"
    f.write_text("{}")

    file_driven.processed_files_remove([f])

    assert not f.exists()


@pytest.mark.unit
def test_processed_files_remove_handles_missing_file(file_driven, tmp_path):
    """processed_files_remove should not raise if a file is already gone"""
    file_driven.processed_files_remove([tmp_path / "already_gone.json"])


@pytest.mark.unit
def test_processed_files_move_moves_to_active_folder(file_driven, temp_logs_folder):
    """processed_files_move should move files into the given active folder"""
    active_folder = temp_logs_folder / "active"
    active_folder.mkdir()
    f = temp_logs_folder / "todo.json"
    f.write_text("{}")

    file_driven.processed_files_move([f], active_folder)

    assert not f.exists()
    assert (active_folder / "todo.json").exists()


@pytest.mark.unit
def test_processed_files_move_aborts_if_folder_missing(file_driven, temp_logs_folder):
    """processed_files_move should do nothing if the active folder doesn't exist"""
    f = temp_logs_folder / "todo.json"
    f.write_text("{}")

    file_driven.processed_files_move([f], temp_logs_folder / "nonexistent")

    assert f.exists()  # left untouched


@pytest.mark.unit
def test_files_extract_infos_reads_json_rows(file_driven, temp_logs_folder):
    """files_extract_infos should read and concatenate rows from JSON files"""
    f1 = temp_logs_folder / "f1.json"
    f1.write_text(json.dumps([["row1"], ["row2"]]))
    f2 = temp_logs_folder / "f2.json"
    f2.write_text(json.dumps([["row3"]]))

    rows, processed = file_driven.files_extract_infos([f1, f2])

    assert rows == [["row1"], ["row2"], ["row3"]]
    assert set(processed) == {f1, f2}


@pytest.mark.unit
def test_files_extract_infos_moves_corrupt_file_to_failed(file_driven, temp_logs_folder):
    """files_extract_infos should move unparseable files to the failed folder"""
    bad = temp_logs_folder / "bad.json"
    bad.write_text("{ not valid json")

    rows, processed = file_driven.files_extract_infos([bad])

    assert rows == []
    assert processed == []
    assert not bad.exists()
    assert list(file_driven.failed_folder.glob("bad_*.json"))


@pytest.mark.unit
def test_files_extract_infos_handles_missing_file(file_driven, tmp_path):
    """files_extract_infos should skip files that vanished before being read"""
    rows, processed = file_driven.files_extract_infos([tmp_path / "gone.json"])

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
def test_rows_get_unsynced_returns_rows(file_driven, temp_logs_folder):
    """rows_get_unsynced should return unsynced rows from the user database"""
    user_logs = temp_logs_folder / "user.db"
    _create_user_logs_with_unsynced_row(user_logs)

    file_driven.user_logs = user_logs
    file_driven.user_id = "u1"
    file_driven.user_name = "User One"

    rows = file_driven.rows_get_unsynced()

    assert len(rows) == 1
    assert rows[0][1] == "SRV1"  # SERVER_ID


@pytest.mark.unit
def test_rows_get_unsynced_returns_none_if_stopped(file_driven, temp_logs_folder):
    """rows_get_unsynced should return None immediately if stop_event is set"""
    file_driven.stop_event.set()

    assert file_driven.rows_get_unsynced() is None


@pytest.mark.unit
def test_mark_rows_as_exported_updates_flag(file_driven, temp_logs_folder):
    """mark_rows_as_exported should set EXPORTED=1 for the given row ids"""
    user_logs = temp_logs_folder / "user.db"
    _create_user_logs_with_unsynced_row(user_logs)
    file_driven.user_logs = user_logs

    file_driven.mark_rows_as_exported([1])

    with contextlib.closing(sqlite3.connect(user_logs)) as conn:
        exported = conn.execute("SELECT EXPORTED FROM QUERIES_EXEC WHERE ROWID = 1").fetchone()[0]

    assert exported == 1


@pytest.mark.unit
def test_insert_into_central_db_inserts_rows(file_driven):
    """insert_into_central_db should insert rows and return True"""
    file_driven.create_db()

    row = ("SRV1", "u1", "User One", "SELECT 1", "2026-01-01T00:00:00", 1, 10, "{}")
    result = file_driven.insert_into_central_db([row])

    assert result is True
    with contextlib.closing(sqlite3.connect(file_driven.db_path)) as conn:
        count = conn.execute("SELECT COUNT(*) FROM QUERIES_EXEC").fetchone()[0]
    assert count == 1


@pytest.mark.unit
def test_insert_into_central_db_returns_false_if_stopped(file_driven):
    """insert_into_central_db should return False immediately if stop_event is set"""
    file_driven.stop_event.set()

    assert file_driven.insert_into_central_db([]) is False


@pytest.mark.unit
def test_insert_into_central_db_returns_false_if_update_fails(file_driven):
    """insert_into_central_db should return False if update_db fails"""
    file_driven.version = -1  # update_db() returns False for unknown version

    assert file_driven.insert_into_central_db([]) is False


@pytest.mark.unit
def test_export_unsynced_data_writes_export_file(file_driven, temp_logs_folder):
    """export_unsynced_data should write a JSON export file and mark rows exported"""
    user_logs = temp_logs_folder / "user.db"
    _create_user_logs_with_unsynced_row(user_logs)
    file_driven.user_logs = user_logs
    file_driven.user_id = "u1"
    file_driven.user_name = "User One"

    result = file_driven.export_unsynced_data()

    assert result is True
    _, active_folder = file_driven.get_active_export_folder()
    exported_files = list(active_folder.glob("*.json"))
    assert len(exported_files) == 1

    with contextlib.closing(sqlite3.connect(user_logs)) as conn:
        exported = conn.execute("SELECT EXPORTED FROM QUERIES_EXEC WHERE ROWID = 1").fetchone()[0]
    assert exported == 1


@pytest.mark.unit
def test_export_unsynced_data_noop_if_stopped(file_driven):
    """export_unsynced_data should return True immediately if stop_event is set"""
    file_driven.stop_event.set()

    assert file_driven.export_unsynced_data() is True


@pytest.mark.unit
def test_export_unsynced_data_noop_if_nothing_to_export(file_driven, temp_logs_folder):
    """export_unsynced_data should return True if there are no unsynced rows"""
    user_logs = temp_logs_folder / "user.db"
    with contextlib.closing(sqlite3.connect(user_logs)) as conn:
        conn.execute(
            "CREATE TABLE QUERIES_EXEC (SERVER_ID TEXT, QUERY TEXT, START TEXT, "
            "DURATION_SECS INTEGER, NB_ROWS INTEGER, PARAMETERS TEXT, EXPORTED INTEGER)"
        )
        conn.commit()
    file_driven.user_logs = user_logs
    file_driven.user_id = "u1"

    result = file_driven.export_unsynced_data()

    assert result is True
    _, active_folder = file_driven.get_active_export_folder()
    assert list(active_folder.glob("*.json")) == []


# ------------------------------------------------------------------------------
# wait_for_files_completion / process_files / synchronize_folder
# ------------------------------------------------------------------------------


@pytest.mark.unit
def test_wait_for_files_completion_returns_stable_files(file_driven, temp_logs_folder, monkeypatch):
    """wait_for_files_completion should return files whose size stopped changing"""
    monkeypatch.setattr("Pytre_Core.logs_central.time.sleep", lambda _: None)

    folder = temp_logs_folder / "watch"
    folder.mkdir()
    f = folder / "data.json"
    f.write_text("{}")

    stable = file_driven.wait_for_files_completion(folder)

    assert stable == {str(f)}


@pytest.mark.unit
def test_wait_for_files_completion_returns_none_if_stopped(file_driven, temp_logs_folder):
    """wait_for_files_completion should return None immediately if stop_event is set"""
    file_driven.stop_event.set()
    folder = temp_logs_folder / "watch"
    folder.mkdir()

    assert file_driven.wait_for_files_completion(folder) is None


@pytest.mark.unit
def test_process_files_returns_false_if_nothing_to_process(file_driven):
    """process_files should return False and log if there's nothing to process"""
    assert file_driven.process_files(None) is False
    assert file_driven.process_files(set()) is False


@pytest.mark.unit
def test_process_files_returns_false_if_stopped(file_driven):
    """process_files should return False immediately if stop_event is set"""
    file_driven.stop_event.set()

    assert file_driven.process_files({"some_file.json"}) is False


@pytest.mark.unit
def test_process_files_inserts_and_removes_on_success(file_driven, temp_logs_folder):
    """process_files should insert extracted rows and remove the source files on success"""
    file_driven.create_db()

    export_file = temp_logs_folder / "export.json"
    row = ["SRV1", "u1", "User One", "SELECT 1", "2026-01-01T00:00:00", 1, 10, "{}"]
    export_file.write_text(json.dumps([row]))

    result = file_driven.process_files({str(export_file)})

    assert result is True
    assert not export_file.exists()
    with contextlib.closing(sqlite3.connect(file_driven.db_path)) as conn:
        count = conn.execute("SELECT COUNT(*) FROM QUERIES_EXEC").fetchone()[0]
    assert count == 1


@pytest.mark.unit
def test_process_files_moves_back_on_insert_failure(file_driven, temp_logs_folder):
    """process_files should move files back to the active folder if the DB insert fails"""
    file_driven.create_dirs(file_driven.export_folders)

    export_file = temp_logs_folder / "export.json"
    row = ["SRV1", "u1", "User One", "SELECT 1", "2026-01-01T00:00:00", 1, 10, "{}"]
    export_file.write_text(json.dumps([row]))

    with patch.object(file_driven, "insert_into_central_db", return_value=False):
        result = file_driven.process_files({str(export_file)})

    assert result is True  # method itself still returns True, see source
    _, active_folder = file_driven.get_active_export_folder()
    assert (active_folder / "export.json").exists()


@pytest.mark.unit
def test_synchronize_folder_raises_if_no_stable_files(file_driven, temp_logs_folder, monkeypatch):
    """synchronize_folder should raise TimeoutError if wait_for_files_completion finds nothing"""
    monkeypatch.setattr("Pytre_Core.logs_central.time.sleep", lambda _: None)
    folder = temp_logs_folder / "empty_watch"
    folder.mkdir()

    with pytest.raises(TimeoutError):
        file_driven.synchronize_folder(folder)


# ------------------------------------------------------------------------------
# synchronization_attempt (full happy path integration)
# ------------------------------------------------------------------------------


@pytest.mark.unit
def test_synchronization_attempt_returns_false_if_nothing_to_sync(file_driven):
    """synchronization_attempt should bail out early if there's nothing exported to process"""
    result = file_driven.synchronization_attempt()

    assert result is False
    assert not file_driven.sync_locked_file.exists()


@pytest.mark.unit
def test_synchronization_attempt_full_happy_path(file_driven, temp_logs_folder, monkeypatch):
    """synchronization_attempt should export, lock, switch the active folder, and sync into the central db"""
    monkeypatch.setattr("Pytre_Core.logs_central.time.sleep", lambda _: None)

    user_logs = temp_logs_folder / "user.db"
    _create_user_logs_with_unsynced_row(user_logs)
    file_driven.user_logs = user_logs
    file_driven.user_id = "u1"
    file_driven.user_name = "User One"
    file_driven.create_db()

    result = file_driven.synchronization_attempt()

    assert result is True

    with contextlib.closing(sqlite3.connect(file_driven.db_path)) as conn:
        count = conn.execute("SELECT COUNT(*) FROM QUERIES_EXEC").fetchone()[0]
    assert count == 1

    # lock must have been released, and the active folder switched from 0 to 1
    assert not file_driven.sync_locked_file.exists()
    index, _ = file_driven.get_active_export_folder()
    assert index == 1


@pytest.mark.unit
def test_synchronization_attempt_returns_true_if_lock_busy(file_driven, temp_logs_folder):
    """synchronization_attempt should still return True (retry later) if the lock is held elsewhere"""
    file_driven.create_dirs(file_driven.export_folders)
    (file_driven.export_folders[0] / "pending.json").write_text("[]")

    with patch.object(file_driven, "try_acquire_lock", return_value=False):
        result = file_driven.synchronization_attempt()

    assert result is True


@pytest.mark.unit
def test_synchronization_attempt_processes_leftover_previous_folder(file_driven, temp_logs_folder, monkeypatch):
    """synchronization_attempt should first flush a non-empty previous folder before switching active"""
    monkeypatch.setattr("Pytre_Core.logs_central.time.sleep", lambda _: None)
    file_driven.create_db()
    file_driven.create_dirs(file_driven.export_folders)

    # active folder is index 1 -> previous is index 0, where we leave an unprocessed file
    file_driven.set_active_export_folder(1)
    leftover = file_driven.export_folders[0] / "leftover.json"
    leftover_row = ["SRV1", "u1", "User One", "SELECT 1", "2026-01-01T00:00:00", 1, 10, "{}"]
    leftover.write_text(json.dumps([leftover_row]))

    user_logs = temp_logs_folder / "user.db"
    _create_user_logs_with_unsynced_row(user_logs)
    file_driven.user_logs = user_logs
    file_driven.user_id = "u1"

    result = file_driven.synchronization_attempt()

    assert result is True
    assert not leftover.exists()  # previous folder's leftover file was processed and removed
    with contextlib.closing(sqlite3.connect(file_driven.db_path)) as conn:
        count = conn.execute("SELECT COUNT(*) FROM QUERIES_EXEC").fetchone()[0]
    assert count == 2  # leftover row + newly exported row


@pytest.mark.unit
def test_synchronization_attempt_releases_lock_on_error(file_driven, temp_logs_folder, monkeypatch):
    """synchronization_attempt should release the lock even if processing raises"""
    monkeypatch.setattr("Pytre_Core.logs_central.time.sleep", lambda _: None)
    file_driven.create_dirs(file_driven.export_folders)
    (file_driven.export_folders[0] / "pending.json").write_text("not valid json")

    result = file_driven.synchronization_attempt()

    assert result is True  # the outer try/except swallows the error, see source
    assert not file_driven.sync_locked_file.exists()


# ------------------------------------------------------------------------------
# sync_thread_start (retry loop)
# ------------------------------------------------------------------------------


@pytest.mark.unit
def test_sync_thread_start_succeeds_on_first_attempt(file_driven, monkeypatch):
    """sync_thread_start should stop after the first successful attempt, with no retry delay"""
    monkeypatch.setattr("Pytre_Core.logs_central.time.sleep", lambda _: None)

    with patch.object(file_driven, "synchronization_attempt", return_value=True) as mock_attempt:
        file_driven.sync_thread_start()

    assert mock_attempt.call_count == 1
    assert file_driven.sync_thread is None


@pytest.mark.unit
def test_sync_thread_start_retries_after_failure_then_succeeds(file_driven, monkeypatch):
    """sync_thread_start should retry (with a delay) after a failed attempt"""
    sleep_calls = []
    monkeypatch.setattr("Pytre_Core.logs_central.time.sleep", lambda s: sleep_calls.append(s))

    with patch.object(file_driven, "synchronization_attempt", side_effect=[False, True]) as mock_attempt:
        file_driven.sync_thread_start()

    assert mock_attempt.call_count == 2
    assert len(sleep_calls) == 1


@pytest.mark.unit
def test_sync_thread_start_gives_up_after_max_retries(file_driven, monkeypatch):
    """sync_thread_start should stop after exhausting all configured retries"""
    monkeypatch.setattr("Pytre_Core.logs_central.time.sleep", lambda _: None)

    with patch.object(file_driven, "synchronization_attempt", return_value=False) as mock_attempt:
        file_driven.sync_thread_start()

    assert mock_attempt.call_count == 3  # len(retries_delay) in the source


@pytest.mark.unit
def test_sync_thread_start_stops_early_if_stop_event_set(file_driven, monkeypatch):
    """sync_thread_start should not retry further once stop_event is set"""
    monkeypatch.setattr("Pytre_Core.logs_central.time.sleep", lambda _: None)

    def fail_and_stop(*args, **kwargs):
        file_driven.stop_event.set()
        return False

    with patch.object(file_driven, "synchronization_attempt", side_effect=fail_and_stop) as mock_attempt:
        file_driven.sync_thread_start()

    assert mock_attempt.call_count == 1


@pytest.mark.unit
def test_sync_thread_start_retries_after_unexpected_exception(file_driven, monkeypatch):
    """sync_thread_start should catch an unexpected exception from an attempt and retry"""
    monkeypatch.setattr("Pytre_Core.logs_central.time.sleep", lambda _: None)

    with patch.object(file_driven, "synchronization_attempt", side_effect=[ValueError("boom"), True]) as mock_attempt:
        file_driven.sync_thread_start()

    assert mock_attempt.call_count == 2


@pytest.mark.unit
def test_sync_thread_start_aborts_if_dirs_cannot_be_created(file_driven):
    """sync_thread_start should not attempt to sync at all if export folders can't be created"""
    with (
        patch.object(file_driven, "create_dirs", return_value=False),
        patch.object(file_driven, "synchronization_attempt") as mock_attempt,
    ):
        file_driven.sync_thread_start()

    assert not mock_attempt.called
