"""
Tests for Pytre_Server/result_streaming.py
"""

import asyncio
import json
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import Mock

import pytest

from Pytre_Core.logs_central import ExecutionLogEntry
from Pytre_Server.result_streaming import ClientDisconnected, pings, stream_result_ndjson


@pytest.fixture
def anyio_backend():
    return "asyncio"


class _FakeResult:
    """Minimal stand-in for AsyncSqlResult - fetch(n) returns the next n rows, optionally after a delay,
    and raises `fetch_error` once `fail_after` rows have been returned."""

    def __init__(
        self,
        rows: list[tuple],
        columns: list[str],
        fetch_delay: float = 0,
        fetch_error: Exception | None = None,
        fail_after: int = 0,
    ):
        self._rows = rows
        self._columns = columns
        self._fetch_delay = fetch_delay
        self._fetch_error = fetch_error
        self._fail_after = fail_after
        self._position = 0
        self.fetch_sizes: list[int] = []
        self.fetch_cancelled = False
        self.closed = False

    def columns(self) -> list[str]:
        return self._columns

    async def fetch(self, n: int) -> list[tuple]:
        self.fetch_sizes.append(n)
        try:
            await asyncio.sleep(self._fetch_delay)
        except asyncio.CancelledError:
            self.fetch_cancelled = True
            raise
        if self._fetch_error is not None and self._position >= self._fail_after:
            raise self._fetch_error
        batch = self._rows[self._position : self._position + n]
        self._position += len(batch)
        return batch

    async def close(self) -> None:
        self.closed = True


class _FakeRequest:
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


def _make_log_entry(
    *,
    server_id: str = "SRV1",
    user_id: str = "u1",
    user_name: str = "User One",
    query_id: str = "Q1",
    start: datetime = datetime(2026, 1, 1, 10, 0, 0),
    duration_secs: int = 0,
    nb_rows: int = 0,
    parameters: dict[str, str] | None = None,
) -> ExecutionLogEntry:
    """Builds an ExecutionLogEntry with sensible defaults, overridable per test"""
    return ExecutionLogEntry(
        server_id=server_id,
        user_id=user_id,
        user_name=user_name,
        query_id=query_id,
        start=start,
        duration_secs=duration_secs,
        nb_rows=nb_rows,
        parameters=parameters or {},
    )


def _stream(
    result: _FakeResult,
    request: _FakeRequest | None = None,
    chunk_size: int = 10,
    heartbeat_secs: float = 30,
    execution_logs: Mock | None = None,
    log_entry: ExecutionLogEntry | None = None,
):
    return stream_result_ndjson(
        result,
        request or _FakeRequest(),
        chunk_size=chunk_size,
        heartbeat_secs=heartbeat_secs,  # type: ignore
        execution_logs=execution_logs,
        log_entry=log_entry or _make_log_entry(),
    )


async def _collect(chunks) -> list[str]:
    return [chunk async for chunk in chunks]


# ------------------------------------------------------------------------------
# Rows streaming
# ------------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.anyio
async def test_stream_ndjson_always_emits_columns_line_first():
    chunks = await _collect(_stream(_FakeResult([], columns=["id"]), chunk_size=3))

    assert len(chunks) == 1
    assert json.loads(chunks[0]) == {"__columns__": ["id"]}


@pytest.mark.unit
@pytest.mark.anyio
async def test_stream_ndjson_emits_empty_columns():
    chunks = await _collect(_stream(_FakeResult([], columns=[])))

    assert [json.loads(chunk) for chunk in chunks] == [{"__columns__": []}]


@pytest.mark.unit
@pytest.mark.anyio
async def test_stream_ndjson_single_chunk_when_under_chunk_size():
    result = _FakeResult([(str(i),) for i in range(2)], columns=["id"])

    chunks = await _collect(_stream(result, chunk_size=3))

    assert json.loads(chunks[0]) == {"__columns__": ["id"]}
    assert len(chunks) == 2
    lines = chunks[1].splitlines()
    assert [json.loads(line) for line in lines] == [{"__row__": ["0"]}, {"__row__": ["1"]}]


