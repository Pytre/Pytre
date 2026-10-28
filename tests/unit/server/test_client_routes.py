"""
Tests for Pytre_Server/routes/client.py's execute_query() helpers - the parts of /execute
testable without a full FastAPI TestClient round trip.
"""

import asyncio
import json
from contextlib import asynccontextmanager
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from Pytre_Core.sql_query.query_drivers_async import AsyncSqlConnectionError, AsyncSqlExecutionError
from Pytre_Core.user_prefs import DisplayFormat
from Pytre_Server.routes.client import ExecuteRequest, _apply_parameters, _run_query


def _make_param(is_hidden: bool, display_value: str = "") -> Mock:
    param = Mock()
    param.is_hidden = is_hidden
    param.display_value = display_value
    return param


def _make_query(params: dict[str, Mock]) -> Mock:
    """Minimal stand-in for Query - _apply_parameters only touches .parameters.params and
    calls .set_display_values(...)."""
    query = Mock()
    query.parameters.params = params
    return query


def _make_request(parameters: dict[str, str]) -> ExecuteRequest:
    return ExecuteRequest(
        server_id="s", query_id="q", template_hash="h", display_format=DisplayFormat(), display_parameters=parameters
    )


# ================================================================================
# _apply_parameters
# ================================================================================


@pytest.mark.unit
def test_apply_parameters_rejects_name_mismatch():
    query = _make_query({"@a": _make_param(is_hidden=False)})
    request = _make_request({"@b": "1"})

    with pytest.raises(HTTPException) as exc_info:
        _apply_parameters(query, request, is_admin=False)

    assert exc_info.value.status_code == 409


@pytest.mark.unit
def test_apply_parameters_rejects_missing_param():
    query = _make_query({"@a": _make_param(is_hidden=False), "@b": _make_param(is_hidden=False)})
    request = _make_request({"@a": "1"})

    with pytest.raises(HTTPException, match="missing.*@b"):
        _apply_parameters(query, request, is_admin=False)


@pytest.mark.unit
def test_apply_parameters_non_admin_cannot_override_hidden_param():
    """The core security fix: a non-admin's submitted value for a hidden parameter (e.g. @USER,
    used for row-level access control) is replaced with the server-computed default before
    being applied - regression for the privilege-escalation gap found in remotes.py review."""
    query = _make_query({"@USER": _make_param(is_hidden=True, display_value="real_user")})
    request = _make_request({"@USER": "someone_else"})

    _apply_parameters(query, request, is_admin=False)

    assert request.display_parameters["@USER"] == "real_user"
    query.set_display_values.assert_called_once_with({"@USER": "real_user"})


@pytest.mark.unit
def test_apply_parameters_admin_can_override_hidden_param():
    query = _make_query({"@USER": _make_param(is_hidden=True, display_value="real_user")})
    request = _make_request({"@USER": "someone_else"})

    _apply_parameters(query, request, is_admin=True)

    assert request.display_parameters["@USER"] == "someone_else"
    query.set_display_values.assert_called_once_with({"@USER": "someone_else"})


@pytest.mark.unit
def test_apply_parameters_leaves_non_hidden_param_untouched_for_non_admin():
    query = _make_query({"@filter": _make_param(is_hidden=False)})
    request = _make_request({"@filter": "2024-01-01"})

    _apply_parameters(query, request, is_admin=False)

    assert request.display_parameters["@filter"] == "2024-01-01"


@pytest.mark.unit
def test_apply_parameters_mix_of_hidden_and_visible_params_for_non_admin():
    query = _make_query(
        {
            "@USER": _make_param(is_hidden=True, display_value="real_user"),
            "@filter": _make_param(is_hidden=False),
        }
    )
    request = _make_request({"@USER": "someone_else", "@filter": "2024-01-01"})

    _apply_parameters(query, request, is_admin=False)

    assert request.display_parameters == {"@USER": "real_user", "@filter": "2024-01-01"}


