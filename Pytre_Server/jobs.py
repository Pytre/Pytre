"""Executions of the client's queries by jobs: a call starts the execution, the client waits for its end with short
calls, then reads the result by numbered batches. No response lasts more than about JOB_WAIT_SECS: a network device
holding a response until it's complete, or cutting a connection without traffic, can't break a long execution."""

import asyncio
import json
import logging
import secrets
import time
import traceback
from contextlib import AbstractAsyncContextManager, AsyncExitStack
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from Pytre_Core.logs_central import ExecutionLogEntry, ServerDriven
from Pytre_Core.remote_protocol import JOB_ABANDON_SECS, JOB_WAIT_SECS, Batch, JobKey, JobStatus, error_payload
from Pytre_Core.utils import log as _log
from Pytre_Core.sql_query.query_drivers_server import (
    AsyncSqlConnectionError,
    AsyncSqlDriver,
    AsyncSqlExecutionError,
    AsyncSqlResult,
)


CLEANUP_INTERVAL_SECS = 10  # how often abandoned jobs are looked for
CLOSE_ALL_SECS = 10  # at shutdown, longest wait for the jobs to give their connections back


def log(msg: str | object, level: int = logging.INFO, sublogger: str = __name__) -> None:
    _log(msg, level=level, sublogger=sublogger)


class JobNotFound(LookupError):
    """Unknown job, or a job of another user"""


class JobSequenceError(Exception):
    """A batch asked before the end of the execution, or neither the last one sent nor the next one"""


