"""
Tests for Pytre_Server/logs.py
"""

import logging
from logging.handlers import RotatingFileHandler

import pytest

from Pytre_Core.utils import LOGGER_ROOT_NAME, log as pytre_log
from Pytre_Server.logs import BACKUP_COUNT, MAX_BYTES, setup_file_logging, setup_terminal_logging


pytestmark = pytest.mark.usefixtures("pristine_pytre_logger")  # see tests/conftest.py


@pytest.mark.unit
def test_setup_file_logging_creates_parent_directory(tmp_path):
    log_file = tmp_path / "nested" / "server.log"

    setup_file_logging(log_file)

    assert log_file.parent.exists()


@pytest.mark.unit
def test_setup_file_logging_attaches_a_rotating_handler(tmp_path):
    log_file = tmp_path / "server.log"
    logger = logging.getLogger(LOGGER_ROOT_NAME)
    before = set(logger.handlers)

    setup_file_logging(log_file)

    new_handlers = [h for h in logger.handlers if h not in before]
    assert len(new_handlers) == 1
    handler = new_handlers[0]
    assert isinstance(handler, RotatingFileHandler)
    assert handler.maxBytes == MAX_BYTES
    assert handler.backupCount == BACKUP_COUNT


@pytest.mark.unit
def test_setup_file_logging_defaults_to_info_level(tmp_path):
    log_file = tmp_path / "server.log"

    setup_file_logging(log_file)

    assert logging.getLogger(LOGGER_ROOT_NAME).level == logging.INFO


@pytest.mark.unit
def test_setup_file_logging_respects_custom_level(tmp_path):
    log_file = tmp_path / "server.log"

    setup_file_logging(log_file, level=logging.WARNING)

    assert logging.getLogger(LOGGER_ROOT_NAME).level == logging.WARNING


@pytest.mark.unit
def test_setup_file_logging_writes_messages_to_file(tmp_path):
    log_file = tmp_path / "server.log"
    setup_file_logging(log_file)

    pytre_log("hello from a test", level=logging.INFO, sublogger="test_logs")
    for handler in logging.getLogger(LOGGER_ROOT_NAME).handlers:
        handler.flush()

    content = log_file.read_text(encoding="utf-8")
    assert "hello from a test" in content


@pytest.mark.unit
def test_setup_file_logging_filters_messages_below_level(tmp_path):
    log_file = tmp_path / "server.log"
    setup_file_logging(log_file, level=logging.WARNING)

    pytre_log("should not appear", level=logging.INFO, sublogger="test_logs")
    pytre_log("should appear", level=logging.WARNING, sublogger="test_logs")
    for handler in logging.getLogger(LOGGER_ROOT_NAME).handlers:
        handler.flush()

    content = log_file.read_text(encoding="utf-8")
    assert "should not appear" not in content
    assert "should appear" in content


@pytest.mark.unit
def test_setup_file_logging_writes_utf8_content(tmp_path):
    log_file = tmp_path / "server.log"
    setup_file_logging(log_file)

    pytre_log("café à côté", level=logging.INFO, sublogger="test_logs")
    for handler in logging.getLogger(LOGGER_ROOT_NAME).handlers:
        handler.flush()

    content = log_file.read_text(encoding="utf-8")
    assert "café à côté" in content


@pytest.mark.unit
def test_setup_terminal_logging_attaches_a_stream_handler():
    logger = logging.getLogger(LOGGER_ROOT_NAME)
    before = set(logger.handlers)

    setup_terminal_logging()

    new_handlers = [h for h in logger.handlers if h not in before]
    assert len(new_handlers) == 1
    assert isinstance(new_handlers[0], logging.StreamHandler)


@pytest.mark.unit
def test_setup_terminal_logging_defaults_to_info_level():
    setup_terminal_logging()

    assert logging.getLogger(LOGGER_ROOT_NAME).level == logging.INFO


@pytest.mark.unit
def test_setup_terminal_logging_respects_custom_level():
    setup_terminal_logging(level=logging.WARNING)

    assert logging.getLogger(LOGGER_ROOT_NAME).level == logging.WARNING


@pytest.mark.unit
def test_setup_terminal_logging_writes_messages_to_stderr(capsys):
    setup_terminal_logging()

    pytre_log("hello from a test", level=logging.INFO, sublogger="test_logs")

    assert "hello from a test" in capsys.readouterr().err


@pytest.mark.unit
def test_setup_terminal_logging_filters_messages_below_level(capsys):
    setup_terminal_logging(level=logging.WARNING)

    pytre_log("should not appear", level=logging.INFO, sublogger="test_logs")
    pytre_log("should appear", level=logging.WARNING, sublogger="test_logs")

    err = capsys.readouterr().err
    assert "should not appear" not in err
    assert "should appear" in err


# ================================================================================
# Combining setup_file_logging() and setup_terminal_logging()
# ================================================================================


@pytest.mark.unit
def test_combining_terminal_then_file_logging_uses_the_most_permissive_level(tmp_path):
    """Regression: attaching a second handler with a stricter level must not raise the
    logger's own level and silently mute a handler that wanted more verbosity."""
    setup_terminal_logging(level=logging.DEBUG)
    setup_file_logging(tmp_path / "server.log", level=logging.WARNING)

    assert logging.getLogger(LOGGER_ROOT_NAME).level == logging.DEBUG


@pytest.mark.unit
def test_combining_file_then_terminal_logging_uses_the_most_permissive_level(tmp_path):
    """Same guarantee, opposite call order."""
    setup_file_logging(tmp_path / "server.log", level=logging.WARNING)
    setup_terminal_logging(level=logging.DEBUG)

    assert logging.getLogger(LOGGER_ROOT_NAME).level == logging.DEBUG


@pytest.mark.unit
def test_second_setup_call_does_not_raise_an_already_lower_level(tmp_path):
    """A second, stricter setup call must not mute a handler already relying on the lower
    (more permissive) level the logger was first given."""
    setup_file_logging(tmp_path / "a.log", level=logging.WARNING)
    setup_file_logging(tmp_path / "b.log", level=logging.ERROR)

    assert logging.getLogger(LOGGER_ROOT_NAME).level == logging.WARNING


@pytest.mark.unit
def test_terminal_and_file_logging_filter_independently_once_combined(tmp_path, capsys):
    """End-to-end: each handler applies its own level once the logger itself lets a record
    through - the terminal (DEBUG) sees everything, the file (WARNING) only sees warnings+."""
    log_file = tmp_path / "server.log"
    setup_terminal_logging(level=logging.DEBUG)
    setup_file_logging(log_file, level=logging.WARNING)

    pytre_log("debug detail", level=logging.DEBUG, sublogger="test_logs")
    pytre_log("a warning", level=logging.WARNING, sublogger="test_logs")
    for handler in logging.getLogger(LOGGER_ROOT_NAME).handlers:
        handler.flush()

    err = capsys.readouterr().err
    file_content = log_file.read_text(encoding="utf-8")

    assert "debug detail" in err
    assert "a warning" in err
    assert "debug detail" not in file_content
    assert "a warning" in file_content
