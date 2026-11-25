"""
Tests for Pytre_Server/jobs.py: executions by jobs, read by batches, without FastAPI.
"""

import asyncio
import json
import threading
import time
from contextlib import AbstractAsyncContextManager
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from Pytre_Core.logs_central import ExecutionLogEntry
from Pytre_Core.remote_protocol import JOB_ABANDON_SECS, Batch, JobKey, JobStatus
from Pytre_Core.sql_query.query_drivers_server import AsyncSqlConnectionError, AsyncSqlExecutionError
from Pytre_Server import jobs as jobs_module
from Pytre_Server.jobs import Job, JobNotFound, JobRegistry, JobSequenceError


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def short_wait(monkeypatch):
    """A response waits 50 ms at most instead of JOB_WAIT_SECS"""
    monkeypatch.setattr(jobs_module, "JOB_WAIT_SECS", 0.05)


class _FakeResult:
    """Minimal stand-in for AsyncSqlResult - fetch(n) returns the next n rows, after `fetch_delay`, or raises
    `fetch_error`"""

    def __init__(
        self, rows: list[tuple], columns: list[str], fetch_delay: float = 0, fetch_error: Exception | None = None
    ):
        self._rows = rows
        self._columns = columns
        self._fetch_delay = fetch_delay
        self._fetch_error = fetch_error
        self._position = 0
        self.fetch_sizes: list[int] = []
        self.closed = False

    def columns(self) -> list[str]:
        return self._columns

    async def fetch(self, n: int) -> list[tuple]:
        self.fetch_sizes.append(n)
        await asyncio.sleep(self._fetch_delay)
        if self._fetch_error is not None:
            raise self._fetch_error
        batch = self._rows[self._position : self._position + n]
        self._position += len(batch)
        return batch

    async def close(self) -> None:
        self.closed = True


class _FakeDriver:
    """Minimal stand-in for AsyncSqlDriver - execute() gives `result` after `delay`, or raises `error`"""

    def __init__(self, result: _FakeResult, delay: float = 0, error: Exception | None = None):
        self._result = result
        self._delay = delay
        self._error = error
        self.cancelled = False

    async def execute(self, template: str, params: dict) -> _FakeResult:
        try:
            await asyncio.sleep(self._delay)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        if self._error is not None:
            raise self._error
        return self._result


class _FakeDriverContext(AbstractAsyncContextManager):
    """Stands for ServerResources.use_driver(server): tells whether the job entered it and left it"""

    def __init__(self, driver: _FakeDriver):
        self.driver = driver
        self.entered = False
        self.exited = False

    async def __aenter__(self) -> _FakeDriver:
        self.entered = True
        return self.driver

    async def __aexit__(self, *exc_info) -> None:
        self.exited = True


def _log_entry(user_id: str = "u1") -> ExecutionLogEntry:
    return ExecutionLogEntry(
        server_id="SRV1",
        user_id=user_id,
        user_display_name="User One",
        query_id="Q1",
        start=datetime.now(timezone.utc),
        parameters={"@p": "1"},
    )


class _Started:
    """A job started on fake resources, and those resources"""

    def __init__(self, job: Job, semaphore: asyncio.Semaphore, context: _FakeDriverContext, result: _FakeResult):
        self.job = job
        self.semaphore = semaphore
        self.context = context
        self.result = result

    @property
    def freed(self) -> bool:
        """The driver is no longer used and the user's slot is free again"""
        return self.context.exited and not self.semaphore.locked()


async def _start(
    rows: list[tuple] | None = None,
    batch_size: int = 2,
    execute_delay: float = 0,
    execute_error: Exception | None = None,
    fetch_delay: float = 0,
    fetch_error: Exception | None = None,
    execution_logs: Mock | None = None,
) -> _Started:
    result = _FakeResult(rows or [], ["id"], fetch_delay=fetch_delay, fetch_error=fetch_error)
    context = _FakeDriverContext(_FakeDriver(result, delay=execute_delay, error=execute_error))
    semaphore = asyncio.Semaphore(1)
    await semaphore.acquire()

    job = Job(_log_entry(), batch_size, execution_logs)
    job.start(context, "SELECT 1", {}, semaphore)
    return _Started(job, semaphore, context, result)


async def _status(job: Job) -> dict:
    return json.loads(await job.wait_status())


async def _batch(job: Job, n: int) -> dict:
    return json.loads(await job.get_batch(n))


async def _ready(started: _Started) -> _Started:
    assert (await _status(started.job))[JobKey.STATUS] == JobStatus.READY
    return started


