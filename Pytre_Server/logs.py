import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from Pytre_Core.utils import LOGGER_ROOT_NAME
from Pytre_Server.audit import AUDIT_LOGGER_NAME, AuditJsonFormatter, iso_time


LOG_MAX_SIZE_MB = 5  # MB per file before rotating
LOG_BACKUP_COUNT = 1  # files kept in addition to the active one (when set to 0, current file has no size limit)
LOG_FORMAT = "%(asctime)s - %(levelname)s - %(name)s - %(message)s"

AUDIT_MAX_SIZE_MB = 10  # MB per audit file before rotating
AUDIT_BACKUP_COUNT = 20  # audit files kept in addition to the active one

ENCODING = "utf-8"
CONTINUE_INDENT = "\t"  # starts every line of a record after its first one


class IndentedFormatter(logging.Formatter):
    """Indents every line of a record after its first one, so only the first line of a record starts with its date.
    A line break slipped into a logged value (a username...) then can't forge a record, neither in the file nor in
    the web administration logs refilled from it. Tracebacks stay readable, under their record."""

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        return iso_time(record.created)

    def format(self, record: logging.LogRecord) -> str:
        return f"\n{CONTINUE_INDENT}".join(super().format(record).splitlines())


def setup_file_logging(
    log_file: Path | str,
    level: int = logging.INFO,
    max_mb: int = LOG_MAX_SIZE_MB,
    backup_count: int = LOG_BACKUP_COUNT,
) -> None:
    """Attaches a rotating file handler to the "Pytre" logger hierarchy"""

    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    handler = RotatingFileHandler(log_path, maxBytes=max_mb * 1024**2, backupCount=backup_count, encoding=ENCODING)
    handler.setFormatter(IndentedFormatter(LOG_FORMAT))
    handler.setLevel(level)

    logger = logging.getLogger(LOGGER_ROOT_NAME)
    logger.addHandler(handler)
    if logger.level == logging.NOTSET or level < logger.level:
        logger.setLevel(level)


def setup_terminal_logging(level: int = logging.INFO) -> None:
    """Attaches a console handler (stderr) to the "Pytre" logger hierarchy"""

    handler = logging.StreamHandler()
    handler.setFormatter(IndentedFormatter(LOG_FORMAT))
    handler.setLevel(level)

    logger = logging.getLogger(LOGGER_ROOT_NAME)
    logger.addHandler(handler)
    if logger.level == logging.NOTSET or level < logger.level:
        logger.setLevel(level)


def setup_audit_file_logging(
    log_file: Path | str,
    max_mb: int = AUDIT_MAX_SIZE_MB,
    backup_count: int = AUDIT_BACKUP_COUNT,
) -> None:
    """Attaches a rotating file handler of the audit events only (see audit.py), one JSON object per line,
    to the "Pytre.audit" logger. The events still propagate to the "Pytre" handlers.
    Both limits must be at least 1: with 0, the file would grow without limit."""
    if max_mb < 1 or backup_count < 1:
        raise ValueError(f"Audit file size and backup count must be at least 1: {max_mb} MB, {backup_count} backup")

    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    handler = RotatingFileHandler(log_path, maxBytes=max_mb * 1024**2, backupCount=backup_count, encoding=ENCODING)
    handler.setFormatter(AuditJsonFormatter())
    handler.setLevel(logging.INFO)

    logger = logging.getLogger(AUDIT_LOGGER_NAME)
    logger.addHandler(handler)
    if logger.level == logging.NOTSET or logging.INFO < logger.level:
        logger.setLevel(logging.INFO)
