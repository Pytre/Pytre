"""
Tests for Pytre_Server/routes/admin_logs.py's _stream_events() - the SSE tail generator.

Tested directly rather than through a real HTTP round trip: Starlette's TestClient can't drive a
StreamingResponse whose generator runs forever (it waits for the response to finish before
returning control to the test) - see tests/integration/routes/test_admin_logs.py for the
HTTP-level tests (page rendering, auth gating, /before pagination) that don't have this problem.
"""

import json
import logging

import pytest

from Pytre_Server.log_buffer import LogBufferHandler
from Pytre_Server.routes import admin_logs


@pytest.fixture
def anyio_backend():
    return "asyncio"


class _FakeDisconnectRequest:
    """Simulates FastAPI's Request.is_disconnected() - stays connected by default,
    or disconnects after a given number of calls."""

    def __init__(self, disconnect_after: int | None = None):
        self.calls = 0
        self._disconnect_after = disconnect_after

    async def is_disconnected(self) -> bool:
        self.calls += 1
        if self._disconnect_after is None:
            return False
        return self.calls > self._disconnect_after


def _make_record(msg: str) -> logging.LogRecord:
    return logging.LogRecord(
        name="Pytre.test", level=logging.INFO, pathname=__file__, lineno=1, msg=msg, args=None, exc_info=None
    )


async def _collect(gen) -> list[dict]:
    chunks = [chunk async for chunk in gen]
    for chunk in chunks:
        assert chunk.startswith("data: ") and chunk.endswith("\n\n")
    return [json.loads(chunk[len("data: ") : -2]) for chunk in chunks]


@pytest.mark.unit
@pytest.mark.anyio
async def test_stream_events_yields_entries_strictly_after_the_given_seq(monkeypatch):
    monkeypatch.setattr(admin_logs, "POLL_INTERVAL_SECS", 0.01)
    log_buffer = LogBufferHandler(max_bytes=10_000)
    log_buffer.emit(_make_record("before"))
    after = log_buffer.entries_since(0)[0].seq
    log_buffer.emit(_make_record("after-1"))
    log_buffer.emit(_make_record("after-2"))

    events = await _collect(admin_logs._stream_events(log_buffer, _FakeDisconnectRequest(disconnect_after=1), after))

    assert [e["message"] for e in events] == ["after-1", "after-2"]


@pytest.mark.unit
@pytest.mark.anyio
async def test_stream_events_stops_once_client_disconnects():
    """A single poll cycle happens (disconnect_after=1) before the stop is noticed - must not
    keep polling forever past that point."""
    log_buffer = LogBufferHandler(max_bytes=10_000)
    request = _FakeDisconnectRequest(disconnect_after=1)

    events = await _collect(admin_logs._stream_events(log_buffer, request, after=0))

    assert events == []
    assert request.calls == 2  # one "not yet" check, one "now disconnected" check


@pytest.mark.unit
@pytest.mark.anyio
async def test_stream_events_stops_immediately_if_already_disconnected():
    log_buffer = LogBufferHandler(max_bytes=10_000)
    log_buffer.emit(_make_record("never sent"))

    events = await _collect(admin_logs._stream_events(log_buffer, _FakeDisconnectRequest(disconnect_after=0), after=0))

    assert events == []


@pytest.mark.unit
@pytest.mark.anyio
async def test_stream_events_yields_nothing_when_log_buffer_is_none():
    events = await _collect(admin_logs._stream_events(None, _FakeDisconnectRequest(disconnect_after=0), after=0))

    assert events == []


@pytest.mark.unit
@pytest.mark.anyio
async def test_stream_events_advances_cursor_so_each_entry_is_sent_only_once(monkeypatch):
    """Entries already seen on an earlier poll must not be resent on a later one."""
    monkeypatch.setattr(admin_logs, "POLL_INTERVAL_SECS", 0.01)
    log_buffer = LogBufferHandler(max_bytes=10_000)
    log_buffer.emit(_make_record("one"))

    events = await _collect(admin_logs._stream_events(log_buffer, _FakeDisconnectRequest(disconnect_after=2), after=0))

    assert [e["message"] for e in events] == ["one"]
