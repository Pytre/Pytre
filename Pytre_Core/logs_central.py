from __future__ import annotations


import hashlib
import json
import logging
import random
import sqlite3
import time
import uuid
from collections.abc import Iterable
from contextlib import closing
from dataclasses import dataclass, field, asdict
from datetime import datetime
from os import getpid, fsync
from pathlib import Path
from queue import Queue, Empty
from socket import gethostname
from threading import Thread, Event


from Pytre_Core.logs_base import BaseLogs
from Pytre_Core.utils import log as _log, setup_standalone_logging


CENTRAL_DB: Path | str = "Pytre_Central_Logs.db"  # default filename for central logs database
SERVER_DB: str = "Pytre_Server_Logs.db"  # default filename for server logs database
SERVER_PENDING_FILE: str = "Pytre_Server_Logs.json"  # default filename for pending logs to add to server database

LATEST_VERSION: int = 2  # latest version model of central database
ENCODING: str = "utf-8"


def log(msg: str | object, level: int = logging.INFO, sublogger: str = __name__) -> None:
    _log(msg, level=level, sublogger=sublogger)


class CentralLogs(BaseLogs):
    def __init__(self, logs_folder: Path | str = Path.cwd() / "logs", logs_filename: str = CENTRAL_DB):
        super().__init__(Path(logs_folder) / logs_filename)
        self.sync_thread: Thread | None = None
        self.logs_folder: Path = Path(logs_folder)
        self.stop_event: Event = Event()
        self.latest_version: int = LATEST_VERSION

    def __del__(self):
        self.stop(5)

    # ------------------------------------------------------------------------------------------
    # Database management
    # ------------------------------------------------------------------------------------------
    def create_db(self) -> bool:
        try:
            with closing(sqlite3.connect(f"file:{self.db_path}?mode=rwc", uri=True)) as conn:
                conn.execute(f"PRAGMA user_version = {self.latest_version};")
                conn.execute(
                    """
                        CREATE TABLE QUERIES_EXEC (
                            SERVER_ID        TEXT,
                            USER_ID          TEXT        NOT NULL,
                            USER_NAME        TEXT,
                            QUERY            TEXT        NOT NULL,
                            START            TEXT        NOT NULL,
                            DURATION_SECS    INTEGER,
                            NB_ROWS          INTEGER,
                            PARAMETERS       TEXT
                        );
                    """
                )
                conn.execute("CREATE INDEX IDX_SERVER_ID ON QUERIES_EXEC (SERVER_ID);")
                conn.execute("CREATE INDEX IDX_QUERY ON QUERIES_EXEC (QUERY);")
                conn.execute("CREATE INDEX IDX_START ON QUERIES_EXEC (START DESC);")

                conn.commit()
                self.version = self.latest_version
        except sqlite3.OperationalError as e:
            log(f"The central SQLite log database could not be created: {e}", logging.ERROR)
            return False

        return True

    def update_db_0_to_1(self) -> bool:
        new_version: int = 1
        try:
            with closing(sqlite3.connect(f"file:{self.db_path}?mode=rw", uri=True)) as conn:
                conn.execute(f"PRAGMA user_version = {new_version};")
                conn.execute("ALTER TABLE QUERIES_EXEC ADD COLUMN SERVER_ID TEXT;")
                conn.execute("DROP INDEX IF EXISTS IDX_SERVER_ID;")
                conn.execute("CREATE INDEX IDX_SERVER_ID ON QUERIES_EXEC (SERVER_ID);")
                conn.commit()
                log("Added SERVER_ID column to QUERIES_EXEC table")

                log(f"Central database updated to version {new_version}")
                self.version = new_version
                return True
        except Exception as e:
            log(f"Unexpected error in schema update to version {new_version}: {e}", logging.ERROR)
            return False

    def update_db_1_to_2(self) -> bool:
        new_version: int = 2
        try:
            with closing(sqlite3.connect(self.db_path)) as conn:
                conn.execute(f"PRAGMA user_version = {new_version};")
                conn.execute("ALTER TABLE QUERIES_EXEC DROP COLUMN END;")
                conn.commit()
                log(f"Central database updated to version {new_version}")
                self.version = new_version
                return True
        except Exception as e:
            log(f"Unexpected error in schema update to version {new_version}: {e}", logging.ERROR)
            return False

    # ------------------------------------------------------------------------------------------
    # Database modifications
    # ------------------------------------------------------------------------------------------
    def insert_into_central_db(self, rows_values: Iterable) -> bool:
        if self.stop_event.is_set():
            return False

        if not self.update_db():
            return False

        try:
            with (
                closing(sqlite3.connect(f"file:{self.db_path}?mode=rw", uri=True, timeout=60)) as conn,
                closing(conn.cursor()) as cursor,
            ):
                conn.execute("PRAGMA busy_timeout=10000")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("PRAGMA cache_size=-10000")

                cursor.execute("BEGIN IMMEDIATE")

                sql_cmd = self._insert_into_central_db_sql()
                cursor.executemany(sql_cmd, rows_values)
                conn.commit()

                return True
        except sqlite3.OperationalError as e:
            log(f"Operational error while writing to central database: {e}", logging.ERROR)
            return False
        except sqlite3.DatabaseError as e:
            log(f"Database error while accessing central database: {e}", logging.ERROR)
            return False
        except Exception as e:
            log(f"Unexpected error while syncing to central database: {e}", logging.ERROR)
            return False

    def _insert_into_central_db_sql(self) -> str:
        return """
            INSERT INTO QUERIES_EXEC (SERVER_ID, USER_ID, USER_NAME, QUERY, START, DURATION_SECS, NB_ROWS, PARAMETERS)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """

    # ------------------------------------------------------------------------------------------
    # Thread management
    # ------------------------------------------------------------------------------------------
    def stop(self, timeout=5) -> bool:
        stopped: bool = True

        self.stop_event.set()
        if self.sync_thread and self.sync_thread.is_alive():
            self.sync_thread.join(timeout)
            stopped = not self.sync_thread.is_alive()

        if stopped:
            self._on_stopped()

        return stopped

    # ------------------------------------------------------------------------------------------
    # Methods to implement in child class
    # ------------------------------------------------------------------------------------------
    def thread_start(self) -> None:
        msg = "Function to start thread for central log database isn't implemented !"
        log(msg, logging.ERROR)
        raise NotImplementedError(msg)

    def _on_stopped(self) -> None:
        """Hook for subclass-specific cleanup. Called by stop() only if the thread is no longer running.
        Cleaning up is not safe while thread might run concurrently on the same objects."""
        pass


