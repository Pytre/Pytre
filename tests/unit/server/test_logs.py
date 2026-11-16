"""
Tests for Pytre_Server/logs.py
"""

import json
import logging
import re
from logging.handlers import RotatingFileHandler

import pytest

from Pytre_Core.utils import LOGGER_ROOT_NAME, log as pytre_log
from Pytre_Server.audit import AUDIT_LOGGER_NAME, AuditJsonFormatter, audit
from Pytre_Server.logs import (
    AUDIT_BACKUP_COUNT,
    AUDIT_MAX_SIZE_MB,
    CONTINUE_INDENT,
    LOG_BACKUP_COUNT,
    LOG_MAX_SIZE_MB,
    IndentedFormatter,
    setup_audit_file_logging,
    setup_file_logging,
    setup_terminal_logging,
)


pytestmark = pytest.mark.usefixtures("pristine_pytre_logger", "pristine_audit_logger")  # see tests/conftest.py

MB = 1024**2
BIG_MESSAGE = "x" * 200_000  # a few of them fill a 1 MB file


def flush_handlers() -> None:
    for name in (LOGGER_ROOT_NAME, AUDIT_LOGGER_NAME):
        for handler in logging.getLogger(name).handlers:
            handler.flush()


def date_lines(text: str) -> list[str]:
    """Lines starting with a date, as the first line of every record does, in any of the dates written so far"""
    return [line for line in text.splitlines() if re.match(r"\d{4}-\d{2}-\d{2}[T ]", line)]


@pytest.mark.unit
def test_setup_file_logging_limits_logs_to_two_files_of_bounded_size(tmp_path):
    log_file = tmp_path / "server.log"
    setup_file_logging(log_file, max_mb=1)

    for _ in range(20):
        pytre_log(BIG_MESSAGE, level=logging.INFO, sublogger="test_logs")

    assert sorted(p.name for p in tmp_path.iterdir()) == ["server.log", "server.log.1"]
    assert log_file.stat().st_size <= MB
    assert log_file.with_name("server.log.1").stat().st_size <= MB


@pytest.mark.unit
def test_setup_file_logging_keeps_the_given_number_of_rotated_files(tmp_path):
    log_file = tmp_path / "server.log"
    setup_file_logging(log_file, max_mb=1, backup_count=3)

    for _ in range(40):
        pytre_log(BIG_MESSAGE, level=logging.INFO, sublogger="test_logs")

    assert sorted(p.name for p in tmp_path.iterdir()) == ["server.log", "server.log.1", "server.log.2", "server.log.3"]


@pytest.mark.unit
def test_setup_file_logging_writes_iso_dates_with_milliseconds_and_their_utc_offset(tmp_path):
    log_file = tmp_path / "server.log"
    setup_file_logging(log_file)

    pytre_log("hello", level=logging.INFO, sublogger="test_logs")

    content = log_file.read_text(encoding="utf-8")
    assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}[+-]\d{2}:\d{2} - INFO - ", content)


@pytest.mark.unit
def test_terminal_logging_writes_iso_dates_with_milliseconds_and_their_utc_offset(capsys):
    setup_terminal_logging()

    pytre_log("hello", level=logging.INFO, sublogger="test_logs")

    err = capsys.readouterr().err
    assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}[+-]\d{2}:\d{2} - INFO - ", err)


@pytest.mark.unit
def test_log_file_and_audit_file_write_the_same_date_for_an_event(tmp_path):
    """A line of one file can be matched with the other's, to the millisecond."""
    log_file, audit_file = tmp_path / "server.log", tmp_path / "audit.log"
    setup_file_logging(log_file)
    setup_audit_file_logging(audit_file)

    audit("client.login", "10.0.0.5", "User 'alice' logged in")
    flush_handlers()

    audit_time = json.loads(audit_file.read_text(encoding="utf-8"))["time"]
    assert log_file.read_text(encoding="utf-8").startswith(f"{audit_time} - INFO - Pytre.audit - ")


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
    assert handler.maxBytes == LOG_MAX_SIZE_MB * MB
    assert handler.backupCount == LOG_BACKUP_COUNT
    assert isinstance(handler.formatter, IndentedFormatter)


