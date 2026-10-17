import itertools
import json
import logging
from collections import deque
from dataclasses import dataclass

from Pytre_Core.utils import LOGGER_ROOT_NAME
from Pytre_Server.logs import MAX_LOG_SIZE_MB


@dataclass(frozen=True, slots=True)
class LogEntry:
    """One application log record, as captured by LogBufferHandler."""

    seq: int  # monotonic, process-lifetime-unique for this handler instance
    timestamp: float  # record.created - epoch seconds UTC
    level: int  # record.levelno
    level_name: str  # record.levelname
    logger_name: str  # record.name
    message: str  # formatted message, includes traceback text if record.exc_info is set

    def to_dict(self) -> dict:
        return {
            "seq": self.seq,
            "timestamp": self.timestamp,
            "level": self.level,
            "level_name": self.level_name,
            "logger_name": self.logger_name,
            "message": self.message,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


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

        self._buffer.append(entry)
        self._total_bytes += len(message)

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


def setup_log_buffer(max_mb: int = MAX_LOG_SIZE_MB) -> LogBufferHandler:
    """Idempotent: if a LogBufferHandler is already attached returns it unchanged (max_mb is then
    ignored - the buffer keeps whatever size it was first created with in this process).
    Otherwise creates, attaches, and returns a new one.

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

    return handler
