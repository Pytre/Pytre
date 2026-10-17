import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from Pytre_Core.utils import LOGGER_ROOT_NAME

MAX_LOG_SIZE_MB = 10
MAX_BYTES = MAX_LOG_SIZE_MB * 1024**2  # 10 MB per file before rotating
BACKUP_COUNT = 0  # rotated files kept in addition to the active one
ENCODING = "utf-8"


def setup_file_logging(log_file: Path | str, level: int = logging.INFO) -> None:
    """Attaches a rotating file handler to the "Pytre" logger hierarchy"""

    log_path = Path(log_file)

    log_path.parent.mkdir(parents=True, exist_ok=True)

    handler = RotatingFileHandler(log_path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding=ENCODING)
    handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s"))
    handler.setLevel(level)

    logger = logging.getLogger(LOGGER_ROOT_NAME)
    logger.addHandler(handler)
    if logger.level == logging.NOTSET or level < logger.level:
        logger.setLevel(level)


def setup_terminal_logging(level: int = logging.INFO) -> None:
    """Attaches a console handler (stderr) to the "Pytre" logger hierarchy"""

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s"))
    handler.setLevel(level)

    logger = logging.getLogger(LOGGER_ROOT_NAME)
    logger.addHandler(handler)
    if logger.level == logging.NOTSET or level < logger.level:
        logger.setLevel(level)