# ------------------------------------------------------------------------------
# Execution
# ------------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.anyio
async def test_status_is_running_until_the_execution_is_over():
    started = await _start(execute_delay=0.2)

    assert (await _status(started.job))[JobKey.STATUS] == JobStatus.RUNNING
    await asyncio.sleep(0.2)
    assert (await _status(started.job))[JobKey.STATUS] == JobStatus.READY


@pytest.mark.unit
@pytest.mark.anyio
async def test_the_driver_is_used_from_the_start_of_the_execution():
    started = await _start(execute_delay=0.2)

    await _status(started.job)

    assert started.context.entered and not started.context.exited


@pytest.mark.unit
@pytest.mark.anyio
@pytest.mark.parametrize(
    "error, prefix",
    [
        (AsyncSqlConnectionError("refused"), "Server unavailable: "),
        (AsyncSqlExecutionError("Invalid object name"), "SQL error: "),
        (RuntimeError("boom"), "Unexpected error: "),
    ],
    ids=["connection", "sql", "unexpected"],
)
async def test_a_failed_execution_reports_its_classified_error_and_frees_everything(error, prefix):
    started = await _start(execute_error=error)

    body = await _status(started.job)

    assert body[JobKey.STATUS] == JobStatus.ERROR
    assert body[JobKey.ERROR] == {"type": type(error).__name__, "message": f"{prefix}{error}"}
    assert started.freed


# ------------------------------------------------------------------------------
# Batches
# ------------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.anyio
async def test_batches_are_read_in_sequence_with_the_columns_in_the_first_one_only():
    started = await _ready(await _start(rows=[(1,), (2,), (3,)]))

    first = await _batch(started.job, 1)
    second = await _batch(started.job, 2)

    assert (first[JobKey.COLUMNS], first[JobKey.ROWS], first[JobKey.LAST]) == (["id"], [[1], [2]], False)
    assert JobKey.COLUMNS not in second
    assert (second[JobKey.ROWS], second[JobKey.LAST]) == ([[3]], True)
    assert started.result.fetch_sizes == [2, 2]


@pytest.mark.unit
@pytest.mark.anyio
async def test_a_full_batch_may_be_followed_by_an_empty_last_one():
    started = await _ready(await _start(rows=[(1,), (2,)]))

    assert (await _batch(started.job, 1))[JobKey.LAST] is False
    assert (await _batch(started.job, 2))[JobKey.ROWS] == []


@pytest.mark.unit
@pytest.mark.anyio
async def test_an_empty_result_has_a_single_batch_with_its_columns():
    started = await _ready(await _start(rows=[]))

    body = await _batch(started.job, 1)

    assert (body[JobKey.COLUMNS], body[JobKey.ROWS], body[JobKey.LAST]) == (["id"], [], True)


@pytest.mark.unit
@pytest.mark.anyio
async def test_the_last_batch_sent_is_sent_again_if_asked_again():
    """The response may have been lost on its way to the client"""
    started = await _ready(await _start(rows=[(1,), (2,), (3,)]))

    first = await started.job.get_batch(1)

    assert await started.job.get_batch(1) == first


@pytest.mark.unit
@pytest.mark.anyio
async def test_the_next_batch_is_prepared_while_the_client_handles_the_current_one():
    started = await _ready(await _start(rows=[(1,), (2,), (3,)]))

    await _batch(started.job, 1)
    await asyncio.sleep(0.01)

    assert len(started.result.fetch_sizes) == 2


@pytest.mark.unit
@pytest.mark.anyio
async def test_a_batch_not_ready_in_time_is_pending_then_sent_once_ready():
    started = await _ready(await _start(rows=[(1,)], fetch_delay=0.2))

    assert (await _batch(started.job, 1))[JobKey.STATUS] == JobStatus.PENDING
    await asyncio.sleep(0.2)
    assert (await _batch(started.job, 1))[JobKey.STATUS] == JobStatus.OK


@pytest.mark.unit
@pytest.mark.anyio
async def test_a_batch_asked_before_the_end_of_the_execution_is_refused():
    started = await _start(execute_delay=0.2)

    with pytest.raises(JobSequenceError):
        await started.job.get_batch(1)


@pytest.mark.unit
@pytest.mark.anyio
@pytest.mark.parametrize("asked", [[2], [1, 3], [1, 2, 3]], ids=["skipped", "ahead", "after_the_last"])
async def test_a_batch_neither_the_last_sent_nor_the_next_one_is_refused(asked):
    started = await _ready(await _start(rows=[(1,), (2,), (3,)]))

    for n in asked[:-1]:
        await _batch(started.job, n)

    with pytest.raises(JobSequenceError):
        await started.job.get_batch(asked[-1])