@pytest.mark.unit
@pytest.mark.anyio
async def test_stream_ndjson_splits_into_multiple_chunks():
    result = _FakeResult([(str(i),) for i in range(7)], columns=["id"])

    chunks = await _collect(_stream(result, chunk_size=3))

    assert json.loads(chunks[0]) == {"__columns__": ["id"]}
    # 7 rows, chunk_size=3 -> chunks of 3, 3, 1 (after the columns chunk)
    assert [len(chunk.splitlines()) for chunk in chunks[1:]] == [3, 3, 1]

    all_rows = [json.loads(line) for chunk in chunks[1:] for line in chunk.splitlines()]
    assert all_rows == [{"__row__": [str(i)]} for i in range(7)]


@pytest.mark.unit
@pytest.mark.anyio
async def test_stream_ndjson_fetches_chunk_size_rows_until_an_empty_batch():
    result = _FakeResult([(str(i),) for i in range(6)], columns=["id"])

    await _collect(_stream(result, chunk_size=3))

    assert result.fetch_sizes == [3, 3, 3]  # the 3rd fetch returns no row and ends the stream


@pytest.mark.unit
@pytest.mark.anyio
async def test_stream_ndjson_each_chunk_ends_with_newline():
    result = _FakeResult([(str(i),) for i in range(3)], columns=["id"])

    chunks = await _collect(_stream(result, chunk_size=3))

    assert all(chunk.endswith("\n") for chunk in chunks)


@pytest.mark.unit
@pytest.mark.anyio
async def test_stream_ndjson_preserves_row_content():
    result = _FakeResult([("a", "1"), ("b", "2")], columns=["title", "x3_id"])

    chunks = await _collect(_stream(result))

    lines = chunks[1].splitlines()
    assert json.loads(lines[0]) == {"__row__": ["a", "1"]}
    assert json.loads(lines[1]) == {"__row__": ["b", "2"]}


@pytest.mark.unit
@pytest.mark.anyio
async def test_stream_ndjson_keeps_columns_sharing_the_same_name():
    """Rows are tuples in columns() order - two columns with the same name both keep their own value."""
    result = _FakeResult([("a", "b")], columns=["name", "name"])

    chunks = await _collect(_stream(result))

    assert json.loads(chunks[0]) == {"__columns__": ["name", "name"]}
    assert json.loads(chunks[1]) == {"__row__": ["a", "b"]}


@pytest.mark.unit
@pytest.mark.anyio
async def test_stream_ndjson_wraps_decimal_and_datetime_values():
    """Values travel typed, not pre-formatted: decode_row_value() (Pytre_Core/remotes.py)
    reconstructs real Decimal/datetime objects client-side, so the client's own SqlTypeConverter
    settings apply - datetime/Decimal need wrapping since JSON has no native date type and a
    plain JSON number would lose Decimal's exact precision."""
    result = _FakeResult([(Decimal("12.50"), datetime(2026, 1, 1, 10, 30))], columns=["amount", "created"])

    chunks = await _collect(_stream(result))

    decoded = json.loads(chunks[1].splitlines()[0])
    assert decoded == {"__row__": [{"$decimal": "12.50"}, {"$datetime": "2026-01-01T10:30:00"}]}


@pytest.mark.unit
@pytest.mark.anyio
async def test_stream_ndjson_passes_through_json_native_types_unwrapped():
    """str/int/float/bool/None survive JSON natively - no wrapping needed."""
    result = _FakeResult([("a", 3, 1.5, True, None)], columns=["name", "count", "ratio", "active", "note"])

    chunks = await _collect(_stream(result))

    decoded = json.loads(chunks[1].splitlines()[0])
    assert decoded == {"__row__": ["a", 3, 1.5, True, None]}


@pytest.mark.unit
@pytest.mark.anyio
async def test_stream_ndjson_wraps_plain_date_values():
    """A plain date (not datetime) needs its own wrapping - datetime is a subclass of date,
    so encode_row_value() must check datetime first or every datetime would also match
    isinstance(value, date) and be wrapped as "$date" instead of "$datetime"."""
    result = _FakeResult([(date(2026, 1, 1),)], columns=["day"])

    chunks = await _collect(_stream(result))

    decoded = json.loads(chunks[1].splitlines()[0])
    assert decoded == {"__row__": [{"$date": "2026-01-01"}]}


# ------------------------------------------------------------------------------
# Errors
# ------------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.anyio
async def test_stream_ndjson_emits_error_line_when_fetch_fails():
    result = _FakeResult([], columns=["id"], fetch_error=ValueError("boom"))

    chunks = await _collect(_stream(result))

    assert len(chunks) == 2  # columns chunk, then the error line
    assert json.loads(chunks[1]) == {"__error__": {"type": "ValueError", "message": "boom"}}