class JobRegistry:
    """The jobs in progress, by id. A job deleted by its user or abandoned is removed at once and closed in the
    background: nobody waits for a running query to give its connection back."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._closing: set[asyncio.Task] = set()  # ref to avoid garbage collection

    def __len__(self) -> int:
        return len(self._jobs)

    def create(
        self,
        log_entry: ExecutionLogEntry,
        batch_size: int,
        execution_logs: ServerDriven | None,
        driver: AbstractAsyncContextManager[AsyncSqlDriver],
        template: str,
        params: dict[str, Any],
        semaphore: asyncio.Semaphore,
    ) -> Job:
        """Creates and starts a job, see Job.start()"""
        job = Job(log_entry, batch_size, execution_logs)
        job.start(driver, template, params, semaphore)
        self._jobs[job.id] = job
        return job

    def get(self, job_id: str, username: str) -> Job:
        """Raises JobNotFound if `job_id` is unknown or isn't one of `username`'s jobs"""
        job = self._jobs.get(job_id)
        if job is None or job.username != username:
            raise JobNotFound(job_id)
        return job

    def cancel(self, job_id: str, username: str) -> None:
        """Removes and closes the job: stopped, or fully read. Raises JobNotFound like get()."""
        self._remove(self.get(job_id, username))

    async def periodic_cleanup(self) -> None:
        """Clean up the abandoned jobs every CLEANUP_INTERVAL_SECS, until cancelled"""
        while True:
            await asyncio.sleep(CLEANUP_INTERVAL_SECS)
            self.cleanup_abandoned()

    def cleanup_abandoned(self, now: float | None = None) -> int:
        """Removes and closes the jobs nothing was asked for since JOB_ABANDON_SECS. Returns how many."""
        now = time.monotonic() if now is None else now
        abandoned = [job for job in self._jobs.values() if job.is_abandoned(now)]
        for job in abandoned:
            log(f"Job {job.id} of '{job.username}' abandoned, nothing asked for {JOB_ABANDON_SECS} s", logging.WARNING)
            self._remove(job)
        return len(abandoned)

    async def close_all(self) -> None:
        """At shutdown: closes every job, waiting at most CLOSE_ALL_SECS for them to give their connections back"""
        jobs = list(self._jobs.values())
        self._jobs.clear()
        closing = {asyncio.ensure_future(job.close()) for job in jobs} | self._closing
        if not closing:
            return

        _done, pending = await asyncio.wait(closing, timeout=CLOSE_ALL_SECS)
        if pending:
            log(f"{len(pending)} job(s) still closing at shutdown, a query may still be running", logging.WARNING)

    def _remove(self, job: Job) -> None:
        del self._jobs[job.id]
        task = asyncio.create_task(job.close())
        self._closing.add(task)
        task.add_done_callback(self._closing.discard)


@dataclass(frozen=True)
class _EncodedBatch:
    """A batch ready to be sent, kept as is to be sent again if asked again"""

    n: int
    body: bytes
    last: bool

    def is_asked_again_by(self, n: int) -> bool:
        """Whether asking for batch `n` asks for this one again, its response lost on its way to the client"""
        return n == self.n and self.n > 0

    def is_followed_by(self, n: int) -> bool:
        """Whether batch `n` is the one to send after this one"""
        return not self.last and n == self.n + 1


class Job:
    """A query executed for a user, then read by batches of `batch_size` rows, each one only once the client asked
    for the previous one. Holds its driver and the user's execution slot until its result is fully read, it fails or
    it's closed. Keeps at most two batches: the last one sent, sent again if asked again after a lost response, and
    the next one, prepared while the client handles the current one."""

    def __init__(self, log_entry: ExecutionLogEntry, batch_size: int, execution_logs: ServerDriven | None) -> None:
        self.id: str = secrets.token_urlsafe(nbytes=24)
        self.username: str = log_entry.user_id
        self.log_entry: ExecutionLogEntry = log_entry
        self.batch_size: int = batch_size
        self.execution_logs: ServerDriven | None = execution_logs
        self.nb_rows: int = 0

        self._to_release: AsyncExitStack[bool | None] = AsyncExitStack()  # driver used and user semaphore
        self._execution: asyncio.Task[None] | None = None
        self._result: AsyncSqlResult | None = None
        self._error: dict[str, str] | None = None

        self._latest_sent: _EncodedBatch = _EncodedBatch(n=0, body=b"", last=False)  # batch 0 before the first one
        self._next_preparation: asyncio.Task[_EncodedBatch | None] | None = None  # preparation of next batch

        self._lock = asyncio.Lock()  # one batch request at a time, e.g. a retry while the first one still waits
        self._cancelled = asyncio.Event()  # wakes up the waiting requests, see close()
        self._releasing: asyncio.Task[None] | None = None  # the release, done once whoever asks first, see _release()
        self._closing: asyncio.Task[None] | None = None  # the closing, done once whoever asks first, see close()

        self.active_requests: int = 0
        self.last_response_end: float = time.monotonic()

    @property
    def status(self) -> JobStatus:
        if self._cancelled.is_set():
            return JobStatus.CANCELLED
        if self._error is not None:
            return JobStatus.ERROR
        if self._execution is None or not self._execution.done():
            return JobStatus.RUNNING
        return JobStatus.READY

    @staticmethod
    def json_response(
        status: JobStatus, error: dict[str, str] | None = None, extra: dict[str, Any] | None = None
    ) -> bytes:
        """JSON response of /jobs that carries no batch: a status, an error, or the job's id at its creation.
        A response carrying a batch is Batch.encode()'s."""
        body: dict[str, Any] = {JobKey.STATUS: status} | (extra or {})
        if error is not None:
            body[JobKey.ERROR] = error
        return json.dumps(body, separators=(",", ":")).encode()

    # ------------------------------------------------------------------------------------------
    # execution
    # ------------------------------------------------------------------------------------------
    def start(
        self,
        driver: AbstractAsyncContextManager[AsyncSqlDriver],
        template: str,
        params: dict[str, Any],
        semaphore: asyncio.Semaphore,
    ) -> None:
        """Starts the execution on `driver`, ServerResources.use_driver() entered by the job so a reload of the
        servers doesn't disconnect it meanwhile. Takes over `semaphore`, already acquired: released once the job
        holds nothing anymore."""
        self._to_release.callback(semaphore.release)
        self._execution = asyncio.create_task(self._execute(driver, template, params))

    async def _execute(
        self, driver_context: AbstractAsyncContextManager[AsyncSqlDriver], template: str, params: dict[str, Any]
    ) -> None:
        try:
            driver = await self._to_release.enter_async_context(driver_context)
            self._result = await driver.execute(template, params)
        except Exception as e:
            self._fail(e, step="execution")
            await self._release()
            return

        self._next_preparation = asyncio.create_task(self._prepare(1))  # prepare first batch

    async def wait_status(self) -> bytes:
        """Response telling the job's status, once the execution is over or at most JOB_WAIT_SECS later"""
        if self._execution is not None:
            await self._wait_for(self._execution)
        return self.json_response(self.status, self._error)

    # ------------------------------------------------------------------------------------------
    # batches
    # ------------------------------------------------------------------------------------------
    async def get_batch(self, n: int) -> bytes:
        """Response carrying batch `n`: asking for it acknowledges batch n - 1, which is let go, and the preparation
        of batch n + 1 starts. The last batch sent is sent again if asked again. Answers JobStatus.PENDING if batch
        `n` isn't ready within JOB_WAIT_SECS, its preparation goes on. Raises JobSequenceError for another `n`, or
        before the end of the execution."""
        async with self._lock:
            # the job must be ready: its execution over, neither failed nor cancelled
            if self.status is JobStatus.RUNNING:
                raise JobSequenceError("The execution isn't over")

            if self.status is not JobStatus.READY:
                return self.json_response(self.status, self._error)

            # if asked again return the batch sent most recently: its response was lost
            if self._latest_sent.is_asked_again_by(n):
                return self._latest_sent.body

            # otherwise only the next batch can be asked for
            preparation = self._next_preparation

            if not self._latest_sent.is_followed_by(n) or preparation is None:
                raise JobSequenceError(f"Batch {n} can't be asked for after batch {self._latest_sent.n}")

            # wait for preparation: PENDING if not ready within JOB_WAIT_SECS, it goes on meanwhile
            if not await self._wait_for(preparation):
                job_status = JobStatus.PENDING if self.status is JobStatus.READY else self.status
                return self.json_response(job_status, self._error)

            if self.status is not JobStatus.READY:  # cancelled meanwhile, or the reading failed
                return self.json_response(self.status, self._error)

            prepared = preparation.result()
            if prepared is None:  # the reading failed, see _prepare(): its error is the job's
                return self.json_response(JobStatus.ERROR, self._error)

            # once ready, keep the batch to send it again if asked again, and start preparing the next one
            self._latest_sent = prepared
            self._next_preparation = None if prepared.last else asyncio.create_task(self._prepare(n + 1))

            return prepared.body

    async def _prepare(self, n: int) -> _EncodedBatch | None:
        """Reads and encodes batch `n`. After the last one, logs the execution and frees what the job holds.
        On a failure, sets the job's error and returns None."""
        assert self._result is not None
        try:
            rows = await self._result.fetch(self.batch_size)
            last = len(rows) < self.batch_size  # a full batch may be followed by an empty last one
            columns = self._result.columns() if n == 1 else None
            body = await asyncio.to_thread(Batch(n, rows, last, columns).encode)  # JSON of thousands of rows
        except Exception as e:
            self._fail(e, step=f"batch {n}")
            await self._release()
            return None

        self.nb_rows += len(rows)
        if last:
            self._log_execution()  # before an await: a job closed meanwhile still logs a fully read result
            await self._release()

        return _EncodedBatch(n=n, body=body, last=last)

    # ------------------------------------------------------------------------------------------
    # requests, to detect an abandoned job
    # ------------------------------------------------------------------------------------------
    def begin_request(self) -> None:
        self.active_requests += 1

    def end_request(self) -> None:
        """Once the response is sent, or the request failed"""
        self.active_requests -= 1
        self.last_response_end = time.monotonic()

    def is_abandoned(self, now: float | None = None) -> bool:
        """Whether nothing was asked for since JOB_ABANDON_SECS, no request running: `now` from time.monotonic()"""
        now = time.monotonic() if now is None else now
        return self.active_requests == 0 and now - self.last_response_end > JOB_ABANDON_SECS

    # ------------------------------------------------------------------------------------------
    # end of the job
    # ------------------------------------------------------------------------------------------
    async def close(self) -> None:
        """Stops the job: cancels what's running, then frees what it holds. Idempotent, never raises."""
        self._cancelled.set()
        if self._closing is None:
            self._closing = asyncio.create_task(self._close_now())
        await asyncio.shield(self._closing)  # a cancelled caller doesn't stop the closing

    async def _close_now(self) -> None:
        running = [task for task in (self._execution, self._next_preparation) if task is not None]
        for task in running:
            task.cancel()  # each driver closes its result on a cancellation, see query_drivers_server
        await asyncio.gather(*running, return_exceptions=True)
        await self._release()

    async def _release(self) -> None:
        """Closes the result, stops using the driver and frees the user's slot: once, whoever asks first"""
        if self._releasing is None:
            self._releasing = asyncio.create_task(self._release_now())
        await asyncio.shield(self._releasing)

    async def _release_now(self) -> None:
        try:
            if self._result is not None:
                await self._result.close()
        except Exception as e:
            log(f"Closing the result of job {self.id} failed: {type(e).__name__}: {e}", logging.WARNING)
        finally:
            await self._to_release.aclose()

    async def _wait_for(self, task: asyncio.Task) -> bool:
        """Waits for `task` at most JOB_WAIT_SECS, less if the job gets cancelled. Returns whether `task` is done."""
        cancelled = asyncio.create_task(self._cancelled.wait())
        try:
            await asyncio.wait({task, cancelled}, timeout=JOB_WAIT_SECS, return_when=asyncio.FIRST_COMPLETED)
        finally:
            cancelled.cancel()
        return task.done()

    def _fail(self, error: Exception, step: str) -> None:
        """Sets the job's error, its message prefixed, and logs it"""

        details = ""

        if isinstance(error, AsyncSqlConnectionError):
            prefix, level = "Server unavailable: ", logging.WARNING
        elif isinstance(error, AsyncSqlExecutionError):
            prefix, level = "SQL error: ", logging.ERROR
        else:  # a bug, its traceback logged
            prefix, level = "Unexpected error: ", logging.ERROR
            details = f"\n{traceback.format_exc()}"

        entry = self.log_entry
        msg = f"{prefix}job {self.id} of '{self.username}', query '{entry.query_id}' on '{entry.server_id}', {step}"
        log(f"{msg}: {type(error).__name__}: {error}{details}", level)

        self._error = error_payload(error, prefix)

    def _log_execution(self) -> None:
        if self.execution_logs is None:
            return
        now = datetime.now(self.log_entry.start.tzinfo)
        duration_secs = int((now - self.log_entry.start).total_seconds())
        self.execution_logs.log_execution(replace(self.log_entry, duration_secs=duration_secs, nb_rows=self.nb_rows))
