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
from Pytre_Server.result_streaming import stream_result_and_release, _stream_result_ndjson


@pytest.fixture
def anyio_backend():
    return "asyncio"


class _FakeRow:
    def __init__(self, data: dict):
        self._data = data

    def to_dict(self) -> dict:
        return self._data


class _FailingRow:
    """Simulates a row that raises when converted/serialized (e.g. ConversionError)."""

    def to_dict(self) -> dict:
        raise ValueError("boom")


class _FakeResult:
    """Minimal stand-in for AsyncSqlResult - exposes columns() and iterates rows."""

    def __init__(self, rows: list, columns: list[str] | None = None, columns_error: Exception | None = None):
        self._rows = rows
        self._columns = columns
        self._columns_error = columns_error

    def columns(self) -> list[str]:
        if self._columns_error is not None:
            raise self._columns_error
        assert self._columns is not None
        return self._columns

    def __iter__(self):
        return iter(self._rows)


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


async def _collect(chunks) -> list[str]:
    return [chunk async for chunk in chunks]


@pytest.mark.unit
@pytest.mark.anyio
async def test_stream_ndjson_always_emits_columns_line_first():
    result = _FakeResult([], columns=["id"])

    chunks = await _collect(
        _stream_result_ndjson(result, _FakeRequest(), chunk_size=3, execution_logs=None, log_entry=_make_log_entry())
    )

    assert len(chunks) == 1
    assert json.loads(chunks[0]) == {"__columns__": ["id"]}


@pytest.mark.unit
@pytest.mark.anyio
async def test_stream_ndjson_falls_back_to_empty_columns_when_result_has_no_rows():
    """fastmssql's columns() raises ValueError when the result has 0 rows (confirmed
    against the real driver) - must degrade to an empty columns list, not crash the stream."""
    result = _FakeResult([], columns_error=ValueError("No column information available"))

    chunks = await _collect(
        _stream_result_ndjson(result, _FakeRequest(), chunk_size=3, execution_logs=None, log_entry=_make_log_entry())
    )

    assert len(chunks) == 1
    assert json.loads(chunks[0]) == {"__columns__": []}


@pytest.mark.unit
@pytest.mark.anyio
async def test_stream_ndjson_single_chunk_when_under_chunk_size():
    rows = [_FakeRow({"id": str(i)}) for i in range(2)]
    result = _FakeResult(rows, columns=["id"])

    chunks = await _collect(
        _stream_result_ndjson(result, _FakeRequest(), chunk_size=3, execution_logs=None, log_entry=_make_log_entry())
    )

    assert json.loads(chunks[0]) == {"__columns__": ["id"]}
    assert len(chunks) == 2
    lines = chunks[1].splitlines()
    assert [json.loads(line) for line in lines] == [{"__row__": ["0"]}, {"__row__": ["1"]}]


@pytest.mark.unit
@pytest.mark.anyio
async def test_stream_ndjson_splits_into_multiple_chunks():
    rows = [_FakeRow({"id": str(i)}) for i in range(7)]
    result = _FakeResult(rows, columns=["id"])

    chunks = await _collect(
        _stream_result_ndjson(result, _FakeRequest(), chunk_size=3, execution_logs=None, log_entry=_make_log_entry())
    )

    assert json.loads(chunks[0]) == {"__columns__": ["id"]}
    # 7 rows, chunk_size=3 -> chunks of 3, 3, 1 (after the columns chunk)
    assert [len(chunk.splitlines()) for chunk in chunks[1:]] == [3, 3, 1]

    all_rows = [json.loads(line) for chunk in chunks[1:] for line in chunk.splitlines()]
    assert all_rows == [{"__row__": [str(i)]} for i in range(7)]


@pytest.mark.unit
@pytest.mark.anyio
async def test_stream_ndjson_each_chunk_ends_with_newline():
    rows = [_FakeRow({"id": str(i)}) for i in range(3)]
    result = _FakeResult(rows, columns=["id"])

    chunks = await _collect(
        _stream_result_ndjson(result, _FakeRequest(), chunk_size=3, execution_logs=None, log_entry=_make_log_entry())
    )

    assert all(chunk.endswith("\n") for chunk in chunks)


@pytest.mark.unit
@pytest.mark.anyio
async def test_stream_ndjson_preserves_row_content():
    rows = [_FakeRow({"title": "a", "x3_id": "1"}), _FakeRow({"title": "b", "x3_id": "2"})]
    result = _FakeResult(rows, columns=["title", "x3_id"])

    chunks = await _collect(
        _stream_result_ndjson(
            result, _FakeRequest(), chunk_size=10, execution_logs=None, log_entry=_make_log_entry()
        )
    )

    lines = chunks[1].splitlines()
    assert json.loads(lines[0]) == {"__row__": ["a", "1"]}
    assert json.loads(lines[1]) == {"__row__": ["b", "2"]}