@pytest.mark.unit
def test_apply_parameters_raises_400_on_invalid_value():
    query = _make_query({"@a": _make_param(is_hidden=False)})
    query.set_display_values.side_effect = ValueError("bad value")
    request = _make_request({"@a": "x"})

    with pytest.raises(HTTPException) as exc_info:
        _apply_parameters(query, request, is_admin=False)

    assert exc_info.value.status_code == 400
    assert "bad value" in exc_info.value.detail


# ================================================================================
# _run_query
# ================================================================================


class _FakeResult:
    """Minimal stand-in for AsyncSqlResult - just enough for stream_result_ndjson to work.
    Row conversion/serialization and column handling are already thoroughly covered in
    tests/unit/server/test_result_streaming.py - only "id"/[rows] matters here.
    Records "close" in `events` (shared with the fake use_driver) to check the ordering."""

    def __init__(self, rows: list[tuple], events: list[str] | None = None, fetch_error: Exception | None = None):
        self._rows = rows
        self._events = events if events is not None else []
        self._fetch_error = fetch_error
        self.close_calls = 0

    def columns(self) -> list[str]:
        return ["id"]

    async def fetch(self, n: int) -> list[tuple]:
        if self._fetch_error is not None:
            raise self._fetch_error
        batch, self._rows = self._rows[:n], self._rows[n:]
        return batch

    async def close(self) -> None:
        self.close_calls += 1
        self._events.append("close")


class _FakeDriver:
    """Driver whose execute() is fully controlled by the test - returns a result, sleeps,
    or raises, depending on what's being exercised."""

    def __init__(self, execute_impl):
        self._execute_impl = execute_impl

    async def execute(self, template, params):
        return await self._execute_impl()


def _make_resources(
    driver: _FakeDriver, events: list[str] | None = None, use_driver_error: Exception | None = None
) -> Mock:
    """Minimal stand-in for ServerResources - _run_query only uses use_driver() and
    execution_logs. Mock() rather than a bespoke class so it type-checks against the
    concrete ServerResources parameter the same way _make_query()/_make_request() already
    stand in for their own concretely-typed parameters elsewhere in this file.
    use_driver() records "enter"/"exit" in `events`, or raises `use_driver_error` like a failing connect."""
    resources = Mock()
    resources.execution_logs = None
    events = events if events is not None else []

    @asynccontextmanager
    async def use_driver(server):
        if use_driver_error is not None:
            raise use_driver_error
        events.append("enter")
        try:
            yield driver
        finally:
            events.append("exit")

    resources.use_driver = use_driver
    return resources


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


def _make_state(heartbeat_secs: float, chunk_size: int = 10) -> Mock:
    state = Mock()
    state.app_ctx.servers.servers_dict = {"s": Mock()}  # "s" matches _make_request()'s server_id
    state.heartbeat_secs = heartbeat_secs
    state.chunk_size = chunk_size
    return state


def _make_user_ctx(username: str = "alice", title: str = "Alice A") -> Mock:
    user_ctx = Mock()
    user_ctx.current_user.username = username
    user_ctx.current_user.title = title
    return user_ctx


async def _collect(chunks) -> list[dict]:
    return [json.loads(line) async for chunk in chunks for line in chunk.splitlines()]


@pytest.mark.unit
@pytest.mark.anyio
async def test_run_query_streams_result_on_success():
    events: list[str] = []
    result = _FakeResult([("1",)], events)

    async def execute_impl():
        return result

    resources = _make_resources(_FakeDriver(execute_impl), events)
    semaphore = asyncio.Semaphore(1)
    await semaphore.acquire()

    records = await _collect(
        _run_query(
            _make_state(heartbeat_secs=30),
            resources,
            _make_request({}),
            _make_user_ctx(),
            "template",
            {},
            semaphore,
            _FakeDisconnectRequest(),
        )
    )

    assert records == [{"__columns__": ["id"]}, {"__row__": ["1"]}]
    assert events == ["enter", "close", "exit"]  # the driver stays in use until the result is closed
    assert result.close_calls == 1
    assert not semaphore.locked()