@pytest.mark.unit
@pytest.mark.anyio
async def test_a_failed_reading_reports_its_error_and_frees_everything():
    """The first batch is read as soon as the execution is over: its failure is the job's status already"""
    started = await _start(rows=[(1,)], fetch_error=AsyncSqlExecutionError("conversion failed"))
    await asyncio.sleep(0.01)

    status, batch = await _status(started.job), await _batch(started.job, 1)

    assert status == batch
    assert batch[JobKey.STATUS] == JobStatus.ERROR
    assert batch[JobKey.ERROR]["message"] == "SQL error: conversion failed"
    assert started.result.closed and started.freed


@pytest.mark.unit
@pytest.mark.anyio
async def test_a_value_json_can_not_encode_reports_an_unexpected_error():
    started = await _start(rows=[(b"bytes",)])
    await asyncio.sleep(0.01)

    body = await _batch(started.job, 1)

    assert body[JobKey.STATUS] == JobStatus.ERROR
    assert body[JobKey.ERROR]["type"] == "TypeError"


@pytest.mark.unit
@pytest.mark.anyio
async def test_a_batch_is_encoded_outside_the_event_loop(monkeypatch):
    """Encoding thousands of rows would hold up every other request meanwhile"""
    encoding_threads: list[threading.Thread] = []
    encode = Batch.encode

    def recording_encode(batch: Batch) -> bytes:
        encoding_threads.append(threading.current_thread())
        return encode(batch)

    monkeypatch.setattr(Batch, "encode", recording_encode)
    started = await _ready(await _start(rows=[(1,)]))

    assert (await _batch(started.job, 1))[JobKey.ROWS] == [[1]]
    assert encoding_threads and threading.current_thread() not in encoding_threads


@pytest.mark.unit
@pytest.mark.anyio
async def test_reading_the_last_batch_frees_everything_and_logs_the_execution():
    execution_logs = Mock()
    started = await _ready(await _start(rows=[(1,), (2,), (3,)], execution_logs=execution_logs))

    await _batch(started.job, 1)
    assert not started.freed  # still read

    await _batch(started.job, 2)

    assert started.result.closed and started.freed
    [logged] = [call.args[0] for call in execution_logs.log_execution.call_args_list]
    assert (logged.query_id, logged.user_id, logged.nb_rows) == ("Q1", "u1", 3)


# ------------------------------------------------------------------------------
# End of a job
# ------------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.anyio
async def test_closing_during_the_execution_cancels_it_and_frees_everything():
    started = await _start(execute_delay=10)
    await asyncio.sleep(0)

    await started.job.close()

    assert started.context.driver.cancelled
    assert started.freed
    assert (await _status(started.job))[JobKey.STATUS] == JobStatus.CANCELLED


@pytest.mark.unit
@pytest.mark.anyio
async def test_closing_wakes_up_a_waiting_request(monkeypatch):
    monkeypatch.setattr(jobs_module, "JOB_WAIT_SECS", 5)
    started = await _start(execute_delay=10)
    waiting = asyncio.create_task(started.job.wait_status())
    await asyncio.sleep(0.01)

    begin = time.monotonic()
    await started.job.close()
    body = json.loads(await waiting)

    assert body[JobKey.STATUS] == JobStatus.CANCELLED
    assert time.monotonic() - begin < 1


@pytest.mark.unit
@pytest.mark.anyio
async def test_closing_while_reading_frees_everything():
    started = await _ready(await _start(rows=[(1,), (2,), (3,)], fetch_delay=10))

    await started.job.close()

    assert started.result.closed and started.freed


@pytest.mark.unit
@pytest.mark.anyio
async def test_closing_twice_frees_only_once():
    started = await _start(execute_delay=10)

    await started.job.close()
    await started.job.close()

    assert started.semaphore._value == 1  # released once


# ------------------------------------------------------------------------------
# Abandoned jobs
# ------------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.anyio
async def test_a_job_is_abandoned_only_once_nothing_was_asked_for_since_abandon_secs():
    started = await _start()
    now = started.job.last_response_end

    assert not started.job.is_abandoned(now + JOB_ABANDON_SECS - 1)
    assert started.job.is_abandoned(now + JOB_ABANDON_SECS + 1)


@pytest.mark.unit
@pytest.mark.anyio
async def test_a_job_is_never_abandoned_while_a_request_is_running():
    started = await _start()
    started.job.begin_request()

    assert not started.job.is_abandoned(started.job.last_response_end + JOB_ABANDON_SECS + 1)