@pytest.mark.unit
@pytest.mark.anyio
async def test_stream_ndjson_emits_error_line_when_a_later_fetch_fails():
    result = _FakeResult([(str(i),) for i in range(5)], columns=["id"], fetch_error=ValueError("boom"), fail_after=3)

    chunks = await _collect(_stream(result, chunk_size=3))

    # columns chunk, then the first batch already sent, then the error line
    assert len(chunks) == 3
    assert [json.loads(line) for line in chunks[1].splitlines()] == [{"__row__": [str(i)]} for i in range(3)]
    assert json.loads(chunks[2]) == {"__error__": {"type": "ValueError", "message": "boom"}}


@pytest.mark.unit
@pytest.mark.anyio
async def test_stream_ndjson_flushes_serialized_rows_before_a_serialization_error():
    result = _FakeResult([("0",), ("1",), (object(),)], columns=["id"])

    chunks = await _collect(_stream(result))

    # columns chunk, then everything serialized before the failure, then the error line
    assert len(chunks) == 3
    assert json.loads(chunks[0]) == {"__columns__": ["id"]}
    assert [json.loads(line) for line in chunks[1].splitlines()] == [{"__row__": ["0"]}, {"__row__": ["1"]}]
    assert json.loads(chunks[2])["__error__"]["type"] == "TypeError"


@pytest.mark.unit
@pytest.mark.anyio
async def test_stream_ndjson_leaves_closing_the_result_to_the_caller():
    result = _FakeResult([("0",)], columns=["id"])

    await _collect(_stream(result))

    assert not result.closed


# ------------------------------------------------------------------------------
# Pings and client disconnection
# ------------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.anyio
async def test_stream_ndjson_emits_pings_while_a_fetch_is_slow():
    result = _FakeResult([("0",)], columns=["id"], fetch_delay=0.1)

    chunks = await _collect(_stream(result, heartbeat_secs=0.02))
    records = [json.loads(line) for chunk in chunks for line in chunk.splitlines()]

    assert records[0] == {"__columns__": ["id"]}
    first_row = records.index({"__row__": ["0"]})
    assert first_row >= 3  # at least 2 pings between the columns and the row
    assert all(record == {"__ping__": True} for record in records[1:first_row])


@pytest.mark.unit
@pytest.mark.anyio
async def test_stream_ndjson_emits_no_ping_when_heartbeat_is_disabled():
    result = _FakeResult([("0",)], columns=["id"], fetch_delay=0.05)

    chunks = await _collect(_stream(result, heartbeat_secs=0))
    records = [json.loads(line) for chunk in chunks for line in chunk.splitlines()]

    assert records == [{"__columns__": ["id"]}, {"__row__": ["0"]}]


@pytest.mark.unit
@pytest.mark.anyio
async def test_stream_ndjson_cancels_a_running_fetch_when_client_disconnects():
    result = _FakeResult([("0",)], columns=["id"], fetch_delay=10)
    execution_logs = Mock()

    chunks = await asyncio.wait_for(
        _collect(
            _stream(result, _FakeRequest(disconnect_after=0), heartbeat_secs=0.02, execution_logs=execution_logs)
        ),
        timeout=2,
    )

    assert [json.loads(chunk) for chunk in chunks] == [{"__columns__": ["id"]}]  # no ping once disconnected
    assert result.fetch_cancelled
    assert not result.closed  # closing is up to the caller
    assert not execution_logs.log_execution.called


@pytest.mark.unit
@pytest.mark.anyio
async def test_stream_ndjson_stops_early_when_client_disconnects():
    """After the first chunk is sent, the fake request reports disconnected - the
    generator must stop instead of fetching the remaining rows."""
    result = _FakeResult([(str(i),) for i in range(9)], columns=["id"])
    request = _FakeRequest(disconnect_after=0)

    chunks = await _collect(_stream(result, request, chunk_size=3))

    # columns chunk, then only the first data chunk (3 rows) - the 2nd/3rd chunks never happen
    assert len(chunks) == 2
    assert [json.loads(line) for line in chunks[1].splitlines()] == [{"__row__": [str(i)]} for i in range(3)]
    assert result.fetch_sizes == [3]