@pytest.mark.unit
def test_setup_file_logging_takes_a_custom_size_and_number_of_rotated_files(tmp_path):
    setup_file_logging(tmp_path / "server.log", max_mb=7, backup_count=4)

    (handler,) = logging.getLogger(LOGGER_ROOT_NAME).handlers
    assert isinstance(handler, RotatingFileHandler)
    assert (handler.maxBytes, handler.backupCount) == (7 * MB, 4)


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
    assert isinstance(new_handlers[0].formatter, IndentedFormatter)


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


# ================================================================================
# IndentedFormatter: a line break in a logged value can't forge a record
# ================================================================================


@pytest.mark.unit
def test_file_logging_indents_every_line_of_a_record_after_its_first(tmp_path):
    log_file = tmp_path / "server.log"
    setup_file_logging(log_file)

    pytre_log("first\nsecond\nthird", level=logging.INFO, sublogger="test_logs")
    flush_handlers()

    first, *continuation = log_file.read_text(encoding="utf-8").splitlines()
    assert first.endswith(" - INFO - Pytre.test_logs - first")
    assert continuation == [f"{CONTINUE_INDENT}second", f"{CONTINUE_INDENT}third"]


@pytest.mark.unit
@pytest.mark.parametrize("line_break", ["\n", "\r", "\r\n", " ", "\x85"])
def test_file_logging_leaves_no_forged_record_whatever_the_line_break(tmp_path, line_break):
    """Python reads a file line by line on \\r too, and other tools also split on Unicode line separators."""
    log_file = tmp_path / "server.log"
    setup_file_logging(log_file)
    forged = "2026-11-16T10:00:00.000+01:00 - WARNING - Pytre - Admin 'mallory' logged in"

    pytre_log(f"Failed login for 'alice{line_break}{forged}'", level=logging.INFO, sublogger="test_logs")
    flush_handlers()

    with open(log_file, encoding="utf-8") as file:  # universal newlines, as the web administration reads it back
        lines = file.read().splitlines()
    assert len(date_lines("\n".join(lines))) == 1
    assert lines[1] == f"{CONTINUE_INDENT}{forged}'"


@pytest.mark.unit
def test_file_logging_indents_the_lines_of_a_traceback(tmp_path):
    log_file = tmp_path / "server.log"
    setup_file_logging(log_file)

    try:
        raise ValueError("boom\n2026-11-16T10:00:00.000+01:00 - INFO - Pytre - forged")
    except ValueError:
        logging.getLogger("Pytre.test_logs").exception("it failed")
    flush_handlers()

    first, *continuation = log_file.read_text(encoding="utf-8").splitlines()
    assert first.endswith(" - ERROR - Pytre.test_logs - it failed")
    assert continuation[0] == f"{CONTINUE_INDENT}Traceback (most recent call last):"
    assert all(line.startswith(CONTINUE_INDENT) for line in continuation)


@pytest.mark.unit
def test_terminal_logging_indents_every_line_of_a_record_after_its_first(capsys):
    setup_terminal_logging()

    pytre_log("first\n2026-11-16T10:00:00.000+01:00 - INFO - Pytre - forged", level=logging.INFO, sublogger="test_logs")

    lines = capsys.readouterr().err.splitlines()
    assert len(lines) == 2
    assert lines[1] == f"{CONTINUE_INDENT}2026-11-16T10:00:00.000+01:00 - INFO - Pytre - forged"


@pytest.mark.unit
def test_indented_formatter_leaves_a_single_line_record_as_it_is():
    formatter = IndentedFormatter("%(levelname)s - %(message)s")
    record = logging.LogRecord("Pytre.test", logging.INFO, __file__, 1, "plain message", None, None)

    assert formatter.format(record) == "INFO - plain message"


# ================================================================================
# setup_audit_file_logging
# ================================================================================


@pytest.mark.unit
def test_setup_audit_file_logging_attaches_a_rotating_json_handler_to_the_audit_logger(tmp_path):
    setup_audit_file_logging(tmp_path / "audit.log")

    (handler,) = logging.getLogger(AUDIT_LOGGER_NAME).handlers
    assert isinstance(handler, RotatingFileHandler)
    assert isinstance(handler.formatter, AuditJsonFormatter)
    assert (handler.maxBytes, handler.backupCount) == (AUDIT_MAX_SIZE_MB * MB, AUDIT_BACKUP_COUNT)
    assert handler.level == logging.INFO
    assert logging.getLogger(LOGGER_ROOT_NAME).handlers == []  # nothing added to the main logger