class ClientDriven(CentralLogs):
    """Central log writer used by Pytre_Client.
    Reads unsynced rows from a user SQLite file and syncs them into a shared central database.
    Uses file-based locking with a rotating export-folder queue as several instances may write concurrently."""

    def __init__(self, logs_folder: Path = Path.cwd() / "logs", logs_filename: str = CENTRAL_DB):
        super().__init__(logs_folder, logs_filename=logs_filename)

        self.user_id: str = ""
        self.user_name: str = ""
        self.user_logs: Path | None = None

        self.queue_ctrl_file: Path = self.logs_folder / "Pytre_Queue.json"
        self.export_folders: list[Path] = [self.logs_folder / "Pytre_Queue_1", self.logs_folder / "Pytre_Queue_2"]
        self.failed_folder: Path = self.logs_folder / "Pytre_Failed"
        self.temp_files: list[Path] = []

        self.sync_unlocked_file: Path = self.logs_folder / "Pytre_Unlocked.json"
        self.sync_locked_file: Path = self.logs_folder / "Pytre_Locked.json"
        self.sync_lock_timeout: int = 120  # seconds since last heartbeat

        self.sync_heartbeat: int = 90  # seconds after which heartbeat will be updated
        self.stop_heartbeat: Event = Event()

    def trigger_sync(self, user_logs: Path, user_id: str = "", user_name: str = ""):
        if not self.db_path:
            log("Error: Central log database not specified", logging.ERROR)
            return

        if not self.check_db():
            log(f"Aborting sync, failed to check central database: {self.db_path}", logging.ERROR)
            return

        if not user_logs or not Path(user_logs).exists():
            log(f"Error: User log database does not exist: {user_logs}", logging.ERROR)
            return

        if self.sync_thread is None or not self.sync_thread.is_alive():
            self.stop_event.clear()
            self.user_id = user_id
            self.user_name = user_name
            self.user_logs = Path(user_logs)

            self.sync_thread = Thread(target=self.thread_start, daemon=True)
            self.sync_thread.start()

    def thread_start(self):
        log("Thread logs sync starting")

        if not self.create_dirs(self.export_folders):
            log("Error: required folders does not exist and failed to create them", logging.ERROR)
            return

        retries_delay: list[int] = [30, 60, 120]  # in seconds
        for attempt, delay in enumerate(retries_delay):
            try:
                sync_result: bool = self.synchronization_attempt()
                if sync_result:
                    log("Synchronization completed successfully")
                    break
            except Exception as e:
                log(f"Unexpected error while syncing logs : {e}", logging.ERROR)

            if self.stop_event.is_set():
                break
            if attempt == len(retries_delay) - 1:
                log("Max retries reached", logging.WARNING)
                break

            jitter_factor = 0.8 + (0.4 * random.random())  # to add jitter to retry delay
            log(f"Next attempt in {delay * jitter_factor:.2f} sec")
            time.sleep(delay * jitter_factor)

        self.sync_thread: Thread | None = None
        log("Thread logs sync ending")

    def synchronization_attempt(self) -> bool:
        self.export_unsynced_data()

        nb_to_sync: int = 0
        for folder in self.export_folders:
            nb_to_sync += len(list(folder.glob("*.json")))
        if nb_to_sync == 0:
            log("Stopping sync, no exported files to process")
            return False

        if not self.try_acquire_lock():
            log("Lock could not be acquired")
        else:
            try:
                active_index, folder_to_process = self.get_active_export_folder()

                # check if previous folder has unprocessed files and synchronize them first
                previous_index = (active_index - 1) % len(self.export_folders)
                previous_folder = self.export_folders[previous_index]
                if next(previous_folder.glob("*.json"), None):
                    log("Previous active folder not empty, attempting to synchronize it")
                    try:
                        self.synchronize_folder(previous_folder)
                    except Exception as e:
                        log(f"Error processing previous folder : {e}", logging.WARNING)

                # switch active folder and synchronize vacated folder
                if not self.set_active_export_folder(active_index + 1):
                    raise Exception("Couldn't change active folder")
                self.synchronize_folder(folder_to_process)

                log("Successfully processed export files")
                return True
            except Exception as e:
                log(f"Error : {e}", logging.ERROR)
            finally:
                self.release_lock()

        return True

    def synchronize_folder(self, folder_path: Path):
        files = self.wait_for_files_completion(folder_path)
        if not files:
            raise TimeoutError("No stable file found before timeout")
        if not self.process_files(files):
            raise RuntimeError("Problem occurred while processing exported files")

    def _on_stopped(self) -> None:
        self.cleanup_temp_files()

    # ------------------------------------------------------------------------------------------
    # user_db : sql commands
    # ------------------------------------------------------------------------------------------
    def _get_unsynced_rows_sql(self) -> str:
        return f"""
            SELECT 
                ROWID, SERVER_ID,
                :user_id AS USER_ID, :user_name AS USER_NAME, 
                QUERY, START, DURATION_SECS, NB_ROWS, PARAMETERS 
            FROM
                QUERIES_EXEC 
            WHERE
                EXPORTED = 0
        """

    def _mark_rows_as_exported_sql(self, place_holders) -> str:
        return f"UPDATE QUERIES_EXEC SET EXPORTED = 1 WHERE ROWID IN ({place_holders})"

    # ------------------------------------------------------------------------------------------
    # Functions to export data from user db into files and to mark as synced
    # ------------------------------------------------------------------------------------------
    def export_unsynced_data(self) -> bool:
        if self.stop_event.is_set():
            return True

        _, active_folder = self.get_active_export_folder()
        if not active_folder.exists() and not self.create_dirs(active_folder):
            log(f"Error: required folder do not exist and failed to create it: {active_folder}", logging.ERROR)
            return False

        try:
            unsynced = self.get_unsynced_rows()
            if not unsynced:
                return True

            rows_id = [item[0] for item in unsynced]
            rows_values = [item[1:] for item in unsynced]

            user_id_hash = hashlib.md5(self.user_id.encode()).hexdigest()[:16]
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            unique_id = str(uuid.uuid4())[:8]
            export_file = active_folder / f"{user_id_hash}_{timestamp}_{unique_id}.json"

            with open(export_file, "w") as f:
                json.dump(rows_values, f, indent=4)

            self.mark_rows_as_exported(rows_id)

            return True
        except Exception as e:
            log(f"Error during data export: {e}", logging.ERROR)
            return False

    def get_unsynced_rows(self) -> list[tuple] | None:
        if self.stop_event.is_set():
            return None

        with (
            closing(sqlite3.connect(f"file:{self.user_logs}?mode=ro", uri=True)) as conn,
            closing(conn.cursor()) as cursor,
        ):
            sql_cmd = self._get_unsynced_rows_sql()
            cursor.execute(sql_cmd, {"user_id": self.user_id, "user_name": self.user_name})
            unsynced = cursor.fetchall()

        return unsynced

    def mark_rows_as_exported(self, rows_id: list[int]):
        with (
            closing(sqlite3.connect(f"file:{self.user_logs}?mode=rw", uri=True)) as conn,
            closing(conn.cursor()) as cursor,
        ):
            place_holders = ", ".join("?" * len(rows_id))
            sql_cmd = self._mark_rows_as_exported_sql(place_holders)
            cursor.execute(sql_cmd, rows_id)

            conn.commit()

    # ------------------------------------------------------------------------------------------
    # Functions to update central database
    # ------------------------------------------------------------------------------------------
    def wait_for_files_completion(self, folder_path: Path, max_wait: int = 60) -> set[str] | None:
        check_interval = 5  # seconds
        required_stable_count = 3  # scan with no change before assuming file is stable

        @dataclass
        class FileStats:
            size: int | None = None
            stable_count: int = 0

        files: dict[str, FileStats] = {}
        start_time = time.time()
        stable_files: set[str] = set()
        while time.time() - start_time < max_wait:
            if self.stop_event.is_set():
                return None

            for f in folder_path.glob("*.json"):
                key = str(f)

                try:
                    f_size = f.stat().st_size
                except FileNotFoundError:
                    files.pop(key, None)
                    continue

                if files.get(key) is None:
                    files[key] = FileStats()

                if f_size == files[key].size:
                    files[key].stable_count += 1
                else:
                    files[key].size = f_size
                    files[key].stable_count = 0

            stable_files = {k for k, v in files.items() if v.stable_count >= required_stable_count}

            if len(stable_files) == len(files):
                log(f"Folder {folder_path} stable for all {len(files)} files")
                return stable_files

            time.sleep(check_interval)

        log(f"Max wait time reached, {len(stable_files)} files from {len(files)} will be processed")
        return stable_files if stable_files else None

    def process_files(self, files_to_process: set[str] | None) -> bool:
        if self.stop_event.is_set():
            return False
        if not files_to_process:
            log("No exported files to process!")
            return False

        log(f"Starting processing {len(files_to_process)} file(s)")

        rows_values, processed_files = self.files_extract_infos(list(files_to_process))

        if rows_values:
            success = self.insert_into_central_db(rows_values)
            if success:
                self.processed_files_remove(processed_files)
            else:
                log("Failed to insert into central database, moving files to active folder...", logging.WARNING)
                _, active_folder = self.get_active_export_folder()
                self.processed_files_move(processed_files, active_folder)

        log("Export processing completed")

        return True

    def files_extract_infos(self, exported_files: list[Path] | list[str]) -> tuple[list, list[Path]]:
        rows_values: list = []
        processed_files: list[Path] = []
        failed_files: list[Path] = []

        for file in exported_files:
            try:
                with open(file, "r") as f:
                    rows = json.load(f)
                rows_values.extend(rows)
                processed_files.append(Path(file))
            except FileNotFoundError:
                log(f"Import error, file not found : {file}", logging.WARNING)
            except Exception as e:
                log(f"Error processing {file} : {e}", logging.WARNING)
                failed_files.append(Path(file))

        if failed_files:
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            self.failed_files_move(failed_files, timestamp)

        return rows_values, processed_files

    def failed_files_move(self, failed_files: list[Path], timestamp: str):
        if not self.create_dirs(self.failed_folder):
            log("Error: failed folder doesn't exist and unable to create it", logging.ERROR)
            return

        for file in failed_files:
            new_name = f"{file.stem}_{timestamp}{file.suffix}"
            file.rename(self.failed_folder / new_name)

    def processed_files_remove(self, processed_files: list[Path]):
        for file in processed_files:
            try:
                file.unlink()
            except Exception as e:
                log(f"Error deleting processed file {file}: {e}", logging.WARNING)

    def processed_files_move(self, processed_files: list[Path], active_folder: Path):
        if not active_folder.exists():
            log(f"Aborting moving files, active folder doesn't exist: {active_folder}", logging.ERROR)
            return

        for file in processed_files:
            file.rename(active_folder / file.name)

    # ------------------------------------------------------------------------------------------
    # Functions for locking based on file renaming
    # ------------------------------------------------------------------------------------------
    def try_acquire_lock(self) -> bool:
        # Ensure the lock file exists
        if not self.sync_unlocked_file.exists() and not self.sync_locked_file.exists():
            self.set_unlocked_data(not_exists_ok=True)

        # Break any existing stale lock
        self.break_stale_lock()

        # Check if lock is taken
        if self.sync_locked_file.exists():
            log("Lock is currently held by another process")
            return False

        try:
            if self.sync_unlocked_file.exists():
                # Try to acquire the lock by renaming the file and writing data to it
                self.sync_unlocked_file.rename(self.sync_locked_file)
                lock_data: dict = {
                    "status": "locked",
                    "acquired": datetime.now().isoformat(),
                    "last_heartbeat": datetime.now().isoformat(),
                    "user": self.user_id,
                    "hostname": gethostname(),
                    "pid": getpid(),
                }
                if not self.write_atomic(self.sync_locked_file, lock_data):
                    log("Lock writing data failed! Another process may have taken lock")
                    return False

                # Check if lock file contains expected content
                with open(self.sync_locked_file, "r") as f:
                    content: dict = json.load(f)
                    if not content == lock_data:
                        log("Lock verification failed! Another process may have taken it")
                        return False

                self.heartbeat_thread: Thread = self.start_heartbeat_thread()
                log("Successfully acquired lock")
                return True
            else:
                # Check for stale lock before giving up and try again if unlocked file appears
                self.break_stale_lock()
                if self.sync_unlocked_file.exists():
                    return self.try_acquire_lock()
                return False
        except json.JSONDecodeError:
            log("Could not verify locked file contents", logging.ERROR)
            return False
        except OSError:
            log("Failed to acquire lock, another process got it first")
            return False

    def break_stale_lock(self) -> bool:
        if not self.sync_locked_file.exists():
            return True

        try:
            with open(self.sync_locked_file, "r") as f:
                data: dict = json.load(f)
            last_heartbeat = datetime.fromisoformat(data.get("last_heartbeat", data.get("acquired", "1901-01-01")))
            time_since_heartbeat = (datetime.now() - last_heartbeat).total_seconds()

            if time_since_heartbeat > self.sync_lock_timeout:
                log(f"Breaking stale lock (last heartbeat {time_since_heartbeat:.2f} secs ago)")
                self.sync_locked_file.rename(self.sync_unlocked_file)
                self.set_unlocked_data()
                return True
        except Exception as e:
            log(f"Error while breaking/checking stale lock: {e}", logging.ERROR)
            return False

        return True

    def release_lock(self) -> bool:
        if self.sync_locked_file.exists():
            try:
                self.stop_heartbeat.set()

                # remove unlocked_file if it exists when it shouldn't
                if self.sync_unlocked_file.exists():
                    self.sync_unlocked_file.unlink()

                self.sync_locked_file.rename(self.sync_unlocked_file)
                self.set_unlocked_data()
                log("Lock released")
                return True
            except Exception as e:
                log(f"Failed to release lock: {e}", logging.WARNING)
                return False
        return False

    def set_unlocked_data(self, not_exists_ok: bool = False) -> None:
        if not not_exists_ok and not self.sync_unlocked_file.exists():
            return

        timestamp = datetime.now().isoformat()
        unlocked_data = {"status": "unlocked", "created": timestamp, "user": self.user_id}
        if not self.write_atomic(self.sync_unlocked_file, unlocked_data):
            log("Failed to set unlocked data")

    # ------------------------------------------------------------------------------------------
    # Functions to add heartbeat to lock file
    # ------------------------------------------------------------------------------------------
    def start_heartbeat_thread(self):
        def heartbeat_worker():
            while not self.stop_heartbeat.is_set():
                if self.sync_locked_file.exists():
                    self.update_heartbeat()
                time.sleep(self.sync_heartbeat)

        self.stop_heartbeat.clear()
        heartbeat_thread = Thread(target=heartbeat_worker, daemon=True)
        heartbeat_thread.start()
        return heartbeat_thread

    def update_heartbeat(self) -> bool:
        if not self.sync_locked_file.exists():
            return False

        try:
            with open(self.sync_locked_file, "r+") as f:
                lock_data = json.load(f)
                lock_data["last_heartbeat"] = datetime.now().isoformat()

                # Reset file pointer to beginning before writing
                f.seek(0)
                json.dump(lock_data, f, indent=4)
                f.truncate()  # Remove any remaining content
            return True
        except Exception as e:
            log(f"Heartbeat update failed: {e}", logging.WARNING)
            return False

    # ------------------------------------------------------------------------------------------
    # Files and folders management
    # ------------------------------------------------------------------------------------------
    def write_atomic(self, target_path: Path, content: str | dict, encoding: str = ENCODING) -> bool:
        temp_file = Path(target_path).with_name(f"{target_path.name}.{uuid.uuid4()}.tmp")
        self.temp_files.append(temp_file)
        try:
            # Writing content to temp file using binary mode for consistency
            with open(temp_file, "wb") as f:
                if isinstance(content, dict):
                    json_string: str = json.dumps(content, indent=4)
                    f.write(json_string.encode(encoding))
                else:
                    f.write(content.encode(encoding))

                # Ensure data is flushed to disk
                f.flush()
                fsync(f.fileno())

            # Atomic rename operation (more likely to be atomic even on network shares)
            temp_file.replace(target_path)
            self.temp_files.remove(temp_file)
            return True
        except FileExistsError:
            log(f"Temp file {temp_file} already exists", logging.ERROR)
            return False
        except PermissionError:
            log(f"Permission denied when writing to {temp_file}", logging.ERROR)
            return False
        except OSError as e:
            log(f"OS error when writing file {target_path}: {e}", logging.ERROR)
            return False
        except Exception as e:
            log(f"Unexpected error writing file {target_path}: {e}", logging.ERROR)
            return False
        finally:
            try:
                if temp_file.exists():
                    temp_file.unlink()
                    self.temp_files.remove(temp_file)
            except Exception as e:
                log(f"Failed to clean up temp file {temp_file}: {e}", logging.ERROR)

    def cleanup_temp_files(self):
        for temp_file in list(self.logs_folder.glob("**/*.tmp")):
            # add files older than 1 week as file to be cleaned up
            if time.time() - temp_file.stat().st_mtime > 3600 * 24 * 7:
                self.temp_files.append(temp_file)

        for temp_file in set(self.temp_files):
            try:
                if temp_file.exists():
                    temp_file.unlink()
                    log(f"Cleaned up temp file: {temp_file}")
            except Exception as e:
                log(f"Error cleaning up temp file {temp_file}: {e}", logging.WARNING)

        self.temp_files = []

    def create_dirs(self, dirs: list[Path] | Path) -> bool:
        dirs_list: list[Path]
        if isinstance(dirs, list):
            dirs_list = dirs
        else:
            dirs_list = [dirs]

        success: bool = True
        for dir in dirs_list:
            dir_path = Path(dir)
            try:
                dir_path.mkdir(exist_ok=True, parents=True)
            except PermissionError:
                log(f"Warning: No permission to create directory: {dir_path}", logging.ERROR)
                success = False
            except OSError as e:
                log(f"OS error when creating directory {dir_path}: {e}", logging.ERROR)
                success = False

        return success

    def set_active_export_folder(self, folder_index: int) -> bool:
        folder_index = folder_index % len(self.export_folders)
        folder_name = self.export_folders[folder_index].name
        content = {
            "active_folder_index": folder_index,
            "active_folder_name": folder_name,
            "updated": datetime.now().isoformat(),
        }
        if not self.write_atomic(self.queue_ctrl_file, content):
            log(f"Error updating active export folder to {folder_name}", logging.ERROR)
            return False

        return True

    def get_active_export_folder(self) -> tuple[int, Path]:
        try:
            if self.queue_ctrl_file.exists():
                with open(self.queue_ctrl_file, "r") as f:
                    data: dict = json.load(f)
                    folder_index = data.get("active_folder_index", 0)
                    return folder_index, self.export_folders[folder_index]
            else:
                self.set_active_export_folder(0)
                return 0, self.export_folders[0]
        except Exception as e:
            log(f"Error reading active folder, using default: {e}", logging.ERROR)
            return 0, self.export_folders[0]