@pytest.mark.unit
@pytest.mark.anyio
async def test_stream_ndjson_wraps_decimal_and_datetime_values():
    """Values now travel typed, not pre-formatted: decode_row_value() (Pytre_Core/remotes.py)
    reconstructs real Decimal/datetime objects client-side, so the client's own SqlTypeConverter
    settings apply - datetime/Decimal need wrapping since JSON has no native date type and a
    plain JSON number would lose Decimal's exact precision."""
    rows = [_FakeRow({"amount": Decimal("12.50"), "created": datetime(2026, 1, 1, 10, 30)})]
    result = _FakeResult(rows, columns=["amount", "created"])

    chunks = await _collect(
        _stream_result_ndjson(
            result, _FakeRequest(), chunk_size=10, execution_logs=None, log_entry=_make_log_entry()
        )
    )

    decoded = json.loads(chunks[1].splitlines()[0])
    assert decoded == {"__row__": [{"$decimal": "12.50"}, {"$datetime": "2026-01-01T10:30:00"}]}


@pytest.mark.unit
@pytest.mark.anyio
async def test_stream_ndjson_passes_through_json_native_types_unwrapped():
    """str/int/float/bool/None survive JSON natively - no wrapping needed."""
    rows = [_FakeRow({"name": "a", "count": 3, "ratio": 1.5, "active": True, "note": None})]
    result = _FakeResult(rows, columns=["name", "count", "ratio", "active", "note"])

    chunks = await _collect(
        _stream_result_ndjson(
            result, _FakeRequest(), chunk_size=10, execution_logs=None, log_entry=_make_log_entry()
        )
    )

    decoded = json.loads(chunks[1].splitlines()[0])
    assert decoded == {"__row__": ["a", 3, 1.5, True, None]}


@pytest.mark.unit
@pytest.mark.anyio
async def test_stream_ndjson_wraps_plain_date_values():
    """A plain date (not datetime) needs its own wrapping - datetime is a subclass of date,
    so _encode_row_value() must check datetime first or every datetime would also match
    isinstance(value, date) and be wrapped as "$date" instead of "$datetime"."""
    rows = [_FakeRow({"day": date(2026, 1, 1)})]
    result = _FakeResult(rows, columns=["day"])

    chunks = await _collect(
        _stream_result_ndjson(
            result, _FakeRequest(), chunk_size=10, execution_logs=None, log_entry=_make_log_entry()
        )
    )

    decoded = json.loads(chunks[1].splitlines()[0])
    assert decoded == {"__row__": [{"$date": "2026-01-01"}]}


@pytest.mark.unit
@pytest.mark.anyio
async def test_stream_ndjson_emits_error_line_when_row_conversion_fails():
    result = _FakeResult([_FailingRow()], columns=["id"])

    chunks = await _collect(
        _stream_result_ndjson(
            result, _FakeRequest(), chunk_size=10, execution_logs=None, log_entry=_make_log_entry()
        )
    )

    assert len(chunks) == 2  # columns chunk, then the error line
    decoded = json.loads(chunks[1].splitlines()[0])
    assert decoded == {"__error__": {"type": "ValueError", "message": "boom"}}


@pytest.mark.unit
@pytest.mark.anyio
async def test_stream_ndjson_flushes_successful_rows_before_error():
    rows = [_FakeRow({"id": "0"}), _FakeRow({"id": "1"}), _FailingRow()]
    result = _FakeResult(rows, columns=["id"])

    chunks = await _collect(
        _stream_result_ndjson(
            result, _FakeRequest(), chunk_size=10, execution_logs=None, log_entry=_make_log_entry()
        )
    )

    # columns chunk, then everything read before the failure, then the error line
    assert len(chunks) == 3
    assert json.loads(chunks[0]) == {"__columns__": ["id"]}
    assert [json.loads(line) for line in chunks[1].splitlines()] == [{"__row__": ["0"]}, {"__row__": ["1"]}]
    assert json.loads(chunks[2].splitlines()[0]) == {"__error__": {"type": "ValueError", "message": "boom"}}


@pytest.mark.unit
@pytest.mark.anyio
async def test_stream_ndjson_stops_early_when_client_disconnects():
    """After the first chunk is sent, the fake request reports disconnected - the
    generator must stop instead of serializing the remaining rows."""
    rows = [_FakeRow({"id": str(i)}) for i in range(9)]
    result = _FakeResult(rows, columns=["id"])
    request = _FakeRequest(disconnect_after=0)

    chunks = await _collect(
        _stream_result_ndjson(result, request, chunk_size=3, execution_logs=None, log_entry=_make_log_entry())
    )

    # columns chunk, then only the first data chunk (3 rows) - the 2nd/3rd chunks never happen
    assert len(chunks) == 2
    assert [json.loads(line) for line in chunks[1].splitlines()] == [{"__row__": [str(i)]} for i in range(3)]


@pytest.mark.unit
@pytest.mark.anyio
async def test_stream_ndjson_checks_disconnection_once_per_chunk_not_per_row():
    """is_disconnected() does real work (checks the ASGI receive channel) - it must only
    be called at chunk boundaries, not for every row."""
    rows = [_FakeRow({"id": str(i)}) for i in range(9)]  # 3 chunks of 3 with chunk_size=3
    result = _FakeResult(rows, columns=["id"])
    request = _FakeRequest()

    await _collect(
        _stream_result_ndjson(result, request, chunk_size=3, execution_logs=None, log_entry=_make_log_entry())
    )

    assert request.calls == 3


