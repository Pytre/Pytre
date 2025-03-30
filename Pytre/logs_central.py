import sqlite3
import time
import uuid
import hashlib
import json
import random
from os import getpid, fsync
from socket import gethostname
from datetime import datetime
from threading import Thread, Event
from pathlib import Path

from singleton_metaclass import Singleton


CENTRAL_DB: Path = "Pytre_Central_Logs.db"
LATEST_VERSION: int = 2  # latest version model of central database


class CentralLogs(metaclass=Singleton):
    def __init__(self, logs_folder: Path = "."):
        self.sync_thread = None
        self.logs_folder: Path = Path(logs_folder)
        self.central_db: Path = self.logs_folder / CENTRAL_DB

        self.user_db: Path = None
        self.user_id: str = ""
        self.user_name: str = ""

        self.temp_files: list[Path] = []
        self.stop_event: Event = Event()

        self.central_version: int = -1
        self.latest_version: int = LATEST_VERSION
        self.update_already_run: bool = False

    def __del__(self):
        self.stop_sync(5)
        self.cleanup_temp_files()

    def check_db(self, create: bool = True) -> bool:
        result: bool = False
        if not self.central_db.exists():
            if create:
                result = self.create_db()
        else:
            self.central_version = self.get_central_version()
            result = self.update_db()

        self.update_already_run = True
        return result

    def get_central_version(self) -> int:
        # fetch current user version
        try:
            with sqlite3.connect(self.central_db) as conn:
                cursor = conn.cursor()
                cursor.execute("PRAGMA user_version;")
                user_version = cursor.fetchone()[0]
                cursor.close()
                return user_version
        except Exception as e:
            print(f"Couldn't retrieve user_info pragma for central db: {e}")
            return -1

    # ------------------------------------------------------------------------------------------
    # Database management
    # ------------------------------------------------------------------------------------------
    def create_db(self):
        try:
            with sqlite3.connect(f"file:{self.central_db}?mode=rwc", uri=True) as conn:
                conn.execute(f"PRAGMA user_version = {self.latest_version};")
                conn.execute(
                    """
                        CREATE TABLE QUERIES_EXEC (
                            SERVER_ID        TEXT,
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
                conn.execute("CREATE INDEX IDX_SERVER_ID ON QUERIES_EXEC (SERVER_ID);")
                conn.execute("CREATE INDEX IDX_QUERY ON QUERIES_EXEC (QUERY);")
                conn.execute("CREATE INDEX IDX_START ON QUERIES_EXEC (START DESC);")

                conn.commit()
                self.central_version = self.latest_version
        except sqlite3.OperationalError as e:
            print(f"The central SQLite log database could not be created: {e}")

    def update_db(self) -> bool:
        if self.update_already_run:
            return True
        if not self.central_version > -1:
            return False
        if not self.central_version < self.latest_version:
            return True

        for version in range(self.latest_version):
            if self.central_version >= version + 1:
                continue

            update_func = getattr(self, f"update_db_{version}_to_{version + 1}")
            if not update_func():
                print(f"Aborting central database update, migration to {version + 1} failed")
                return False

        print(f"Central database migration to version {self.latest_version} completed")
        return True

    def update_db_0_to_1(self) -> bool:
        try:
            new_version: int = 1

            with sqlite3.connect(f"file:{self.central_db}?mode=rw", uri=True) as conn:
                conn.execute(f"PRAGMA user_version = {new_version};")
                conn.execute("ALTER TABLE QUERIES_EXEC ADD COLUMN SERVER_ID TEXT;")
                conn.execute("DROP INDEX IF EXISTS IDX_SERVER_ID;")
                conn.execute("CREATE INDEX IDX_SERVER_ID ON QUERIES_EXEC (SERVER_ID);")
                conn.commit()
                print("Added SERVER_ID column to QUERIES_EXEC table")

                print(f"Central database updated to version {new_version}")
                self.central_version = new_version
                return True
        except Exception as e:
            print(f"Unexpected error in schema update to version {new_version}: {e}")
            return False

    def update_db_1_to_2(self) -> bool:
        try:
            new_version: int = 2

            with sqlite3.connect(self.central_db) as conn:
                conn.execute(f"PRAGMA user_version = {new_version};")
                conn.execute("ALTER TABLE QUERIES_EXEC DROP COLUMN END;")
                conn.commit()
                print(f"Central database updated to version {new_version}")
                self.central_version = new_version
                return True
        except Exception as e:
            print(f"Unexpected error in schema update to version {new_version}: {e}")
            return False

    # ------------------------------------------------------------------------------------------
    # Database select and insert sql commands
    # ------------------------------------------------------------------------------------------
    def sql_rows_get_unsynced(self) -> str:
        return f"""
            SELECT 
                ROWID, SERVER_ID,
                '{self.user_id}' AS USER_ID, '{self.user_name}' AS USER_NAME, 
                QUERY, START, DURATION_SECS, NB_ROWS, PARAMETERS 
            FROM
                QUERIES_EXEC 
            WHERE
                EXPORTED = 0
        """

    def sql_mark_rows_as_exported(self, place_holders) -> str:
        return f"UPDATE QUERIES_EXEC SET EXPORTED = 1 WHERE ROWID IN ({place_holders})"

    def sql_insert_into_central_db(self) -> str:
        return """
            INSERT INTO QUERIES_EXEC (SERVER_ID, USER_ID, USER_NAME, QUERY, START, DURATION_SECS, NB_ROWS, PARAMETERS)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """

    # ------------------------------------------------------------------------------------------
    # Thread management
    # ------------------------------------------------------------------------------------------
    def trigger_sync(self, user_db: Path, user_id: str = "", user_name: str = ""):
        if not self.central_db:
            print("Error: Central log database not specified")
            return

        if not self.check_db():
            print(f"Aborting sync, failed to check central database: {self.central_db}")
            return

        if not user_db or not Path(user_db).exists():
            print(f"Error: User log database does not exist: {user_db}")
            return

        if self.sync_thread is None or not self.sync_thread.is_alive():
            self.stop_event.clear()
            self.user_db = Path(user_db)
            self.user_id = user_id
            self.user_name = user_name

            self.sync_thread: Thread = Thread(target=self.sync_thread_start, daemon=True)
            self.sync_thread.start()

    def stop_sync(self, timeout=5) -> bool:
        self.stop_event.set()
        if self.sync_thread and self.sync_thread.is_alive():
            self.sync_thread.join(timeout)
            return not self.sync_thread.is_alive()

        self.cleanup_temp_files()

        return True

    # ------------------------------------------------------------------------------------------
    # Methods to implement in child class
    # ------------------------------------------------------------------------------------------
    def sync_thread_start(self):
        pass

    def cleanup_temp_files(self):
        pass


class FileDriven(CentralLogs):
    def __init__(self, logs_folder: Path = "."):
        super().__init__(logs_folder)

        self.queue_ctrl_file: Path = self.logs_folder / "Pytre_Queue.json"
        self.export_folders: list[Path] = [self.logs_folder / "Pytre_Queue_1", self.logs_folder / "Pytre_Queue_2"]
        self.failed_folder: Path = self.logs_folder / "Pytre_Failed"

        self.sync_unlocked_file: Path = self.logs_folder / "Pytre_Unlocked.json"
        self.sync_locked_file: Path = self.logs_folder / "Pytre_Locked.json"
        self.sync_lock_timeout: int = 120  # seconds since last heartbeat

        self.sync_heartbeat: int = 90  # seconds after which heartbeat will be updated
        self.stop_heartbeat: Event = Event()

    def sync_thread_start(self):
        print("Thread logs sync starting")

        if not self.create_dirs(self.export_folders):
            print("Error: required folders does not exist and failed to create them")
            return

        retries_delay: list[int] = [30, 60, 120]  # in seconds
        for attempt, delay in enumerate(retries_delay):
            try:
                sync_result: bool = self.synchronization_attempt()
                if sync_result:
                    print("Synchronization completed successfully")
                    break
            except Exception as e:
                print(f"Unexpected error while syncing logs : {e}")

            if self.stop_event.is_set():
                break
            if attempt == len(retries_delay) - 1:
                print("Max retries reached")
                break

            jitter_factor = 0.8 + (0.4 * random.random())  # to add jitter to retry delay
            print(f"Next attempt in {delay * jitter_factor:.2f} sec")
            time.sleep(delay * jitter_factor)

        self.sync_thread = None
        print("Thread logs sync ending")

    def synchronization_attempt(self) -> bool:
        self.export_unsynced_data()

        nb_to_sync: int = 0
        for folder in self.export_folders:
            nb_to_sync += len(list(folder.glob("*.json")))
        if nb_to_sync == 0:
            print("Stopping sync, no exported files to process")
            return False

        if not self.try_acquire_lock():
            print("Lock could not be acquired")
        else:
            try:
                active_index, folder_to_process = self.get_active_export_folder()
                if not self.set_active_export_folder(active_index + 1):  # change active folder
                    raise Exception("Couldn't change active folder")
                if not self.wait_for_folder_completion(folder_to_process):
                    raise Exception("Timed out while waiting for folder completion")
                if not self.process_files(folder_to_process):
                    raise Exception("Problem occured while processing exported files")

                print("Successfully processed export files")
                return True
            except Exception as e:
                print(f"Error : {e}")
            finally:
                self.release_lock()

    # ------------------------------------------------------------------------------------------
    # Functions to export data to sync into files
    # ------------------------------------------------------------------------------------------
    def export_unsynced_data(self) -> bool:
        if self.stop_event.is_set():
            return True

        _, active_folder = self.get_active_export_folder()
        if not active_folder.exists() and not self.create_dirs(active_folder):
            print(f"Error: required folder do not exist and failed to create it: {active_folder}")
            return False

        try:
            unsynced = self.rows_get_unsynced()
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
            print(f"Error during data export: {e}")
            return False

    def rows_get_unsynced(self) -> list[tuple] | None:
        if self.stop_event.is_set():
            return None

        with sqlite3.connect(f"file:{self.user_db}?mode=ro", uri=True) as conn:
            cursor: sqlite3.Cursor = conn.cursor()
            sql_cmd = self.sql_rows_get_unsynced()
            cursor.execute(sql_cmd)
            unsynced = cursor.fetchall()
            cursor.close()

        return unsynced

    def mark_rows_as_exported(self, rows_id: list[int]):
        with sqlite3.connect(f"file:{self.user_db}?mode=rw", uri=True) as conn:
            cursor: sqlite3.Cursor = conn.cursor()

            place_holders = ", ".join("?" * len(rows_id))
            sql_cmd = self.sql_mark_rows_as_exported(place_holders)
            cursor.execute(sql_cmd, rows_id)

            conn.commit()
            cursor.close()

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
            if self.stop_event.is_set():
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

    def process_files(self, folder_to_process: Path) -> bool:
        if self.stop_event.is_set():
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
        rows_values, processed_files = self.files_extract_infos(exported_files)

        if rows_values:
            success = self.insert_into_central_db(rows_values)
            if success:
                self.processed_files_remove(processed_files)
            else:
                print("Failed to insert into central database, moving files to active folder...")
                _, active_folder = self.get_active_export_folder()
                self.processed_files_move(processed_files, active_folder)

        print("Export processing completed")

        return True

    def files_extract_infos(self, exported_files: list[Path]) -> tuple[list[str], list[Path]]:
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
            self.failed_files_move(failed_files, timestamp)

        return rows_values, processed_files

    def failed_files_move(self, failed_files: list[Path], timestamp: str):
        if not self.create_dirs(self.failed_folder):
            print("Error: failed folder doesn't exist and unable to create it")
            return

        for file in failed_files:
            new_name = f"{file.stem}_{timestamp}{file.suffix}"
            file.rename(self.failed_folder / new_name)

    def processed_files_remove(self, processed_files: list[Path]):
        for file in processed_files:
            try:
                file.unlink()
            except Exception as e:
                print(f"Error deleting processed file {file}: {e}")

    def processed_files_move(self, processed_files: list[Path], active_folder: Path):
        if not active_folder.exists():
            print(f"Aborting moving files, active folder doesn't exist: {active_folder}")
            return

        for file in processed_files:
            file.rename(active_folder / file.name)

    def insert_into_central_db(self, rows_values: list[tuple]) -> bool:
        if self.stop_event.is_set():
            return False

        if not self.update_db():
            return False

        try:
            with sqlite3.connect(f"file:{self.central_db}?mode=rw", uri=True, timeout=60) as conn:
                conn.execute("PRAGMA busy_timeout=10000")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("PRAGMA cache_size=-10000")

                cursor: sqlite3.Cursor = conn.cursor()
                cursor.execute("BEGIN IMMEDIATE")

                sql_cmd = self.sql_insert_into_central_db()
                cursor.executemany(sql_cmd, rows_values)
                conn.commit()

                cursor.close()
                return True
        except sqlite3.DatabaseError as e:
            print(f"Database error while accessing central database: {e}")
            return False
        except sqlite3.OperationalError as e:
            print(f"Operational error while writing to central database: {e}")
            return False
        except Exception as e:
            print(f"Unexpected error while syncing to central database: {e}")
            return False

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
            print("Lock is currently held by another process")
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
                    print("Lock writing data failed! Another process may have taken lock")
                    return False

                # Check if lock file contains expected content
                with open(self.sync_locked_file, "r") as f:
                    content: dict = json.load(f)
                    if not content == lock_data:
                        print("Lock verification failed! Another process may have taken it")
                        return False

                self.heartbeat_thread: Thread = self.start_heartbeat_thread()
                print("Successfully acquired lock")
                return True
            else:
                # Check for stale lock before giving up and try again if unlocked file appears
                self.break_stale_lock()
                if self.sync_unlocked_file.exists():
                    return self.try_acquire_lock()
                return False
        except json.JSONDecodeError:
            print("Could not verify locked file contents")
            return False
        except OSError:
            print("Failed to acquire lock, another process got it first")
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
                print(f"Breaking stale lock (last heartbeat {time_since_heartbeat:.2f} secs ago)")
                self.sync_locked_file.rename(self.sync_unlocked_file)
                self.set_unlocked_data()
                return True
        except Exception as e:
            print(f"Error while breaking/checking stale lock: {e}")
            return False

    def release_lock(self) -> bool:
        if self.sync_locked_file.exists():
            try:
                self.stop_heartbeat.set()

                # remove unlocked_file if it exists when it shouldn't
                if self.sync_unlocked_file.exists():
                    self.sync_unlocked_file.unlink()

                self.sync_locked_file.rename(self.sync_unlocked_file)
                self.set_unlocked_data()
                print("Lock released")
                return True
            except Exception as e:
                print(f"Failed to release lock: {e}")
                return False
        return False

    def set_unlocked_data(self, not_exists_ok: bool = False) -> None:
        if not not_exists_ok and not self.sync_unlocked_file.exists():
            return

        timestamp = datetime.now().isoformat()
        unlocked_data = {"status": "unlocked", "created": timestamp, "user": self.user_id}
        if not self.write_atomic(self.sync_unlocked_file, unlocked_data):
            print("Failed to set unlocked data")

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
            print(f"Heartbeat update failed: {e}")
            return False

    # ------------------------------------------------------------------------------------------
    # Files and folders management
    # ------------------------------------------------------------------------------------------
    def write_atomic(self, target_path: Path, content: str | dict) -> bool:
        temp_file = Path(target_path).with_name(f"{target_path.name}.{uuid.uuid4()}.tmp")
        self.temp_files.append(temp_file)
        try:
            # Writing content to temp file using binary mode for consistency
            with open(temp_file, "wb") as f:
                if isinstance(content, dict):
                    json_string: str = json.dumps(content, indent=4)
                    f.write(json_string.encode("utf-8"))
                else:
                    f.write(content.encode("utf-8"))

                # Ensure data is flushed to disk
                f.flush()
                fsync(f.fileno())

            # Atomic rename operation (more likely to be atomic even on network shares)
            temp_file.replace(target_path)
            self.temp_files.remove(temp_file)
            return True
        except FileExistsError:
            print(f"Temp file {temp_file} already exists")
            return False
        except PermissionError:
            print(f"Permission denied when writing to {temp_file}")
            return False
        except OSError as e:
            print(f"OS error when writing file {target_path}: {e}")
            return False
        except Exception as e:
            print(f"Unexpected error writing file {target_path}: {e}")
            return False
        finally:
            try:
                if temp_file.exists():
                    temp_file.unlink()
                    self.temp_files.remove(temp_file)
            except Exception:
                pass

    def cleanup_temp_files(self):
        for temp_file in list(self.logs_folder.glob("**/*.tmp")):
            # add files older than 1 hour as file to be cleaned up
            if time.time() - temp_file.stat().st_mtime > 3600:
                self.temp_files.append(temp_file)

        for temp_file in set(self.temp_files):
            try:
                if temp_file.exists():
                    temp_file.unlink()
                    print(f"Cleaned up temp file: {temp_file}")
            except Exception as e:
                print(f"Error cleaning up temp file {temp_file}: {e}")

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
                print(f"Warning: No permission to create directory: {dir_path}")
                success = False
            except OSError as e:
                print(f"OS error when creating directory {dir_path}: {e}")
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
            print(f"Error updating active export folder to {folder_name}")
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
            print(f"Error reading active folder, using default: {e}")
            return 0, self.export_folders[0]


if __name__ == "__main__":
    Path_Home: Path = Path.home() / "Pytre"
    central_log = FileDriven(Path_Home)
    central_log.trigger_sync(Path_Home / "Pytre_Logs.db", "id_test", "name_test")
    for i in range(10):
        time.sleep(30)
        if central_log.sync_thread is None or not central_log.sync_thread.is_alive():
            break