@pytest.mark.unit
@pytest.mark.anyio
async def test_run_query_closes_result_when_streaming_fails():
    events: list[str] = []
    result = _FakeResult([], events, fetch_error=ValueError("boom"))

    async def execute_impl():
        return result

    resources = _make_resources(_FakeDriver(execute_impl), events)
    semaphore = asyncio.Semaphore(1)
    await semaphore.acquire()

    records = await _collect(
        _run_query(
            _make_state(heartbeat_secs=30),
            resources,
            _make_request({}),
            _make_user_ctx(),
            "template",
            {},
            semaphore,
            _FakeDisconnectRequest(),
        )
    )

    assert records == [{"__columns__": ["id"]}, {"__error__": {"type": "ValueError", "message": "boom"}}]
    assert events == ["enter", "close", "exit"]
    assert not semaphore.locked()


@pytest.mark.unit
@pytest.mark.anyio
async def test_run_query_emits_heartbeat_pings_while_query_executes():
    async def execute_impl():
        await asyncio.sleep(0.1)
        return _FakeResult([])

    resources = _make_resources(_FakeDriver(execute_impl))
    semaphore = asyncio.Semaphore(1)
    await semaphore.acquire()

    records = await _collect(
        _run_query(
            _make_state(heartbeat_secs=0.02),
            resources,
            _make_request({}),
            _make_user_ctx(),
            "template",
            {},
            semaphore,
            _FakeDisconnectRequest(),
        )
    )

    pings = [r for r in records if "__ping__" in r]
    assert len(pings) >= 2
    assert records[-1] == {"__columns__": ["id"]}  # the real result still follows the pings
    assert not semaphore.locked()


@pytest.mark.unit
@pytest.mark.anyio
async def test_run_query_heartbeat_disabled_when_interval_is_zero():
    async def execute_impl():
        await asyncio.sleep(0.05)
        return _FakeResult([])

    resources = _make_resources(_FakeDriver(execute_impl))
    semaphore = asyncio.Semaphore(1)
    await semaphore.acquire()

    records = await _collect(
        _run_query(
            _make_state(heartbeat_secs=0),
            resources,
            _make_request({}),
            _make_user_ctx(),
            "template",
            {},
            semaphore,
            _FakeDisconnectRequest(),
        )
    )

    assert records == [{"__columns__": ["id"]}]
    assert not semaphore.locked()


@pytest.mark.unit
@pytest.mark.anyio
async def test_run_query_detects_client_disconnect_during_execution():
    """A client disconnecting while the query is still running must stop the yielding (no
    columns/rows ever reached) but still let the query finish rather than abandoning it
    mid-flight, then close its result while the driver is still in use."""
    finished = False
    events: list[str] = []
    result = _FakeResult([], events)

    async def execute_impl():
        nonlocal finished
        await asyncio.sleep(0.2)
        finished = True
        return result

    resources = _make_resources(_FakeDriver(execute_impl), events)
    semaphore = asyncio.Semaphore(1)
    await semaphore.acquire()

    records = await _collect(
        _run_query(
            _make_state(heartbeat_secs=0.02),
            resources,
            _make_request({}),
            _make_user_ctx(),
            "template",
            {},
            semaphore,
            _FakeDisconnectRequest(disconnect_after=1),
        )
    )

    assert records  # at least one ping happened before the disconnect was noticed
    assert all("__ping__" in r for r in records)  # __columns__/rows never reached
    assert finished  # the background task was awaited to completion, not abandoned
    assert events == ["enter", "close", "exit"]
    assert not semaphore.locked()


@pytest.mark.unit
@pytest.mark.anyio
async def test_run_query_disconnect_during_failing_execution_has_nothing_to_close():
    events: list[str] = []

    async def execute_impl():
        await asyncio.sleep(0.2)
        raise AsyncSqlExecutionError("boom")

    resources = _make_resources(_FakeDriver(execute_impl), events)
    semaphore = asyncio.Semaphore(1)
    await semaphore.acquire()

    records = await _collect(
        _run_query(
            _make_state(heartbeat_secs=0.02),
            resources,
            _make_request({}),
            _make_user_ctx(),
            "template",
            {},
            semaphore,
            _FakeDisconnectRequest(disconnect_after=1),
        )
    )

    assert all("__ping__" in r for r in records)  # the client is gone, the error isn't sent
    assert events == ["enter", "exit"]
    assert not semaphore.locked()


