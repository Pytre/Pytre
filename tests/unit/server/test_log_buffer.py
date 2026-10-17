"""
Tests for Pytre_Server/log_buffer.py
"""

import logging

import pytest

from Pytre_Core.utils import LOGGER_ROOT_NAME
from Pytre_Server.log_buffer import LogBufferHandler, setup_log_buffer


@pytest.fixture(autouse=True)
def _restore_logger_state():
    """setup_log_buffer() mutates the shared "Pytre" logger (adds a handler, adjusts its level) -
    save/restore around every test so handlers/levels from one test never leak into another, and
    close any handler added during the test (same pattern as test_logs.py's own fixture)."""
    logger = logging.getLogger(LOGGER_ROOT_NAME)
    original_handlers = list(logger.handlers)
    original_level = logger.level

    yield

    for handler in list(logger.handlers):
        if handler not in original_handlers:
            logger.removeHandler(handler)
            handler.close()
    logger.setLevel(original_level)


def _make_record(msg: str = "hello", level: int = logging.INFO, logger_name: str = "Pytre.test") -> logging.LogRecord:
    return logging.LogRecord(
        name=logger_name, level=level, pathname=__file__, lineno=1, msg=msg, args=None, exc_info=None
    )


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
