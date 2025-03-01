import sqlite3
import time
import re
import uuid
import hashlib
import json
import random
from os import getpid
from socket import gethostname
from datetime import datetime
from threading import Thread
from pathlib import Path


CENTRAL_DB: Path = "Pytre_Logs.db"


class CentralLogs:
    def __init__(self, logs_folder: Path = "."):
        self.sync_thread = None
        self.logs_folder: Path = Path(logs_folder)
        self.central_db: Path = self.logs_folder / CENTRAL_DB

        self.control_file: Path = self.logs_folder / "sync_active_queue.json"
        self.export_folders: list[Path] = [self.logs_folder / "export_queue_1", self.logs_folder / "export_queue_2"]
        self.failed_folder: Path = self.logs_folder / "failed_queue"

        self.sync_unlocked_file: Path = self.logs_folder / "sync.unlocked"
        self.sync_locked_file: Path = self.logs_folder / "sync.locked"
        self.sync_lock_timeout = 300  # seconds (5 minutes)

        self.temp_files: list[Path] = []
        self.force_to_stop: bool = False

    def __del__(self):
        self.stop_sync(5)
        self.cleanup_temp_files()

    def create_db(self):
        try:
            with sqlite3.connect(f"file:{self.central_db}?mode=rw", uri=True) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
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
                conn.execute("CREATE INDEX IDX_QUERY ON QUERIES_EXEC (QUERY);")
                conn.execute("CREATE INDEX START ON QUERIES_EXEC (START DESC);")

                conn.commit()
        except sqlite3.OperationalError as e:
            print(f"The central SQLite log database could not be created: {e}")

    def create_dirs(self, dirs: list[Path]) -> bool:
        dirs = [*self.export_folders, self.failed_folder]

        success: bool = True
        for dir in dirs:
            dir_path = Path(dir)
            try:
                dir_path.mkdir(exist_ok=True, parents=True)
            except PermissionError:
                print(f"Warning: No permission to create directory: {dir_path}")
                success = False
            except OSError as e:
                print(f"OS error when creating directory {dir_path}: {e}")
                success = False

        return success

    def write_atomic(self, target_path: Path, content) -> bool:
        temp_file = Path(target_path).with_name(f"{target_path.name}.{uuid.uuid4()}.tmp")
        self.temp_files.append(temp_file)
        try:
            with open(temp_file, "w") as f:
                if isinstance(content, dict):
                    json.dump(content, f, indent=4)
                else:
                    f.write(content)
            # Atomic rename operation (more likely to be atomic even on network shares)
            temp_file.replace(target_path)
            self.temp_files.remove(temp_file)
            return True
        except Exception as e:
            print(f"Error writing file {target_path}: {e}")
            try:
                if temp_file.exists():
                    temp_file.unlink()
                    self.temp_files.remove(temp_file)
            except Exception as e:
                pass
            return False

    def cleanup_temp_files(self):
        for temp_file in self.temp_files:
            try:
                if temp_file.exists():
                    temp_file.unlink()
                    print(f"Cleaned up temp file: {temp_file}")
            except Exception as e:
                print(f"Error cleaning up temp file {temp_file}: {e}")

        self.temp_files = []

    # ------------------------------------------------------------------------------------------
    # Thread management
    # ------------------------------------------------------------------------------------------
    def trigger_sync(self, user_db: Path, user_id: str = "", user_name: str = ""):
        if self.central_db and not self.central_db.exists():
            self.create_db()

        if not self.central_db:
            print("Error: Central log database not specified")
            return
        if not user_db or not Path(user_db).exists():
            print(f"Error: User log database does not exist: {user_db}")
            return
        if not self.create_dirs(self.export_folders):
            print("Error: required folders does not exist and failed to create them")
            return

        if self.sync_thread is None or not self.sync_thread.is_alive():
            self.force_to_stop = False
            args = (Path(user_db), user_id, user_name)
            self.sync_thread = Thread(target=self._sync_thread_start, args=args, daemon=True)
            self.sync_thread.start()

    def _sync_thread_start(self, user_db: Path, user_id: str, user_name: str):
        print("Thread logs sync starting")
        retry_delay: int = 15  # seconds
        for _ in range(10):
            try:
                self.export_unsynced_data(user_db, user_id, user_name)

                nb_to_sync: int = 0
                for folder in self.export_folders:
                    nb_to_sync += len(list(folder.glob("*.json")))
                if nb_to_sync == 0:
                    break

                if self.try_acquire_lock(user_id):
                    try:
                        active_index, folder_to_process = self.get_active_export_folder()
                        if not self.set_active_export_folder(active_index + 1):  # change active folder
                            print("Couldn't change active folder, aborting syncing")
                            break
                        if not self.wait_for_folder_completion(folder_to_process):
                            break
                        self.process_exported_files(folder_to_process)
                    finally:
                        self.release_lock()
                    print("Successfully processed export files")
                    break
                else:
                    print("Lock could not be acquired")
            except Exception as e:
                print(f"Syncing logs error : {e}")

            if self.force_to_stop:
                break

            jitter_factor = 0.8 + (0.4 * random.random())  # to add jitter to retry delay
            time.sleep(retry_delay * jitter_factor)
            retry_delay = min(retry_delay * 2, 300)

        print("Thread logs sync ending")

    def stop_sync(self, timeout=5) -> bool:
        if self.sync_thread and self.sync_thread.is_alive():
            self.force_to_stop = True
            self.sync_thread.join(timeout)
            return not self.sync_thread.is_alive()

        self.cleanup_temp_files()

        return True

    # ------------------------------------------------------------------------------------------
    # Functions to export data to sync into files
    # ------------------------------------------------------------------------------------------
    def export_unsynced_data(self, user_db: Path, user_id: str, user_name: str) -> bool:
        if self.force_to_stop:
            return True

        _, export_folder = self.get_active_export_folder()
        if not export_folder.exists():
            return False

        try:
            unsynced = self.rows_get_unsynced(user_db, user_id, user_name)
            if not unsynced:
                return True

            rows_id = [item[0] for item in unsynced]
            rows_values = [item[1:] for item in unsynced]

            safe_user_id = self.get_safe_filename(user_id)
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            unique_id = str(uuid.uuid4())[:8]
            export_file = export_folder / f"{safe_user_id}_{timestamp}_{unique_id}.json"

            with open(export_file, "w") as f:
                json.dump(rows_values, f, indent=4)

            self.mark_rows_as_exported(user_db, rows_id)

            return True
        except Exception as e:
            print(f"Error during data export: {e}")
            return False

    def rows_get_unsynced(self, user_db: Path, user_id: str, user_name: str) -> list[tuple] | None:
        if self.force_to_stop:
            return None

        with sqlite3.connect(f"file:{user_db}?mode=ro", uri=True) as conn:
            cursor: sqlite3.Cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT 
                    ROWID,
                    '{user_id}' AS USER_ID, '{user_name}' AS USER_NAME, 
                    QUERY, START, END, DURATION_SECS, NB_ROWS, PARAMETERS 
                FROM
                    QUERIES_EXEC 
                WHERE
                    EXPORTED = 0
            """
            )
            unsynced = cursor.fetchall()
            cursor.close()

        return unsynced

    def mark_rows_as_exported(self, user_db: Path, rows_id: list[int]):
        with sqlite3.connect(f"file:{user_db}?mode=rw", uri=True) as conn:
            cursor: sqlite3.Cursor = conn.cursor()
            rows_to_update = ", ".join([str(id) for id in rows_id])
            cursor.execute(f"UPDATE QUERIES_EXEC SET EXPORTED = 1 WHERE ROWID IN ({rows_to_update})")
            conn.commit()
            cursor.close()

    def get_safe_filename(self, text: str) -> str:
        safe_name = re.sub(r"[^\w\-_]", "_", text)  # remove invalid characters

        if len(safe_name) > 16 or not safe_name:
            hash_object = hashlib.md5(text.encode())
            safe_name = hash_object.hexdigest()[:16]

        return safe_name

    # ------------------------------------------------------------------------------------------
    # Functions to update central database
    # ------------------------------------------------------------------------------------------
    def wait_for_folder_completion(self, folder_path: Path, max_wait: int = 60) -> bool:
        check_interval = 5  # seconds
        stable_period = 15  # seconds without changes

        last_sizes = {}
        stable_since = None

        start_time = time.time()
        while time.time() - start_time < max_wait:
            if self.force_to_stop:
                return False

            current_sizes = {f: f.stat().st_size for f in folder_path.glob("*.json")}

            if current_sizes == last_sizes:
                if stable_since is None:
                    stable_since = time.time()
                elif time.time() - stable_since >= stable_period:
                    print(f"Folder {folder_path} stable for {stable_period}s with {len(current_sizes)} files")
                    return True
            else:
                stable_since = None
                last_sizes = current_sizes

            time.sleep(check_interval)

        print(f"Max wait time reached, {len(last_sizes)} files (possibly incomplete)")
        return False

    def process_exported_files(self, folder_to_process: Path) -> bool:
        if self.force_to_stop:
            return False

        if not folder_to_process.exists():
            print(f"The export folder does not exist: {folder_to_process}")
            return False

        exported_files = list(folder_to_process.glob("*.json"))
        if not exported_files:
            print("No exported files to process!")
            return False

        print(f"Starting processing {len(exported_files)} file(s)")

        rows_values: list = []
        processed_files: list[Path] = []
        failed_files: list[Path] = []
        for file in exported_files:
            try:
                with open(file, "r") as f:
                    rows = json.load(f)
                rows_values.extend(rows)
                processed_files.append(file)
            except Exception as e:
                print(f"Error processing {file} : {e}")
                failed_files.append(file)

        if failed_files:
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            self.move_failed_files(failed_files, timestamp)

        if rows_values:
            success = self.insert_into_central_db(rows_values)
            if success:
                self.remove_processed_files(processed_files)
            else:
                print("Failed to insert into central database, moving files to active folder...")
                _, active_folder = self.get_active_export_folder()
                for file in processed_files:
                    file.rename(active_folder / file.name)

        print("Export processing completed")

        return True

    def insert_into_central_db(self, rows_values: list[tuple]) -> bool:
        if self.force_to_stop:
            return

        try:
            with sqlite3.connect(f"file:{self.central_db}?mode=rw", uri=True, timeout=60) as conn:
                conn.execute("PRAGMA busy_timeout=10000")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("PRAGMA cache_size=-10000")

                cursor: sqlite3.Cursor = conn.cursor()
                cursor.executemany(
                    """
                    INSERT INTO QUERIES_EXEC (USER_ID, USER_NAME, QUERY, START, END, DURATION_SECS, NB_ROWS, PARAMETERS)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows_values,
                )
                conn.commit()
                cursor.close()
                return True
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e):
                print(f"Central database is locked : {e}")
                return False
            else:
                print(f"Error writing to central database : {e}")
                return False
        except Exception as e:
            print(f"Unexpected error while syncing to central database : {e}")
            return False

    def move_failed_files(self, failed_files: list[Path], timestamp: str):
        if not self.create_dirs(self.failed_folder):
            print("Error: failed folder doesn't exist and unable to create it")
            return

        for file in failed_files:
            new_name = f"{file.stem}_{timestamp}{file.suffix}"
            file.rename(self.failed_folder / new_name)

    def remove_processed_files(self, processed_files: list[Path]):
        for file in processed_files:
            try:
                file.unlink()
            except Exception as e:
                print(f"Error deleting processed file {file}: {e}")

    # ------------------------------------------------------------------------------------------
    # Functions for locking based on file renaming
    # ------------------------------------------------------------------------------------------
    def is_locked(self) -> bool:
        self.break_stale_lock()
        return self.sync_locked_file.exists()

    def try_acquire_lock(self, user_id: str = "") -> bool:
        # Ensure the lock file exists
        if not self.sync_unlocked_file.exists() and not self.sync_locked_file.exists():
            self.set_unlocked_data(not_exists_ok=True)

        # Break any existing stale lock
        self.break_stale_lock()

        # Check if lock is taken
        if self.sync_locked_file.exists():
            print("Lock is currently held by another process")
            return False

        try:
            if self.sync_unlocked_file.exists():
                # Try to acquire the lock by renaming the file and writing data to it
                self.sync_unlocked_file.rename(self.sync_locked_file)
                lock_data: dict = {
                    "status": "locked",
                    "acquired": datetime.now().isoformat(),
                    "user": user_id,
                    "hostname": gethostname(),
                    "pid": getpid(),
                }
                if not self.write_atomic(self.sync_locked_file, lock_data):
                    print("Lock writing data failed! Another process may have taken lock")
                    return False

                # Check if lock file contains expected content
                with open(self.sync_locked_file, "r") as f:
                    content: dict = json.load(f)
                    if not content == lock_data:
                        print("Lock verification failed! Another process may have taken it")
                        return False

                print("Successfully acquired lock")
                return True
            else:
                # Check for stale lock before giving up and try again if unlocked file appears
                self.break_stale_lock()
                if self.sync_unlocked_file.exists():
                    return self.try_acquire_lock(user_id)
                return False
        except json.JSONDecodeError:
            print("Could not verify locked file contents")
            return False
        except OSError:
            print("Failed to acquire lock, another process got it first")
            return False

    def break_stale_lock(self):
        if not self.sync_locked_file.exists():
            return

        is_stale: bool = False
        try:
            with open(self.sync_locked_file, "r") as f:
                data: dict = json.load(f)
            lock_time = datetime.fromisoformat(data.get("acquired", ""))
            time_elapsed = (datetime.now() - lock_time).total_seconds()
            is_stale = time_elapsed > self.sync_lock_timeout
        except Exception:
            lock_time = self.sync_locked_file.stat().st_mtime
            is_stale = time.time() - lock_time > self.sync_lock_timeout

        if is_stale:
            try:
                print("Breaking stale lock")
                self.sync_locked_file.rename(self.sync_unlocked_file)
                self.set_unlocked_data()
            except Exception as e:
                print(f"Could not break stale lock, another process may have started breaking it : {e}")

    def release_lock(self) -> bool:
        if self.sync_locked_file.exists():
            try:
                self.sync_locked_file.rename(self.sync_unlocked_file)
                self.set_unlocked_data()
                print("Lock released")
                return True
            except Exception:
                print("Failed to release lock")
                return False
        return False

    def set_unlocked_data(self, not_exists_ok: bool = False) -> None:
        if not not_exists_ok and not self.sync_unlocked_file.exists():
            return

        timestamp = datetime.now().isoformat()
        unlocked_data = {"status": "unlocked", "created": timestamp}
        if not self.write_atomic(self.sync_unlocked_file, unlocked_data):
            print("Failed to set unlocked data")

    # ------------------------------------------------------------------------------------------
    # Functions for managing export folders
    # ------------------------------------------------------------------------------------------
    def set_active_export_folder(self, folder_index: int) -> bool:
        folder_index = folder_index % len(self.export_folders)
        folder_name = self.export_folders[folder_index].name
        content = {
            "active_folder_index": folder_index,
            "active_folder_name": folder_name,
            "updated": datetime.now().isoformat(),
        }
        if not self.write_atomic(self.control_file, content):
            print(f"Error updating active export folder to {folder_name}")
            return False

        return True

    def get_active_export_folder(self) -> tuple[int, Path]:
        try:
            if self.control_file.exists():
                with open(self.control_file, "r") as f:
                    data: dict = json.load(f)
                    folder_index = data.get("active_folder_index", 0)
                    return folder_index, self.export_folders[folder_index]
            else:
                self.set_active_export_folder(0)
                return 0, self.export_folders[0]
        except Exception as e:
            print(f"Error reading active folder, using default: {e}")
            return 0, self.export_folders[0]


if __name__ == "__main__":
    central_log = CentralLogs(".")
    central_log.trigger_sync("Pytre_Logs_User.db", "id_test", "name_test")
    for i in range(10):
        time.sleep(30)
        if central_log.sync_thread is None or not central_log.sync_thread.is_alive():
            break
