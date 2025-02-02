import sqlite3
import json
from datetime import datetime, timedelta
from pathlib import Path

import settings


LOG_FILE: Path = settings.USER_FOLDER / "Pytre_Logs.db"


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
    query: str, start: datetime, end: datetime = None, nb_rows: float = None, params: dict = None, file: str = ""
) -> None:
    if not LOG_FILE.exists():
        create_db()

    log_start: str = start.isoformat()
    log_end: str = end.isoformat() if end is not None else None
    log_duration: float = (end - start).total_seconds() if end else None
    log_params = json.dumps(params, indent=4, ensure_ascii=False) if params else None
    log_file = str(file)  # si un objet Path est retourné il ne peut pas être insérer

    try:
        with sqlite3.connect(LOG_FILE) as conn:
            # insertion infos
            conn.execute(
                f"""INSERT INTO QUERIES_EXEC (QUERY, START, END, DURATION_SECS, NB_ROWS, PARAMETERS, FILE)
                    VALUES (?, ?, ?, ?, ?, ?, ?);""",
                (query, log_start, log_end, log_duration, nb_rows, log_params, log_file),
            )

            # nettoyage pour ne garder que les 250 requêtes les plus récentes
            conn.execute(
                """DELETE FROM QUERIES_EXEC
                WHERE START IN (SELECT START FROM QUERIES_EXEC ORDER BY START DESC LIMIT -1 OFFSET 250)"""
            )

            conn.commit()
    except sqlite3.OperationalError as e:
        print(f"SQLite operational error : {e}")


if __name__ == "__main__":
    start = datetime.now() - timedelta(seconds=5)
    i = 0
    while i < 100000000:
        i += 1
    end = datetime.now()
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