# ------------------------------------------------------------------------------
# Central execution logging
# ------------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.anyio
async def test_stream_ndjson_logs_execution_on_successful_completion():
    rows = [_FakeRow({"id": str(i)}) for i in range(3)]
    result = _FakeResult(rows, columns=["id"])
    execution_logs = Mock()
    log_entry = _make_log_entry()

    await _collect(
        _stream_result_ndjson(result, _FakeRequest(), chunk_size=10, execution_logs=execution_logs, log_entry=log_entry)
    )

    assert execution_logs.log_execution.call_count == 1
    logged = execution_logs.log_execution.call_args[0][0]
    assert logged.nb_rows == 3
    assert logged.duration_secs >= 0
    assert logged.server_id == log_entry.server_id
    assert logged.query_id == log_entry.query_id


@pytest.mark.unit
@pytest.mark.anyio
async def test_stream_ndjson_does_not_log_when_execution_logs_is_none():
    rows = [_FakeRow({"id": "0"})]
    result = _FakeResult(rows, columns=["id"])

    # Should not raise
    await _collect(
        _stream_result_ndjson(
            result, _FakeRequest(), chunk_size=10, execution_logs=None, log_entry=_make_log_entry()
        )
    )


@pytest.mark.unit
@pytest.mark.anyio
async def test_stream_ndjson_does_not_log_on_row_conversion_error():
    result = _FakeResult([_FailingRow()], columns=["id"])
    execution_logs = Mock()

    await _collect(
        _stream_result_ndjson(
            result, _FakeRequest(), chunk_size=10, execution_logs=execution_logs, log_entry=_make_log_entry()
        )
    )

    assert not execution_logs.log_execution.called


@pytest.mark.unit
@pytest.mark.anyio
async def test_stream_ndjson_does_not_log_when_client_disconnects_early():
    """Only log once 100% of the query's result has reached the client - a client that
    disconnects mid-stream must not be logged as a completed execution."""
    rows = [_FakeRow({"id": str(i)}) for i in range(9)]
    result = _FakeResult(rows, columns=["id"])
    execution_logs = Mock()
    request = _FakeRequest(disconnect_after=0)

    await _collect(
        _stream_result_ndjson(
            result, request, chunk_size=3, execution_logs=execution_logs, log_entry=_make_log_entry()
        )
    )

    assert not execution_logs.log_execution.called


# ------------------------------------------------------------------------------
# stream_result_and_release
# ------------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.anyio
async def test_stream_result_and_release_releases_after_normal_completion():
    rows = [_FakeRow({"id": str(i)}) for i in range(3)]
    result = _FakeResult(rows, columns=["id"])
    semaphore = asyncio.Semaphore(1)
    await semaphore.acquire()
    assert semaphore.locked()

    await _collect(
        stream_result_and_release(
            result, _FakeRequest(), semaphore, chunk_size=10, execution_logs=None, log_entry=_make_log_entry()
        )
    )

    assert not semaphore.locked()


@pytest.mark.unit
@pytest.mark.anyio
async def test_stream_result_and_release_releases_when_client_disconnects():
    rows = [_FakeRow({"id": str(i)}) for i in range(9)]
    result = _FakeResult(rows, columns=["id"])
    semaphore = asyncio.Semaphore(1)
    await semaphore.acquire()

    await _collect(
        stream_result_and_release(
            result,
            _FakeRequest(disconnect_after=0),
            semaphore,
            chunk_size=3,
            execution_logs=None,
            log_entry=_make_log_entry(),
        )
    )

    assert not semaphore.locked()


@pytest.mark.unit
@pytest.mark.anyio
async def test_stream_result_and_release_releases_when_row_conversion_fails():
    rows = [_FakeRow({"id": "0"}), _FailingRow()]
    result = _FakeResult(rows, columns=["id"])
    semaphore = asyncio.Semaphore(1)
    await semaphore.acquire()

    await _collect(
        stream_result_and_release(
            result, _FakeRequest(), semaphore, chunk_size=10, execution_logs=None, log_entry=_make_log_entry()
        )
    )

    assert not semaphore.locked()


@pytest.mark.unit
@pytest.mark.anyio
async def test_stream_result_and_release_releases_when_closed_early():
    """Simulates Starlette calling .aclose() on the generator before it's done (e.g. it
    stops consuming for its own reasons) - the finally must still run via GeneratorExit."""
    rows = [_FakeRow({"id": str(i)}) for i in range(7)]  # chunk_size=3 -> chunks of 3, 3, 1
    result = _FakeResult(rows, columns=["id"])
    semaphore = asyncio.Semaphore(1)
    await semaphore.acquire()

    gen = stream_result_and_release(
        result, _FakeRequest(), semaphore, chunk_size=3, execution_logs=None, log_entry=_make_log_entry()
    )
    await gen.__anext__()  # consume only the columns chunk, all rows are left unread
    await gen.aclose()

    assert not semaphore.locked()
