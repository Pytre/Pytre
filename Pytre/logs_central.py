import sqlite3
import time
from threading import Thread
from pathlib import Path


CENTRAL_DB: Path = "Pytre_Logs.db"


class CentralLogs:
    def __init__(self, logs_folder: Path = "."):
        self.sync_thread = None
        self.central_db: Path = Path(logs_folder) / CENTRAL_DB

    def create_db(self):
        try:
            with sqlite3.connect(self.central_db) as conn:
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
            print(f"La base SQLite centrale de log n'a pas pu être créée : {e}")

    def trigger_sync(self, user_db: Path, user_id: str = "", user_name: str = ""):
        if self.central_db and not self.central_db.exists():
            self.create_db()

        if not self.central_db:
            print(f"Erreur base de logs centrale non renseignée")
            return
        if not user_db or not user_db.exists():
            print(f"Erreur base de logs utilisateur inexistante : {user_db}")
            return

        if self.sync_thread is None or not self.sync_thread.is_alive():
            args = (user_db, user_id, user_name)
            self.sync_thread = Thread(target=self._sync_thread_start, args=args, daemon=True)
            self.sync_thread.start()

    def _sync_thread_start(self, user_db: Path, user_id: str, user_name: str):  # démarrer par trigger_sync
        print("Thread logs sync starting")
        nb_try: int = 0
        while True:
            try:
                nb_try += 1
                unsynced = self.rows_get_unsynced(user_db, user_id, user_name)
                if not unsynced:
                    break
                elif nb_try > 1:
                    time.sleep(60)

                rows_id = [item[0] for item in unsynced]
                rows_values = [item[1:] for item in unsynced]

                self.sync_rows(rows_values)
                self.mark_rows_as_synced(user_db, rows_id)

            except Exception as e:
                print(f"Erreur de synchro des données dans la base de logs centrale : {e}")

        print("Thread logs sync ending")

    def rows_get_unsynced(self, user_db: Path, user_id: str, user_name: str) -> list[tuple] | None:
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

    def sync_rows(self, rows_values: list[tuple]):
        try:
            with sqlite3.connect(f"file:{self.central_db}?mode=rw", uri=True, timeout=15) as conn:
                cursor: sqlite3.Cursor = conn.cursor()
                cursor.execute("BEGIN TRANSACTION;")
                for record in rows_values:
                    cursor.execute(
                        """
                        INSERT INTO QUERIES_EXEC (USER_ID, USER_NAME, QUERY, START, END, DURATION_SECS, NB_ROWS, PARAMETERS)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        record,
                    )
                conn.commit()
                cursor.close()
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e):
                pass
            else:
                raise

    def mark_rows_as_synced(self, user_db: Path, rows_id: list[int]):
        with sqlite3.connect(f"file:{user_db}?mode=rw", uri=True) as conn:
            cursor: sqlite3.Cursor = conn.cursor()
            rows_to_update = ", ".join([str(id) for id in rows_id])
            cursor.execute(f"UPDATE QUERIES_EXEC SET EXPORTED = 1 WHERE ROWID IN ({rows_to_update})")
            conn.commit()
            cursor.close()


if __name__ == "__main__":
    central_log = CentralLogs(".")
    central_log.trigger_sync("Pytre_Logs_User.db", "id_test", "name_test")
