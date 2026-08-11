import sqlite3
import logging
from contextlib import closing
from pathlib import Path

from Pytre_Core.utils import log as _log


def log(msg: str | object, level: int = logging.INFO, sublogger: str = __name__) -> None:
    _log(msg, level=level, sublogger=sublogger)


class BaseLogs:
    """Base SQLite class for storing logs with version update mechanism."""

    LATEST_VERSION: int = 0

    def __init__(self, db_path: Path | str):
        self.db_path: Path = Path(db_path)
        self.version: int = -1
        self.latest_version: int = self.LATEST_VERSION
        self.update_already_run: bool = False

    def check_db(self, create: bool = True) -> bool:
        result: bool = False
        if not self.db_path.exists():
            if create:
                result = self.create_db()
        else:
            self.version = self.get_version()
            result = self.update_db()

        self.update_already_run = True
        return result

    def get_version(self) -> int:
        # fetch db user version
        try:
            # sqlite3 connection context manage don't close connection but commit or rollback automatically
            with closing(sqlite3.connect(self.db_path)) as conn, closing(conn.cursor()) as cursor:
                cursor.execute("PRAGMA user_version;")
                version = cursor.fetchone()[0]
                return version
        except Exception as e:
            log(f"Couldn't retrieve user_version pragma for {self.db_path.name}: {e}", logging.ERROR)
            return -1

    def create_db(self) -> bool:
        """To override : create schema and set current_version to latest_version."""
        raise NotImplementedError

    def update_db(self) -> bool:
        if self.update_already_run:
            return True
        if not self.version > -1:
            return False
        if not self.version < self.latest_version:
            return True

        for version in range(self.latest_version):
            if self.version >= version + 1:
                continue

            update_func = getattr(self, f"update_db_{version}_to_{version + 1}")
            if not update_func():
                log(f"Aborting {self.db_path.name} update, migration to {version + 1} failed", logging.ERROR)
                return False

        log(f"{self.db_path.name} migration to version {self.latest_version} completed")
        return True
