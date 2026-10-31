"""
Tests for Pytre_Server/log_buffer.py
"""

import logging
from datetime import datetime, timezone

import pytest

from Pytre_Core.utils import LOGGER_ROOT_NAME
from Pytre_Server.log_buffer import SEPARATOR_MESSAGE, LogBufferHandler, LogEntry, setup_log_buffer
from Pytre_Server.logs import setup_file_logging


pytestmark = pytest.mark.usefixtures("pristine_pytre_logger")  # see tests/conftest.py


def _make_record(msg: str = "hello", level: int = logging.INFO, logger_name: str = "Pytre.test") -> logging.LogRecord:
    return logging.LogRecord(
        name=logger_name, level=level, pathname=__file__, lineno=1, msg=msg, args=None, exc_info=None
    )


def _line(msg: str, level: str = "INFO") -> str:
    return f"2026-10-31 10:13:20+0100 - {level} - Pytre.test - {msg}\n"


# ================================================================================
# LogBufferHandler.emit / entries_*
# ================================================================================


@pytest.mark.unit
def test_emit_appends_entry_with_expected_fields():
    handler = LogBufferHandler(max_bytes=10_000)

    handler.emit(_make_record(msg="hello world", level=logging.WARNING, logger_name="Pytre.Pytre_Server.foo"))

    entries = handler.entries_since(0)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.seq == 1
    assert entry.level == logging.WARNING
    assert entry.level_name == "WARNING"
    assert entry.logger_name == "Pytre.Pytre_Server.foo"
    assert entry.message == "hello world"


@pytest.mark.unit
def test_emit_assigns_strictly_increasing_seq():
    handler = LogBufferHandler(max_bytes=10_000)

    for i in range(5):
        handler.emit(_make_record(msg=f"msg{i}"))

    seqs = [e.seq for e in handler.entries_since(0)]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)


@pytest.mark.unit
def test_eviction_drops_oldest_entries_once_max_bytes_exceeded():
    # each message is 10 bytes; budget for 3 entries, emit 5
    handler = LogBufferHandler(max_bytes=30)

    for _ in range(5):
        handler.emit(_make_record(msg="x" * 10))

    entries = handler.entries_since(0)
    assert [e.seq for e in entries] == [3, 4, 5]


@pytest.mark.unit
def test_eviction_can_drop_several_small_entries_for_one_large_message():
    """A single oversized message must be able to evict multiple older small ones in one shot -
    not just "pop one entry per emit"."""
    handler = LogBufferHandler(max_bytes=50)

    for _ in range(5):
        handler.emit(_make_record(msg="x" * 10))  # 5 * 10 = 50 bytes, exactly at budget

    handler.emit(_make_record(msg="y" * 40))  # pushes total to 90, well over budget

    entries = handler.entries_since(0)
    assert sum(len(e.message) for e in entries) <= 50
    assert entries[-1].message == "y" * 40
    assert len(entries) < 6  # several of the earlier "x"*10 entries got evicted, not just one


@pytest.mark.unit
def test_entries_latest_returns_newest_first():
    handler = LogBufferHandler(max_bytes=10_000)
    for i in range(5):
        handler.emit(_make_record(msg=f"msg{i}"))

    latest = handler.entries_latest(3)

    assert [e.message for e in latest] == ["msg4", "msg3", "msg2"]


@pytest.mark.unit
def test_entries_latest_caps_at_requested_limit_even_with_more_available():
    handler = LogBufferHandler(max_bytes=10_000)
    for i in range(10):
        handler.emit(_make_record(msg=f"msg{i}"))

    assert len(handler.entries_latest(3)) == 3


@pytest.mark.unit
def test_entries_since_returns_only_strictly_newer_entries_oldest_first():
    handler = LogBufferHandler(max_bytes=10_000)
    for i in range(5):
        handler.emit(_make_record(msg=f"msg{i}"))
    first_seq = handler.entries_since(0)[0].seq

    newer = handler.entries_since(first_seq + 1)

    assert [e.message for e in newer] == ["msg2", "msg3", "msg4"]


@pytest.mark.unit
def test_entries_since_zero_returns_everything_buffered():
    handler = LogBufferHandler(max_bytes=10_000)
    for i in range(3):
        handler.emit(_make_record(msg=f"msg{i}"))

    assert len(handler.entries_since(0)) == 3


@pytest.mark.unit
def test_entries_before_returns_newest_first_among_older_entries():
    handler = LogBufferHandler(max_bytes=10_000)
    for i in range(5):
        handler.emit(_make_record(msg=f"msg{i}"))
    seqs = [e.seq for e in handler.entries_since(0)]

    before = handler.entries_before(seqs[-1], limit=2)

    assert [e.message for e in before] == ["msg3", "msg2"]


@pytest.mark.unit
def test_entries_before_is_empty_once_history_exhausted():
    handler = LogBufferHandler(max_bytes=10_000)
    handler.emit(_make_record(msg="only one"))
    first_seq = handler.entries_since(0)[0].seq

    assert handler.entries_before(first_seq, limit=10) == []


