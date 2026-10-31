import itertools
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Self

from Pytre_Core.utils import LOGGER_ROOT_NAME, log as _log
from Pytre_Server.logs import ENCODING, MAX_LOG_SIZE_MB


SEPARATOR_MESSAGE = str("-") * 25 + " Logs below were loaded from files and may be incomplete " + str("-") * 25


def log(msg: str | object, level: int = logging.INFO, sublogger: str = __name__) -> None:
    _log(msg, level=level, sublogger=sublogger)


@dataclass(frozen=True, slots=True)
class LogEntry:
    """One application log record, as captured by LogBufferHandler."""

    seq: int  # monotonic, process-lifetime-unique for this handler instance
    timestamp: float  # record.created - epoch seconds UTC
    level: int  # record.levelno
    level_name: str  # record.levelname
    logger_name: str  # record.name
    message: str  # formatted message, includes traceback text if record.exc_info is set
    is_separator: bool = False  # display-only boundary in the logs page, not a real log record

    def to_dict(self) -> dict:
        return {
            "seq": self.seq,
            "timestamp": self.timestamp,
            "level": self.level,
            "level_name": self.level_name,
            "logger_name": self.logger_name,
            "message": self.message,
            "is_separator": self.is_separator,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def parse_line(cls, line: str, seq: int) -> Self | None:
        """Builds an entry from a line written with LOG_FORMAT. None if the line doesn't have that format,
        such as a traceback's continuation line."""
        parts = line.split(" - ", 3)
        if len(parts) != 4:
            return None

        asctime, level_name, logger_name, message = parts
        level = logging.getLevelNamesMapping().get(level_name)
        if level is None:
            return None

        try:
            timestamp = datetime.fromisoformat(asctime).timestamp()
        except ValueError:
            return None

        return cls(
            seq=seq,
            timestamp=timestamp,
            level=level,
            level_name=level_name,
            logger_name=logger_name,
            message=message,
        )


class LogBufferHandler(logging.Handler):
    """Bounded buffer of record - backs the admin webui's live Logs page.
    Always captures at DEBUG (its own level), the admin webui filters by level client-side.

    Bounded by approximate total message bytes (not entry count). Uses len(message) for an
    entry's size rather than precisely measuring real footprint.

    emit() is already called under self.lock by the base Handler.handle() - the write side needs no
    extra synchronization. Read methods explicitly take the same self.lock themselves, since they
    run entirely outside logging's own call path (from route code)."""

    def __init__(self, max_bytes: int) -> None:
        super().__init__(level=logging.DEBUG)
        self._max_bytes = max_bytes
        self._buffer: deque[LogEntry] = deque()
        self._total_bytes = 0
        self._seq = itertools.count(1)
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        message = self.format(record)
        entry = LogEntry(
            seq=next(self._seq),
            timestamp=record.created,
            level=record.levelno,
            level_name=record.levelname,
            logger_name=record.name,
            message=message,
        )

        self._append(entry)

    def _append(self, entry: LogEntry) -> None:
        self._buffer.append(entry)
        self._total_bytes += len(entry.message)

        while self._total_bytes > self._max_bytes and self._buffer:
            evicted = self._buffer.popleft()
            self._total_bytes -= len(evicted.message)

    def entries_latest(self, limit: int) -> list[LogEntry]:
        """Number of entries is determined using `limit`, newest first."""
        assert self.lock is not None
        with self.lock:
            return list(reversed(list(self._buffer)[-limit:]))

    def entries_since(self, after: int = 0) -> list[LogEntry]:
        """Entries with seq > after, oldest first. after=0 returns everything currently buffered."""
        assert self.lock is not None
        with self.lock:
            return [e for e in self._buffer if e.seq > after]

    def entries_before(self, before: int, limit: int) -> list[LogEntry]:
        """`limit` entries immediately preceding `before` (exclusive), newest first.
        Empty once nothing older is left."""
        assert self.lock is not None
        with self.lock:
            older = [e for e in self._buffer if e.seq < before]
        return list(reversed(older[-limit:]))

    def load_from_file(self, log_file: Path) -> None:
        """Prefills a newly created buffer from the rotated file then the active one, oldest first,
        followed by a separator entry if anything was loaded. Meant for startup, before other threads
        can log. The byte limit applies. An unreadable file does not raise, it's only logged."""
        errors: list[str] = []
        for path in (log_file.with_name(f"{log_file.name}.1"), log_file):
            try:
                self._load_lines(path)
            except FileNotFoundError:
                pass
            except OSError as e:
                errors.append(f"{path}: {e}")

        if self._buffer:
            separator = LogEntry(
                seq=next(self._seq),
                timestamp=time.time(),
                level=logging.INFO,
                level_name="",
                logger_name="",
                message=SEPARATOR_MESSAGE,
                is_separator=True,
            )
            self._append(separator)

        for error in errors:
            log(f"Could not load stored logs from {error}", logging.WARNING)

    def _load_lines(self, path: Path) -> None:
        """A line that isn't the start of an entry belongs to the current one (traceback), or is
        dropped if there is none yet. A skipped seq is harmless, it only has to keep increasing."""
        current: LogEntry | None = None

        with open(path, encoding=ENCODING, errors="replace") as file:
            for line in file:
                line = line.rstrip("\n")

                entry = LogEntry.parse_line(line, next(self._seq))
                if entry is None:
                    if current is not None:
                        current = replace(current, message=f"{current.message}\n{line}")
                    continue

                if current is not None:
                    self._append(current)

                current = entry

        if current is not None:
            self._append(current)


def setup_log_buffer(max_mb: int = MAX_LOG_SIZE_MB, log_file: Path | str | None = None) -> LogBufferHandler:
    """Idempotent: if a LogBufferHandler is already attached returns it unchanged (max_mb is then
    ignored - the buffer keeps whatever size it was first created with in this process).
    Otherwise creates, attaches, and returns a new one, prefilled from `log_file` if there is one.

    Must be idempotent: create_app() calls this unconditionally on every call, and tests build a
    fresh app per test function - a non-idempotent attach would leave one more handler on the shared,
    process-global logger per test, forever (handlers are never garbage collected once attached)."""
    logger = logging.getLogger(LOGGER_ROOT_NAME)

    for handler in logger.handlers:
        if isinstance(handler, LogBufferHandler):
            return handler

    handler = LogBufferHandler(max_bytes=max_mb * 1024**2)
    logger.addHandler(handler)

    if logger.level == logging.NOTSET or handler.level < logger.level:
        logger.setLevel(handler.level)

    if log_file is not None:
        handler.load_from_file(Path(log_file))

    return handler