@pytest.mark.unit
def test_setup_audit_file_logging_takes_a_custom_size_and_number_of_rotated_files(tmp_path):
    setup_audit_file_logging(tmp_path / "audit.log", max_mb=3, backup_count=50)

    (handler,) = logging.getLogger(AUDIT_LOGGER_NAME).handlers
    assert isinstance(handler, RotatingFileHandler)
    assert (handler.maxBytes, handler.backupCount) == (3 * MB, 50)


@pytest.mark.unit
@pytest.mark.parametrize(("max_mb", "backup_count"), [(0, 20), (10, 0), (-1, 20), (10, -1)])
def test_setup_audit_file_logging_refuses_a_limit_that_would_let_the_file_grow_forever(tmp_path, max_mb, backup_count):
    with pytest.raises(ValueError, match="must be at least 1"):
        setup_audit_file_logging(tmp_path / "audit.log", max_mb=max_mb, backup_count=backup_count)

    assert logging.getLogger(AUDIT_LOGGER_NAME).handlers == []


@pytest.mark.unit
def test_setup_audit_file_logging_creates_parent_directory(tmp_path):
    audit_file = tmp_path / "nested" / "audit.log"

    setup_audit_file_logging(audit_file)

    assert audit_file.parent.is_dir()


@pytest.mark.unit
def test_setup_audit_file_logging_lets_audit_events_through_at_info_level(tmp_path):
    """Even when the "Pytre" logger only lets warnings through, the audit file gets every event."""
    logging.getLogger(LOGGER_ROOT_NAME).setLevel(logging.WARNING)
    audit_file = tmp_path / "audit.log"
    setup_audit_file_logging(audit_file)

    audit("client.login", "10.0.0.5", "User 'alice' logged in")
    flush_handlers()

    assert logging.getLogger(AUDIT_LOGGER_NAME).level == logging.INFO
    assert "User 'alice' logged in" in audit_file.read_text(encoding="utf-8")


@pytest.mark.unit
def test_audit_file_gets_only_the_audit_events_one_json_object_per_line(tmp_path):
    audit_file = tmp_path / "audit.log"
    setup_audit_file_logging(audit_file)

    pytre_log("not an audit event", level=logging.WARNING, sublogger="test_logs")
    audit("client.login", "10.0.0.5", "User 'alice' logged in", target="alice")
    audit("admin.login", "10.0.0.6", "Failed admin login for 'bob\nforged'", target="bob\nforged")
    flush_handlers()

    lines = audit_file.read_text(encoding="utf-8").splitlines()
    entries = [json.loads(line) for line in lines]
    assert [(e["event"], e["ip"], e["target"]) for e in entries] == [
        ("client.login", "10.0.0.5", "alice"),
        ("admin.login", "10.0.0.6", "bob\nforged"),
    ]


@pytest.mark.unit
def test_audit_events_also_reach_the_main_log_file_as_text(tmp_path):
    log_file = tmp_path / "server.log"
    setup_file_logging(log_file)
    setup_audit_file_logging(tmp_path / "audit.log")

    audit("client.login", "10.0.0.5", "User 'alice' logged in")
    flush_handlers()

    assert " - INFO - Pytre.audit - User 'alice' logged in from 10.0.0.5" in log_file.read_text(encoding="utf-8")


@pytest.mark.unit
def test_audit_file_rotates_and_keeps_the_given_number_of_files(tmp_path):
    audit_file = tmp_path / "audit.log"
    setup_audit_file_logging(audit_file, max_mb=1, backup_count=2)

    for _ in range(30):
        audit("client.login", "10.0.0.5", BIG_MESSAGE)
    flush_handlers()

    assert sorted(p.name for p in tmp_path.iterdir()) == ["audit.log", "audit.log.1", "audit.log.2"]
    assert all(p.stat().st_size <= MB for p in tmp_path.iterdir())
