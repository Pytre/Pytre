"""
Integration tests characterizing event loop responsiveness with fastmssql, independent
of how Pytre_Server/app.py actually copes with it today.

Confirms: await driver.execute(...) stays responsive on its own (fastmssql's PyO3
binding yields control during the network wait), but the subsequent `for row in
result` iteration has no await point (QueryStream is sync-only) and starves the
loop for its whole duration. Wrapping that iteration in loop.run_in_executor() is
one way to restore responsiveness (tested below) - but it is not what /execute
actually does: it streams NDJSON in chunks instead (Pytre_Server/app.py's
_stream_result_ndjson), where the chunk boundaries themselves provide the yield points,
with no executor involved.

Requires the local mssql-test docker container (see tests/resources/build_test_keepass.py for
its pinned connection details) - skipped automatically if it isn't reachable.
"""

import asyncio
import time

import pytest

from Pytre_Core.sql_query.query_drivers_async import FastMssqlDriver
from tests.integration.conftest import requires_docker
from tests.resources.build_test_keepass import TEST_SQL_SERVER

pytestmark = pytest.mark.integration

_HEARTBEAT_INTERVAL = 0.01

# No persisted table needed - TOP bounds the synthetic cross join to ~500 000 rows.
_BIG_QUERY = """
SELECT TOP (500000) ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) AS n
FROM sys.all_columns a CROSS JOIN sys.all_columns b
"""


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def driver():
    d = FastMssqlDriver(
        host=TEST_SQL_SERVER["host"],
        port=int(TEST_SQL_SERVER["port"]),
        database=TEST_SQL_SERVER["database"],
        username=TEST_SQL_SERVER["user"],
        password=TEST_SQL_SERVER["password"],
        trust_certificate=True,  # mssql-test docker uses a self-signed certificate
    )
    await d.connect()
    yield d
    await d.disconnect()


async def _heartbeat(interval: float, stop: asyncio.Event, timestamps: list[float]) -> None:
    """Records a wake-up timestamp every `interval`. A gap much larger than
    `interval` between two consecutive timestamps means the event loop was
    starved (something else ran without yielding) during that gap."""
    while not stop.is_set():
        await asyncio.sleep(interval)
        timestamps.append(time.monotonic())


def _max_gap_in_window(timestamps: list[float], start: float, end: float) -> float:
    """Largest gap between heartbeats (or window edges) within [start, end].
    Close to _HEARTBEAT_INTERVAL means the loop stayed responsive; close to
    (end - start) means it was blocked for essentially the whole window."""
    in_window = [t for t in timestamps if start <= t <= end]
    edges = [start] + in_window + [end]
    return max(b - a for a, b in zip(edges, edges[1:]))


async def _to_list_sync(result) -> list:
    """Wraps the raw synchronous iteration in a coroutine so it can be passed to
    _measure() - it still blocks the loop for its whole duration, there is no
    await point inside the for loop itself."""
    return [row.to_dict() for row in result]


async def _measure(coro_awaitable) -> tuple[list, float, float]:
    """Runs `coro_awaitable` concurrently with a heartbeat, returns (result, elapsed, max_gap)."""
    stop = asyncio.Event()
    timestamps: list[float] = []
    heartbeat_task = asyncio.create_task(_heartbeat(_HEARTBEAT_INTERVAL, stop, timestamps))

    start = time.monotonic()
    result = await coro_awaitable
    end = time.monotonic()

    stop.set()
    await heartbeat_task

    return result, end - start, _max_gap_in_window(timestamps, start, end)


@requires_docker
@pytest.mark.anyio
async def test_query_call_does_not_block_event_loop(driver):
    """await driver.execute(...) itself must stay responsive - fastmssql's PyO3 binding
    is expected to release control during the network wait, unlike the row iteration
    below (see test_raw_iteration_blocks_event_loop)."""
    _result, elapsed, max_gap = await _measure(driver.execute(_BIG_QUERY, {}))

    print(f"\nquery() elapsed: {elapsed:.3f}s, max heartbeat gap: {max_gap:.3f}s")
    # Relative to elapsed rather than a fixed multiple of the heartbeat interval - a fixed
    # multiple is too tight once tests run under pytest-xdist (many worker processes competing
    # for CPU inflate absolute scheduling jitter well past a small constant like 5x10ms), same
    # reasoning already applied to the executor-wrapped case below.
    assert max_gap < elapsed * 0.5


@requires_docker
@pytest.mark.anyio
async def test_raw_iteration_blocks_event_loop(driver):
    """Characterization test: `for row in result` has no await point (QueryStream is
    sync-only), so it starves the loop for its whole duration. If this ever starts
    failing because fastmssql grows a real async iterator, that's good news - the
    chunked streaming in Pytre_Server/app.py's _stream_result_ndjson (and the executor-based
    alternative characterized below) would no longer be needed."""
    result = await driver.execute(_BIG_QUERY, {})

    rows, elapsed, max_gap = await _measure(_to_list_sync(result))

    assert len(rows) == 500_000
    print(f"\nraw iteration elapsed: {elapsed:.3f}s, max heartbeat gap: {max_gap:.3f}s")
    assert max_gap > elapsed * 0.9  # essentially the whole window, i.e. fully blocked


@requires_docker
@pytest.mark.anyio
async def test_iteration_wrapped_in_executor_does_not_block_event_loop(driver):
    """Characterization test, not a regression test tied to /execute's current
    implementation: wrapping row materialization in loop.run_in_executor() is one way
    to keep the event loop responsive while iterating a large result. Pytre_Server/app.py's
    /execute route actually uses a different technique (chunked NDJSON streaming via
    _stream_result_ndjson, where each yield between chunks hands control back to the loop) -
    this test documents that the executor approach also works, as a lower-level fact
    about fastmssql/asyncio, independent of which technique /execute happens to use."""
    result = await driver.execute(_BIG_QUERY, {})
    loop = asyncio.get_running_loop()

    rows, elapsed, max_gap = await _measure(loop.run_in_executor(None, lambda: [row.to_dict() for row in result]))

    assert len(rows) == 500_000
    print(f"\nwrapped iteration elapsed: {elapsed:.3f}s, max heartbeat gap: {max_gap:.3f}s")
    # Relative to elapsed rather than a fixed multiple of the heartbeat interval - absolute
    # thread-scheduling jitter grows under system load, but the fix keeps the loop responsive
    # for well under half the window, vs. ~the whole window for the unwrapped raw case above.
    assert max_gap < elapsed * 0.5
