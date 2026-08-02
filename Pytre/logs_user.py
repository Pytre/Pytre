import sqlite3
import json
import logging
from datetime import datetime
from pathlib import Path

from Pytre import user_prefs
from Pytre.logs_base import BaseLogs
from Pytre.utils import log as _log, setup_standalone_logging

USER_DB: Path = user_prefs.CONFIG_FOLDER / "Pytre_Logs.db"
LATEST_VERSION: int = 2  # latest version model of user database
LOG_MAX: int = 2500


def log(msg: str | object, level: int = logging.INFO, sublogger: str = __name__) -> None:
    _log(msg, level=level, sublogger=sublogger)


class LogRecord:
    def __init__(self):
        self.id: int
        self.server: str
        self.query: str
        self.start: datetime | None
        self.duration: int
        self.nb_rows: int
        self.parameters: dict
        self.file: Path | None
        self.exported: int

    def __repr__(self):
        return str(
            {
                "server": self.server,
                "query": self.query,
                "start": self.start,
                "duration": self.duration,
                "nb_rows": self.nb_rows,
                "exported": self.exported,
            }
        )

    def __str__(self):
        return str(
            {
                "server": self.server,
                "query": self.query,
                "start": self.start,
                "duration": self.duration,
                "nb_rows": self.nb_rows,
                "exported": self.exported,
            }
        )


class LogStats:
    def __init__(self):
        self.server: str
        self.query: str
        self.nb_run: int
        self.min_run: int
        self.max_run: int
        self.last_run: datetime | None

    def __repr__(self):
        return str(
            {
                "server": self.server,
                "query": self.query,
                "nb_run": self.nb_run,
                "min_run": self.min_run,
                "max_run": self.max_run,
                "last_run": self.last_run,
            }
        )

    def __str__(self):
        return str(
            {
                "server": self.server,
                "query": self.query,
                "nb_run": self.nb_run,
                "min_run": self.min_run,
                "max_run": self.max_run,
                "last_run": self.last_run,
            }
        )


