import sqlite3
import json
from datetime import datetime, timedelta
from pathlib import Path

import settings


LOG_FILE: Path = settings.USER_FOLDER / "Pytre_Logs.db"
LOG_MAX: int = 2500


class LogRecord:
    def __init__(self):
        self.id: int
        self.query: str
        self.start: datetime
        self.end: datetime
        self.duration: int
        self.nb_rows: int
        self.parameters: dict
        self.file: Path
        self.exported: int

    def __repr__(self):
        return str(
            {
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
                "query": self.query,
                "start": self.start,
                "duration": self.duration,
                "nb_rows": self.nb_rows,
                "exported": self.exported,
            }
        )


class LogStats:
    def __init__(self):
        self.query: str
        self.nb_run: int
        self.min_run: int
        self.max_run: int
        self.last_run: datetime

    def __repr__(self):
        return str(
            {
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
                "query": self.query,
                "nb_run": self.nb_run,
                "min_run": self.min_run,
                "max_run": self.max_run,
                "last_run": self.last_run,
            }
        )


def create_db() -> None:
    with sqlite3.connect(LOG_FILE) as conn:
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
        conn.execute("CREATE INDEX IDX_QUERY ON QUERIES_EXEC (QUERY);")
        conn.execute("CREATE INDEX START ON QUERIES_EXEC (START DESC);")
        conn.execute("CREATE INDEX IDX_EXPORTED ON QUERIES_EXEC (EXPORTED ASC);")

        conn.commit()


def insert_exec(
    query: str, start: datetime, end: datetime = None, nb_rows: float = None, params: dict = None, file: str = None
) -> None:
    if not LOG_FILE.exists():
        create_db()

    log_start: str = start.isoformat()
    log_end: str = end.isoformat() if end is not None else None
    log_duration: float = (end - start).total_seconds() if end else None
    log_params = json.dumps(params, indent=4, ensure_ascii=False) if params else None
    log_file = str(file) if file else None  # si un objet Path est retourné il ne peut pas être insérer

    try:
        with sqlite3.connect(LOG_FILE) as conn:
            # insertion infos
            conn.execute(
                """INSERT INTO QUERIES_EXEC (QUERY, START, END, DURATION_SECS, NB_ROWS, PARAMETERS, FILE)
                    VALUES (?, ?, ?, ?, ?, ?, ?);""",
                (query, log_start, log_end, log_duration, nb_rows, log_params, log_file),
            )

            # nettoyage pour ne garder que les requêtes les plus récentes
            conn.execute(
                f"""DELETE FROM QUERIES_EXEC
                WHERE START IN (SELECT START FROM QUERIES_EXEC ORDER BY START DESC LIMIT -1 OFFSET {LOG_MAX})"""
            )

            conn.commit()
    except sqlite3.OperationalError as e:
        print(f"SQLite operational error : {e}")


def get_last_files(nb_files: int = 10) -> list[Path]:
    if not LOG_FILE.exists():
        return []

    with sqlite3.connect(LOG_FILE) as conn:
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


def get_stats(query_name: str = "") -> list[LogStats]:
    if not LOG_FILE.exists():
        return []

    with sqlite3.connect(LOG_FILE) as conn:
        conn.row_factory = sqlite3.Row

        cursor: sqlite3.Cursor = conn.cursor()
        cursor.execute(
            """SELECT
                    QUERY,
                    COUNT(*) as NB_RUN,
                    MIN(DURATION_SECS) as MIN_RUN, MAX(DURATION_SECS) as MAX_RUN,
                    MAX(START) LAST_RUN
               FROM QUERIES_EXEC
               WHERE :query_name = "" OR QUERY = :query_name
               GROUP BY QUERY
               ORDER BY COUNT(*) DESC;""",
            {"query_name": query_name},
        )
        dict_results = cursor.fetchall()
        stats_lst = [row_to_stats(row) for row in dict_results]

    return stats_lst


def row_to_stats(row: dict) -> LogStats:
    stat = LogStats()

    stat.query = row["QUERY"]
    stat.nb_run = row["NB_RUN"]
    stat.min_run = row["MIN_RUN"]
    stat.max_run = row["MAX_RUN"]
    stat.last_run = datetime.fromisoformat(row["LAST_RUN"]) if row["LAST_RUN"] else None

    return stat


def get_last_records(query_name: str = "", nb_records: int = 100) -> list[LogRecord]:
    if not LOG_FILE.exists():
        return []

    with sqlite3.connect(LOG_FILE) as conn:
        conn.row_factory = sqlite3.Row

        cursor: sqlite3.Cursor = conn.cursor()
        cursor.execute(
            """SELECT ROWID, * FROM QUERIES_EXEC
               WHERE :query_name = "" OR QUERY = :query_name
               ORDER BY START DESC LIMIT :nb_records;""",
            {"query_name": query_name, "nb_records": nb_records},
        )
        dict_results = cursor.fetchall()
        records_lst = [row_to_logrecord(row) for row in dict_results]

    return records_lst


def row_to_logrecord(row: dict) -> LogRecord:
    record = LogRecord()

    record.id = row["ROWID"]
    record.query = row["QUERY"]
    record.start = datetime.fromisoformat(row["START"]) if row["START"] else None
    record.end = datetime.fromisoformat(row["END"]) if row["START"] else None
    record.duration = row["DURATION_SECS"]
    record.nb_rows = row["NB_ROWS"]
    record.parameters = row["PARAMETERS"]
    record.file = Path(row["FILE"]) if row["FILE"] else None
    record.exported = row["EXPORTED"]

    return record


if __name__ == "__main__":
    start = datetime.now() - timedelta(seconds=5)
    i = 0
    while i < 1000000:
        i += 1
    end = datetime.now()
    """
    insert_exec(
        "test2",
        start,
        end,
        50,
        {
            "1": {"description": "référentiel", "display": "aa"},
            "2": {"description": 4, "display": "4"},
            "3": {"description": "sfsf", "display": "random"},
        },
    )
    """
    records = get_stats(query_name="ZGL2")
    print(records)