# ================================================================================
# LogEntry.parse_line
# ================================================================================


@pytest.mark.unit
def test_parse_line_reads_every_field_and_respects_the_utc_offset():
    entry = LogEntry.parse_line("2026-10-31 10:13:20+0100 - WARNING - Pytre.foo - hello", seq=7)

    assert entry is not None
    assert entry.seq == 7
    assert entry.timestamp == datetime(2026, 10, 31, 9, 13, 20, tzinfo=timezone.utc).timestamp()
    assert entry.level == logging.WARNING
    assert entry.level_name == "WARNING"
    assert entry.logger_name == "Pytre.foo"
    assert entry.message == "hello"
    assert entry.is_separator is False


@pytest.mark.unit
def test_parse_line_reads_a_date_without_offset_as_local_time():
    """Lines written before the UTC offset was added to the log format."""
    entry = LogEntry.parse_line("2026-10-31 10:13:20,739 - INFO - Pytre.foo - hello", seq=1)

    assert entry is not None
    assert entry.timestamp == datetime(2026, 10, 31, 10, 13, 20, 739000).timestamp()


@pytest.mark.unit
def test_parse_line_keeps_separators_inside_the_message():
    entry = LogEntry.parse_line("2026-10-31 10:13:20+0100 - INFO - Pytre.foo - a - b - c", seq=1)

    assert entry is not None
    assert entry.message == "a - b - c"


@pytest.mark.unit
@pytest.mark.parametrize(
    "line",
    [
        "",
        "Traceback (most recent call last):",
        "  some - traceback - line - here",
        "2026-10-31 10:13:20+0100 - NOTALEVEL - Pytre.foo - hello",
        "not-a-date - INFO - Pytre.foo - hello",
    ],
)
def test_parse_line_returns_none_for_a_line_that_does_not_start_an_entry(line):
    assert LogEntry.parse_line(line, seq=1) is None


# ================================================================================
# LogBufferHandler.load_from_file
# ================================================================================


@pytest.mark.unit
def test_load_from_file_reads_rotated_file_first_then_adds_a_separator(tmp_path):
    log_file = tmp_path / "server.log"
    log_file.with_name("server.log.1").write_text(_line("oldest") + _line("older"), encoding="utf-8")
    log_file.write_text(_line("newest"), encoding="utf-8")
    handler = LogBufferHandler(max_bytes=10_000)

    handler.load_from_file(log_file)

    entries = handler.entries_since(0)
    assert [e.message for e in entries] == ["oldest", "older", "newest", SEPARATOR_MESSAGE]
    assert [e.is_separator for e in entries] == [False, False, False, True]
    seqs = [e.seq for e in entries]
    assert seqs == sorted(set(seqs))


@pytest.mark.unit
def test_load_from_file_works_with_only_the_active_file(tmp_path):
    log_file = tmp_path / "server.log"
    log_file.write_text(_line("only"), encoding="utf-8")
    handler = LogBufferHandler(max_bytes=10_000)

    handler.load_from_file(log_file)

    assert [e.message for e in handler.entries_since(0)] == ["only", SEPARATOR_MESSAGE]


@pytest.mark.unit
def test_load_from_file_attaches_continuation_lines_to_their_entry(tmp_path):
    log_file = tmp_path / "server.log"
    content = _line("failed", level="ERROR") + "Traceback (most recent call last):\n  File x\n" + _line("next")
    log_file.write_text(content, encoding="utf-8")
    handler = LogBufferHandler(max_bytes=10_000)

    handler.load_from_file(log_file)

    messages = [e.message for e in handler.entries_since(0)]
    assert messages == ["failed\nTraceback (most recent call last):\n  File x", "next", SEPARATOR_MESSAGE]


@pytest.mark.unit
def test_load_from_file_drops_lines_before_the_first_entry(tmp_path):
    log_file = tmp_path / "server.log"
    log_file.write_text("  orphan line\n" + _line("first"), encoding="utf-8")
    handler = LogBufferHandler(max_bytes=10_000)

    handler.load_from_file(log_file)

    assert [e.message for e in handler.entries_since(0)] == ["first", SEPARATOR_MESSAGE]


@pytest.mark.unit
def test_load_from_file_adds_no_separator_when_files_are_missing(tmp_path):
    handler = LogBufferHandler(max_bytes=10_000)

    handler.load_from_file(tmp_path / "server.log")

    assert handler.entries_since(0) == []


@pytest.mark.unit
def test_load_from_file_adds_no_separator_when_nothing_was_loaded(tmp_path):
    log_file = tmp_path / "server.log"
    log_file.write_text("", encoding="utf-8")
    handler = LogBufferHandler(max_bytes=10_000)

    handler.load_from_file(log_file)

    assert handler.entries_since(0) == []