@pytest.mark.unit
@pytest.mark.anyio
async def test_stream_ndjson_checks_disconnection_once_per_chunk_not_per_row():
    """is_disconnected() does real work (checks the ASGI receive channel) - it must only
    be called at chunk boundaries, not for every row."""
    result = _FakeResult([(str(i),) for i in range(9)], columns=["id"])  # 3 chunks of 3 with chunk_size=3
    request = _FakeRequest()

    await _collect(_stream(result, request, chunk_size=3))

    assert request.calls == 3


# ------------------------------------------------------------------------------
# pings
# ------------------------------------------------------------------------------


async def _sleep_then(delay: float, value: str = "done") -> str:
    await asyncio.sleep(delay)
    return value


@pytest.mark.unit
@pytest.mark.anyio
async def test_pings_yields_nothing_for_a_fast_task():
    task = asyncio.create_task(_sleep_then(0))

    lines = [line async for line in pings(task, _FakeRequest(), heartbeat_secs=30)]

    assert lines == []
    assert task.done()


@pytest.mark.unit
@pytest.mark.anyio
async def test_pings_yields_ping_lines_until_the_task_is_done():
    task = asyncio.create_task(_sleep_then(0.1))

    lines = [line async for line in pings(task, _FakeRequest(), heartbeat_secs=0.02)]  # type: ignore

    assert len(lines) >= 2
    assert all(json.loads(line) == {"__ping__": True} and line.endswith("\n") for line in lines)
    assert task.done()


@pytest.mark.unit
@pytest.mark.anyio
async def test_pings_disabled_waits_for_the_task_without_checking_the_client():
    task = asyncio.create_task(_sleep_then(0.05))
    request = _FakeRequest(disconnect_after=0)

    lines = [line async for line in pings(task, request, heartbeat_secs=0)]

    assert lines == []
    assert task.done()
    assert request.calls == 0


@pytest.mark.unit
@pytest.mark.anyio
async def test_pings_raises_client_disconnected_and_leaves_the_task_running():
    task = asyncio.create_task(_sleep_then(10))

    with pytest.raises(ClientDisconnected):
        async for _line in pings(task, _FakeRequest(disconnect_after=1), heartbeat_secs=0.02):  # type: ignore
            pass

    assert not task.done()  # stopping or awaiting it is up to the caller
    task.cancel()


# ------------------------------------------------------------------------------
# Central execution logging
# ------------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.anyio
async def test_stream_ndjson_logs_execution_on_successful_completion():
    result = _FakeResult([(str(i),) for i in range(3)], columns=["id"])
    execution_logs = Mock()
    log_entry = _make_log_entry()

    await _collect(_stream(result, execution_logs=execution_logs, log_entry=log_entry))

    assert execution_logs.log_execution.call_count == 1
    logged = execution_logs.log_execution.call_args[0][0]
    assert logged.nb_rows == 3
    assert logged.duration_secs >= 0
    assert logged.server_id == log_entry.server_id
    assert logged.query_id == log_entry.query_id


@pytest.mark.unit
@pytest.mark.anyio
async def test_stream_ndjson_logs_rows_count_across_chunks():
    result = _FakeResult([(str(i),) for i in range(7)], columns=["id"])
    execution_logs = Mock()

    await _collect(_stream(result, chunk_size=3, execution_logs=execution_logs))

    assert execution_logs.log_execution.call_args[0][0].nb_rows == 7


@pytest.mark.unit
@pytest.mark.anyio
async def test_stream_ndjson_does_not_log_when_execution_logs_is_none():
    # Should not raise
    await _collect(_stream(_FakeResult([("0",)], columns=["id"]), execution_logs=None))


@pytest.mark.unit
@pytest.mark.anyio
async def test_stream_ndjson_does_not_log_on_error():
    result = _FakeResult([(object(),)], columns=["id"])
    execution_logs = Mock()

    await _collect(_stream(result, execution_logs=execution_logs))

    assert not execution_logs.log_execution.called


@pytest.mark.unit
@pytest.mark.anyio
async def test_stream_ndjson_does_not_log_when_client_disconnects_early():
    """Only log once 100% of the query's result has reached the client - a client that
    disconnects mid-stream must not be logged as a completed execution."""
    result = _FakeResult([(str(i),) for i in range(9)], columns=["id"])
    execution_logs = Mock()

    await _collect(_stream(result, _FakeRequest(disconnect_after=0), chunk_size=3, execution_logs=execution_logs))

    assert not execution_logs.log_execution.called