@dataclass(frozen=True)
class ExecutionLogEntry:
    """A single completed query execution, ready to be logged to the central database."""

    server_id: str
    user_id: str
    user_name: str
    query_id: str
    start: datetime
    duration_secs: int = 0
    nb_rows: int = 0
    parameters: dict[str, str] = field(default_factory=dict)

    def as_row(self) -> tuple:
        """Row tuple matching CentralLogs._insert_into_central_db_sql()'s column order."""
        params_json = json.dumps(self.parameters, indent=4, ensure_ascii=False) if self.parameters else None
        return (
            self.server_id,
            self.user_id,
            self.user_name,
            self.query_id,
            self.start.isoformat(),
            self.duration_secs,
            self.nb_rows,
            params_json,
        )

    def to_dict(self) -> dict:
        """JSON-safe representation, for the pending-file fallback."""
        data = asdict(self)
        data["start"] = self.start.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: dict) -> ExecutionLogEntry:
        data = dict(data)
        data["start"] = datetime.fromisoformat(data["start"])
        return cls(**data)


class ServerDriven(CentralLogs):
    """Central log writer used by Pytre_Server.
    Receives execution records directly through a dedicated background thread using an in-memory queue.
    Only one process writes to its own central database, so no cross-process locking is needed"""

    def __init__(self, logs_folder: Path | str = Path.cwd() / "logs", logs_filename: str = SERVER_DB):
        super().__init__(logs_folder, logs_filename=logs_filename)

        self.queue: Queue[ExecutionLogEntry] = Queue()
        self.queue_poll_timeout: float = 1  # secs, loop wakes up and how soon a partial batch gets flushed on idle
        self.batch_size: int = 25  # how many entries are stored before being written when queue piles up entries

        self.force_file: bool = False  # to force write to pending file if there is a problem with the database
        self.pending_file: Path = self.logs_folder / SERVER_PENDING_FILE  # file to use when failing to write to db
        self.failures_nb: int = 0
        self.max_failures: int = 5  # consecutive failures before spilling to the pending file
        self.retry_interval_secs: int = 300  # how often the thread retries the pending file while running

    def log_execution(self, entry: ExecutionLogEntry) -> None:
        """Queue an execution record for writing. Non-blocking, safe to call from async code -
        starts the writer thread lazily on first use if it isn't already running."""
        self.queue.put_nowait(entry)

        if self.sync_thread is None or not self.sync_thread.is_alive():
            self.stop_event.clear()
            self.sync_thread = Thread(target=self.thread_start, daemon=True)
            self.sync_thread.start()

    def check_db(self, create: bool = True) -> bool:
        """Supersedes parent method to default to pending file if database couldn't be created or updated"""
        result = super().check_db(create)

        if not result:
            self.force_file = True
            if not self.db_path.exists():
                log("Failed to create central logs database, logs will be written on a pending file", logging.WARNING)
            else:
                log("Failed to update central logs database, logs will be written on a pending file", logging.ERROR)

        return result

    def thread_start(self) -> None:
        log("Log writer thread starting")
        self.logs_folder.mkdir(parents=True, exist_ok=True)
        self.check_db()  # failing not fatal : logs will be written on the pending file
        self._retry_pending_file()

        entries: list[ExecutionLogEntry] = []
        last_retry = time.time()
        while not self.stop_event.is_set():
            try:
                entry = self.queue.get(timeout=self.queue_poll_timeout)
                entries.append(entry)
            except Empty:
                if time.time() - last_retry >= self.retry_interval_secs:
                    self._retry_pending_file()
                    last_retry = time.time()

                if entries and self._write_entries(entries):
                    entries = []

                continue

            if len(entries) >= self.batch_size and self._write_entries(entries):
                entries = []

        self._write_pending_file(extras=entries)
        log("Server-side central log writer thread stopping")

    def _write_entries(self, entries: list[ExecutionLogEntry]) -> bool:
        """Try to write entries to the database.
        If `max_failures` consecutives failures are reached writes to the pending file instead."""

        if not self.force_file:
            rows = [entry.as_row() for entry in entries]
            if self.insert_into_central_db(rows):
                self.failures_nb = 0
                return True

            self.failures_nb += 1
            log(f"Failed to write execution log ({self.failures_nb} consecutive failure(s))", logging.DEBUG)

        if self.failures_nb >= self.max_failures or self.force_file:
            if not self.force_file:
                log("Too many consecutive failures, spilling pending entries to disk", logging.WARNING)

            self._write_pending_file(extras=entries)
            self.failures_nb = 0
            return True

        return False

    def _write_pending_file(self, extras: list[ExecutionLogEntry] | None = None) -> None:
        """Appends whatever is left in the in-memory queue (plus `extras`, if given) to the pending JSON Lines file.
        Never rereads what's already on disk, to reduce the cost of a spill to the new entries."""
        entries = extras if extras is not None else []
        while True:
            try:
                entries.append(self.queue.get_nowait())
            except Empty:
                break

        if not entries:
            return

        try:
            with open(self.pending_file, "a", encoding=ENCODING) as f:
                for entry in entries:
                    f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
                f.flush()
                fsync(f.fileno())
        except OSError as e:
            log(f"Failed to spill {len(entries)} pending log entrie(s) to {self.pending_file}: {e}", logging.ERROR)

    def _read_pending_file(self, encoding=ENCODING) -> list[dict]:
        """Reads the pending JSON Lines file one line at a time. An unparseable line is skipped."""
        if not self.pending_file.exists():
            return []

        entries: list[dict] = []
        try:
            with open(self.pending_file, "r", encoding=encoding) as f:
                for line_nb, line in enumerate(f, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        log(f"Skipping corrupt line {line_nb} in {self.pending_file}: {line}", logging.WARNING)
        except OSError as e:
            log(f"Could not read pending log file {self.pending_file}: {e}", logging.ERROR)
            return []

        return entries

    def _retry_pending_file(self) -> None:
        """Retry to write pending file to database. Do nothing if `self.force_file` is True."""
        if self.force_file:
            return

        pending = self._read_pending_file()
        if not pending:
            return

        entries: list[ExecutionLogEntry] = []
        for data in pending:
            try:
                entries.append(ExecutionLogEntry.from_dict(data))
            except Exception as e:
                log(f"Skipping corrupt entry in {self.pending_file}: {e}", logging.WARNING)

        if not entries:
            self.pending_file.unlink(missing_ok=True)
            return

        if self.insert_into_central_db([e.as_row() for e in entries]):
            self.pending_file.unlink(missing_ok=True)
            self.failures_nb = 0
            log(f"Recovered {len(entries)} pending log entrie(s) from {self.pending_file}")
        else:
            log(f"Still unable to write {len(entries)} pending log entrie(s), will retry later", logging.WARNING)


if __name__ == "__main__":
    setup_standalone_logging()

    Path_Home: Path = Path.home() / "Pytre"
    central_log = ClientDriven(Path_Home)
    central_log.trigger_sync(Path_Home / "Pytre_Logs.db", "id_test", "name_test")
    for i in range(10):
        time.sleep(30)
        if central_log.sync_thread is None or not central_log.sync_thread.is_alive():
            break