@pytest.mark.unit
@pytest.mark.anyio
async def test_run_query_yields_error_line_when_driver_cannot_connect():
    async def execute_impl():
        raise AssertionError("execute() must not be reached")

    resources = _make_resources(_FakeDriver(execute_impl), use_driver_error=AsyncSqlConnectionError("refused"))
    semaphore = asyncio.Semaphore(1)
    await semaphore.acquire()

    records = await _collect(
        _run_query(
            _make_state(heartbeat_secs=30),
            resources,
            _make_request({}),
            _make_user_ctx(),
            "template",
            {},
            semaphore,
            _FakeDisconnectRequest(),
        )
    )

    assert records == [{"__error__": {"type": "AsyncSqlConnectionError", "message": "Server unavailable: refused"}}]
    assert not semaphore.locked()


@pytest.mark.unit
@pytest.mark.anyio
async def test_run_query_yields_error_line_on_connection_error():
    async def execute_impl():
        raise AsyncSqlConnectionError("boom")

    resources = _make_resources(_FakeDriver(execute_impl))
    semaphore = asyncio.Semaphore(1)
    await semaphore.acquire()

    records = await _collect(
        _run_query(
            _make_state(heartbeat_secs=30),
            resources,
            _make_request({}),
            _make_user_ctx(),
            "template",
            {},
            semaphore,
            _FakeDisconnectRequest(),
        )
    )

    assert records == [{"__error__": {"type": "AsyncSqlConnectionError", "message": "Server unavailable: boom"}}]
    assert not semaphore.locked()


@pytest.mark.unit
@pytest.mark.anyio
async def test_run_query_yields_error_line_on_execution_error():
    async def execute_impl():
        raise AsyncSqlExecutionError("boom")

    resources = _make_resources(_FakeDriver(execute_impl))
    semaphore = asyncio.Semaphore(1)
    await semaphore.acquire()

    records = await _collect(
        _run_query(
            _make_state(heartbeat_secs=30),
            resources,
            _make_request({}),
            _make_user_ctx(),
            "template",
            {},
            semaphore,
            _FakeDisconnectRequest(),
        )
    )

    assert records == [{"__error__": {"type": "AsyncSqlExecutionError", "message": "SQL error: boom"}}]
    assert not semaphore.locked()


@pytest.mark.unit
@pytest.mark.anyio
async def test_run_query_yields_error_line_on_unexpected_error():
    async def execute_impl():
        raise RuntimeError("boom")

    resources = _make_resources(_FakeDriver(execute_impl))
    semaphore = asyncio.Semaphore(1)
    await semaphore.acquire()

    records = await _collect(
        _run_query(
            _make_state(heartbeat_secs=30),
            resources,
            _make_request({}),
            _make_user_ctx(),
            "template",
            {},
            semaphore,
            _FakeDisconnectRequest(),
        )
    )

    assert records == [{"__error__": {"type": "RuntimeError", "message": "Unexpected error: boom"}}]
    assert not semaphore.locked()


@pytest.mark.unit
@pytest.mark.anyio
async def test_run_query_releases_semaphore_when_closed_early():
    """Simulates Starlette calling .aclose() on the generator before it's done (e.g. it
    stops consuming for its own reasons) - the finally must still run via GeneratorExit."""

    events: list[str] = []

    async def execute_impl():
        return _FakeResult([("1",)], events)

    resources = _make_resources(_FakeDriver(execute_impl), events)
    semaphore = asyncio.Semaphore(1)
    await semaphore.acquire()

    gen = _run_query(
        _make_state(heartbeat_secs=30),
        resources,
        _make_request({}),
        _make_user_ctx(),
        "template",
        {},
        semaphore,
        _FakeDisconnectRequest(),
    )
    await gen.__anext__()  # consume only the columns chunk
    await gen.aclose()

    assert events == ["enter", "close", "exit"]
    assert not semaphore.locked()