@pytest.mark.unit
@pytest.mark.anyio
async def test_the_abandon_delay_starts_once_the_response_is_sent():
    started = await _start()
    started.job.begin_request()
    started.job.end_request()

    assert started.job.active_requests == 0
    assert not started.job.is_abandoned(time.monotonic() + JOB_ABANDON_SECS - 1)


# ------------------------------------------------------------------------------
# JobRegistry
# ------------------------------------------------------------------------------


async def _create(registry: JobRegistry, user_id: str = "u1", execute_delay: float = 10) -> _Started:
    result = _FakeResult([(1,)], ["id"])
    context = _FakeDriverContext(_FakeDriver(result, delay=execute_delay))
    semaphore = asyncio.Semaphore(1)
    await semaphore.acquire()

    job = registry.create(_log_entry(user_id), 2, None, context, "SELECT 1", {}, semaphore)
    return _Started(job, semaphore, context, result)


async def _background_closings_done() -> None:
    for _ in range(10):
        await asyncio.sleep(0)


@pytest.mark.unit
@pytest.mark.anyio
async def test_registry_finds_a_job_of_its_user_only():
    registry = JobRegistry()
    started = await _create(registry, user_id="u1")

    assert registry.get(started.job.id, "u1") is started.job
    with pytest.raises(JobNotFound):
        registry.get(started.job.id, "u2")
    with pytest.raises(JobNotFound):
        registry.get("unknown", "u1")

    await registry.close_all()


@pytest.mark.unit
@pytest.mark.anyio
async def test_registry_gives_each_job_an_unpredictable_id():
    registry = JobRegistry()
    first, second = await _create(registry), await _create(registry)

    assert first.job.id != second.job.id
    assert len(first.job.id) >= 32

    await registry.close_all()


@pytest.mark.unit
@pytest.mark.anyio
async def test_registry_cancel_removes_the_job_at_once_and_closes_it_in_the_background():
    registry = JobRegistry()
    started = await _create(registry)

    registry.cancel(started.job.id, "u1")

    assert len(registry) == 0
    await _background_closings_done()
    assert started.freed


@pytest.mark.unit
@pytest.mark.anyio
async def test_registry_cancel_refuses_a_job_of_another_user():
    registry = JobRegistry()
    started = await _create(registry, user_id="u1")

    with pytest.raises(JobNotFound):
        registry.cancel(started.job.id, "u2")

    assert len(registry) == 1
    await registry.close_all()


@pytest.mark.unit
@pytest.mark.anyio
async def test_registry_cleans_up_the_abandoned_jobs_only():
    registry = JobRegistry()
    abandoned, asking = await _create(registry), await _create(registry)
    asking.job.begin_request()

    removed = registry.cleanup_abandoned(now=time.monotonic() + JOB_ABANDON_SECS + 1)

    assert removed == 1
    with pytest.raises(JobNotFound):
        registry.get(abandoned.job.id, "u1")
    assert registry.get(asking.job.id, "u1") is asking.job
    await _background_closings_done()
    assert abandoned.freed

    await registry.close_all()


@pytest.mark.unit
@pytest.mark.anyio
async def test_registry_close_all_closes_every_job():
    registry = JobRegistry()
    jobs = [await _create(registry), await _create(registry)]

    await registry.close_all()

    assert len(registry) == 0
    assert all(started.freed for started in jobs)


@pytest.mark.unit
@pytest.mark.anyio
async def test_registry_close_all_waits_close_all_secs_at_most(monkeypatch):
    """A query that can't be interrupted must not hold the server's shutdown"""
    monkeypatch.setattr(jobs_module, "CLOSE_ALL_SECS", 0.05)
    registry = JobRegistry()
    started = await _create(registry)

    async def never_closing() -> None:
        await asyncio.sleep(10)

    monkeypatch.setattr(started.job, "_close_now", never_closing)

    begin = time.monotonic()
    await registry.close_all()

    assert time.monotonic() - begin < 1


@pytest.mark.unit
@pytest.mark.anyio
async def test_registry_periodic_cleanup_runs_until_cancelled(monkeypatch):
    monkeypatch.setattr(jobs_module, "CLEANUP_INTERVAL_SECS", 0.01)
    registry = JobRegistry()
    passes = []
    monkeypatch.setattr(registry, "cleanup_abandoned", lambda: passes.append(1) or 0)

    task = asyncio.create_task(registry.periodic_cleanup())
    await asyncio.sleep(0.05)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    assert len(passes) >= 2