class UserLogs(BaseLogs):
    def __init__(self, user_db: Path | str = USER_DB, log_max: int = LOG_MAX):
        super().__init__(Path(user_db))

        self.log_max = log_max
        self.latest_version: int = LATEST_VERSION

    # ------------------------------------------------------------------------------------------
    # database creation and migration to newer versions
    # ------------------------------------------------------------------------------------------
    def create_db(self) -> bool:
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(f"PRAGMA user_version = {self.latest_version};")
                conn.execute(
                    """
                        CREATE TABLE QUERIES_EXEC (
                            SERVER_ID        TEXT,
                            QUERY            TEXT        NOT NULL,
                            START            TEXT        NOT NULL,
                            DURATION_SECS    INTEGER,
                            NB_ROWS          INTEGER,
                            PARAMETERS       TEXT,
                            FILE             TEXT,
                            EXPORTED         INTEGER     NOT NULL DEFAULT 0
                        );
                    """
                )
                conn.execute("CREATE INDEX IDX_QUERY ON QUERIES_EXEC (QUERY);")
                conn.execute("CREATE INDEX IDX_START ON QUERIES_EXEC (START DESC);")
                conn.execute("CREATE INDEX IDX_EXPORTED ON QUERIES_EXEC (EXPORTED ASC);")

                conn.commit()
                self.version = self.latest_version
                return True
        except Exception as e:
            log(f"Unexpected error during user database creation: {e}", logging.ERROR)
            return False

    def update_db_0_to_1(self) -> bool:
        new_version: int = 1
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(f"PRAGMA user_version = {new_version};")

                conn.execute("ALTER TABLE QUERIES_EXEC ADD COLUMN SERVER_ID TEXT;")
                conn.execute("DROP INDEX IF EXISTS IDX_SERVER_ID;")
                conn.execute("CREATE INDEX IDX_SERVER_ID ON QUERIES_EXEC (SERVER_ID);")

                conn.commit()
                log(f"User database updated to version {new_version}")
                self.version = new_version
                return True
        except Exception as e:
            log(f"Unexpected error in schema update to version {new_version}: {e}", logging.ERROR)
            return False

    def update_db_1_to_2(self) -> bool:
        new_version: int = 2
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(f"PRAGMA user_version = {new_version};")
                conn.execute("ALTER TABLE QUERIES_EXEC DROP COLUMN END;")
                conn.commit()
                log(f"User database updated to version {new_version}")
                self.version = new_version
                return True
        except Exception as e:
            log(f"Unexpected error in schema update to version {new_version}: {e}", logging.ERROR)
            return False

    # ------------------------------------------------------------------------------------------
    # insert methods
    # ------------------------------------------------------------------------------------------
    def insert_exec(
        self,
        server_id: str,
        query: str,
        start: datetime,
        end: datetime | None = None,
        nb_rows: float | None = None,
        params: dict | None = None,
        file: str | Path | None = None,
    ) -> None:
        self.check_db()

        log_start: str = start.isoformat()
        log_duration: float | None = (end - start).total_seconds() if end else None
        log_params = json.dumps(params, indent=4, ensure_ascii=False) if params else None
        log_file = str(file) if file else None  # si un objet Path est retourné il ne peut pas être insérer

        try:
            with sqlite3.connect(f"file:{self.db_path}?mode=rw", uri=True) as conn:
                # insertion infos
                conn.execute(
                    """INSERT INTO QUERIES_EXEC (SERVER_ID, QUERY, START, DURATION_SECS, NB_ROWS, PARAMETERS, FILE)
                        VALUES (?, ?, ?, ?, ?, ?, ?);""",
                    (server_id, query, log_start, log_duration, nb_rows, log_params, log_file),
                )

                # nettoyage pour ne garder que les requêtes les plus récentes
                conn.execute(
                    f"""DELETE FROM QUERIES_EXEC
                    WHERE START IN (SELECT START FROM QUERIES_EXEC ORDER BY START DESC LIMIT -1 OFFSET :log_max)""",
                    {"log_max": self.log_max},
                )

                conn.commit()
        except sqlite3.OperationalError as e:
            log(f"SQLite operational error : {e}", logging.ERROR)

    # ------------------------------------------------------------------------------------------
    # select methods
    # ------------------------------------------------------------------------------------------
    def get_last_files(self, nb_files: int = 10) -> list[Path]:
        if not self.check_db(False):
            return []

        with sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True) as conn:
            cursor: sqlite3.Cursor = conn.cursor()
            cursor.execute(
                """SELECT FILE FROM QUERIES_EXEC
                WHERE FILE IS NOT NULL AND FILE <> ""
                ORDER BY START DESC LIMIT :nb_files;""",
                {"nb_files": nb_files},
            )
            records = cursor.fetchall()
            files = [Path(file[0]) for file in records]

        return files

    def get_stats(self, query_name: str = "") -> list[LogStats]:
        if not self.check_db(False):
            return []

        with sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True) as conn:
            conn.row_factory = sqlite3.Row

            cursor: sqlite3.Cursor = conn.cursor()
            cursor.execute(
                """SELECT
                        SERVER_ID,
                        QUERY,
                        COUNT(*) as NB_RUN,
                        MIN(DURATION_SECS) as MIN_RUN, MAX(DURATION_SECS) as MAX_RUN,
                        MAX(START) LAST_RUN
                FROM QUERIES_EXEC
                WHERE :query_name = "" OR QUERY = :query_name
                GROUP BY SERVER_ID, QUERY
                ORDER BY COUNT(*) DESC;""",
                {"query_name": query_name},
            )
            dict_results = cursor.fetchall()
            stats_lst = [self.row_to_stats(row) for row in dict_results]

        return stats_lst

    def get_last_records(self, query_name: str = "", nb_records: int = 100) -> list[LogRecord]:
        if not self.check_db(False):
            return []

        with sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True) as conn:
            conn.row_factory = sqlite3.Row

            cursor: sqlite3.Cursor = conn.cursor()
            cursor.execute(
                """SELECT ROWID, * FROM QUERIES_EXEC
                WHERE :query_name = "" OR QUERY = :query_name
                ORDER BY START DESC LIMIT :nb_records;""",
                {"query_name": query_name, "nb_records": nb_records},
            )
            dict_results = cursor.fetchall()
            records_lst = [self.row_to_logrecord(row) for row in dict_results]

        return records_lst

    # ------------------------------------------------------------------------------------------
    # helper methods to convert a row into an object
    # ------------------------------------------------------------------------------------------
    def row_to_stats(self, row: dict) -> LogStats:
        stat = LogStats()

        stat.server = row["SERVER_ID"]
        stat.query = row["QUERY"]
        stat.nb_run = row["NB_RUN"]
        stat.min_run = row["MIN_RUN"]
        stat.max_run = row["MAX_RUN"]
        stat.last_run = datetime.fromisoformat(row["LAST_RUN"]) if row["LAST_RUN"] else None

        return stat

    def row_to_logrecord(self, row: dict) -> LogRecord:
        record = LogRecord()

        record.id = row["ROWID"]
        record.server = row["SERVER_ID"]
        record.query = row["QUERY"]
        record.start = datetime.fromisoformat(row["START"]) if row["START"] else None
        record.duration = row["DURATION_SECS"]
        record.nb_rows = row["NB_ROWS"]
        record.parameters = row["PARAMETERS"]
        record.file = Path(row["FILE"]) if row["FILE"] else None
        record.exported = row["EXPORTED"]

        return record


if __name__ == "__main__":
    setup_standalone_logging()
    mydb = UserLogs()