@pytest.mark.unit
def test_load_from_file_respects_the_byte_budget_and_keeps_the_most_recent_lines(tmp_path):
    log_file = tmp_path / "server.log"
    log_file.write_text("".join(_line(f"{i:020d}") for i in range(10)), encoding="utf-8")
    max_bytes = len(SEPARATOR_MESSAGE) + 40
    handler = LogBufferHandler(max_bytes=max_bytes)

    handler.load_from_file(log_file)

    messages = [e.message for e in handler.entries_since(0)]
    assert messages == [f"{8:020d}", f"{9:020d}", SEPARATOR_MESSAGE]


@pytest.mark.unit
def test_load_from_file_replaces_undecodable_bytes(tmp_path):
    log_file = tmp_path / "server.log"
    log_file.write_bytes(_line("before \xff after").encode("latin-1"))
    handler = LogBufferHandler(max_bytes=10_000)

    handler.load_from_file(log_file)

    assert handler.entries_since(0)[0].message == "before � after"


@pytest.mark.unit
def test_load_from_file_reads_back_what_the_file_handler_wrote(tmp_path):
    """Guards the parser against any drift from the format actually written to the file."""
    log_file = tmp_path / "server.log"
    setup_file_logging(log_file)
    logger = logging.getLogger("Pytre.test_round_trip")
    logger.warning("plain - message")
    try:
        raise ValueError("boom")
    except ValueError:
        logger.exception("it failed")
    handler = LogBufferHandler(max_bytes=10_000)

    handler.load_from_file(log_file)

    plain, failed, separator = handler.entries_since(0)
    assert (plain.level_name, plain.logger_name, plain.message) == (
        "WARNING",
        "Pytre.test_round_trip",
        "plain - message",
    )
    assert failed.level_name == "ERROR"
    assert failed.message.startswith("it failed\nTraceback (most recent call last):")
    assert failed.message.endswith("ValueError: boom")
    assert separator.is_separator is True


# ================================================================================
# setup_log_buffer
# ================================================================================


@pytest.mark.unit
def test_setup_log_buffer_attaches_a_handler_to_the_pytre_logger():
    logger = logging.getLogger(LOGGER_ROOT_NAME)
    before = set(logger.handlers)

    handler = setup_log_buffer(max_mb=1)

    new_handlers = [h for h in logger.handlers if h not in before]
    assert new_handlers == [handler]
    assert isinstance(handler, LogBufferHandler)


@pytest.mark.unit
def test_setup_log_buffer_is_idempotent():
    """Must not attach a second handler on a later call - create_app() calls this unconditionally
    every time it runs, including once per test across the whole suite."""
    first = setup_log_buffer(max_mb=1)
    logger = logging.getLogger(LOGGER_ROOT_NAME)
    before = set(logger.handlers)

    second = setup_log_buffer(max_mb=2)

    assert second is first
    assert set(logger.handlers) == before


@pytest.mark.unit
def test_setup_log_buffer_lowers_logger_level_to_debug_if_higher():
    logger = logging.getLogger(LOGGER_ROOT_NAME)
    logger.setLevel(logging.WARNING)

    setup_log_buffer(max_mb=1)

    assert logger.level == logging.DEBUG


@pytest.mark.unit
def test_setup_log_buffer_captures_records_logged_through_the_pytre_logger():
    handler = setup_log_buffer(max_mb=1)

    logging.getLogger("Pytre.test_log_buffer_capture").warning("captured me")

    messages = [e.message for e in handler.entries_since(0)]
    assert "captured me" in messages


@pytest.mark.unit
def test_setup_log_buffer_without_log_file_starts_empty():
    assert setup_log_buffer(max_mb=1).entries_since(0) == []


@pytest.mark.unit
def test_setup_log_buffer_prefills_a_new_buffer_from_log_file(tmp_path):
    log_file = tmp_path / "server.log"
    log_file.write_text(_line("stored"), encoding="utf-8")

    handler = setup_log_buffer(max_mb=1, log_file=str(log_file))

    assert [e.message for e in handler.entries_since(0)] == ["stored", SEPARATOR_MESSAGE]


@pytest.mark.unit
def test_setup_log_buffer_does_not_load_again_on_a_later_call(tmp_path):
    log_file = tmp_path / "server.log"
    log_file.write_text(_line("stored"), encoding="utf-8")
    handler = setup_log_buffer(max_mb=1, log_file=log_file)

    setup_log_buffer(max_mb=1, log_file=log_file)

    assert [e.message for e in handler.entries_since(0)] == ["stored", SEPARATOR_MESSAGE]


@pytest.mark.unit
def test_setup_log_buffer_survives_an_unreadable_file_and_shows_the_warning_above_the_separator(tmp_path):
    log_file = tmp_path / "server.log"
    log_file.mkdir()  # reading a directory raises an OSError
    log_file.with_name("server.log.1").write_text(_line("stored"), encoding="utf-8")

    handler = setup_log_buffer(max_mb=1, log_file=log_file)

    stored, separator, warning = handler.entries_since(0)
    assert stored.message == "stored"
    assert separator.is_separator is True
    assert warning.level == logging.WARNING
    assert "Could not load stored logs" in warning.message
