"""Routes of the executions by jobs, see Pytre_Server/jobs.py: a call starts the execution, the client then asks
for its status and for its batches, no response lasting more than about JOB_WAIT_SECS."""

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Response, status
from starlette.types import Receive, Scope, Send

from Pytre_Core.logs_central import ExecutionLogEntry
from Pytre_Core.remote_protocol import JobKey, JobStatus
from Pytre_Core.utils import log as _log

from Pytre_Server.jobs import Job, JobNotFound, JobSequenceError
from Pytre_Server.routes.client_auth import require_access_token
from Pytre_Server.routes.client_execution import ExecuteRequest, prepare_execution
from Pytre_Server.server_state import ServerState, get_server_state


def log(msg: str | object, level: int = logging.INFO, sublogger: str = __name__) -> None:
    _log(msg, level=level, sublogger=sublogger)


router = APIRouter(prefix="/jobs")


class _JobResponse(Response):
    """JSON response telling its job once it's fully sent, or its sending failed, see Job.is_abandoned()"""

    media_type = "application/json"

    def __init__(self, job: Job, content: bytes, status_code: int = status.HTTP_200_OK) -> None:
        super().__init__(content=content, status_code=status_code)
        self._job: Job = job

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:  # even if the client went away meanwhile
            self._job.end_request()


async def _answer(job: Job, build_content: Callable[[], Awaitable[bytes]]) -> _JobResponse:
    """Response of a request on `job`, the job knowing a request is running until it's sent"""
    job.begin_request()
    try:
        content = await build_content()
    except BaseException:
        job.end_request()  # no response will be sent
        raise
    return _JobResponse(job, content)


def _get_job(state: ServerState, job_id: str, username: str) -> Job:
    """Raises HTTPException(404) for an unknown job or a job of another user"""
    try:
        return state.resources.jobs.get(job_id, username)
    except JobNotFound as e:
        detail = "Unknown job: the server restarted, or the job was deleted or abandoned"
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail) from e


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_job(
    request: ExecuteRequest,
    username: str = Depends(require_access_token),
    state: ServerState = Depends(get_server_state),
) -> _JobResponse:
    """Starts executing a query using infos sent by the client: a query_id, template hash (to check for staleness) and
    parameter values. Authorization is re-validated (query exists, is associated with server_id, user allowed).
    Answers at once with the job's id."""

    prepared = await prepare_execution(request, username, state)
    resources = state.resources

    log_entry = ExecutionLogEntry(
        server_id=request.server_id,
        user_id=username,
        user_display_name=prepared.user_ctx.user.display_name,
        query_id=request.query_id,
        start=datetime.now(timezone.utc),
        parameters=request.display_parameters,
    )

    try:
        server = state.app_ctx.servers.servers_dict[request.server_id]
        job = resources.jobs.create(
            log_entry,
            state.batch_size,
            resources.execution_logs,
            resources.use_driver(server),
            prepared.template,
            prepared.params,
            prepared.semaphore,
        )
    except BaseException:
        prepared.semaphore.release()  # the job didn't take it over
        raise

    log(f"Job {job.id} started for '{username}': '{request.query_id}' on '{request.server_id}'", logging.INFO)

    job.begin_request()
    content = job.json_response(JobStatus.RUNNING, extra={JobKey.JOB_ID: job.id})
    return _JobResponse(job, content, status_code=status.HTTP_201_CREATED)


@router.get("/{job_id}")
async def get_job_status(
    job_id: str,
    username: str = Depends(require_access_token),
    state: ServerState = Depends(get_server_state),
) -> _JobResponse:
    """Status of the job, once its execution is over or at most JOB_WAIT_SECS later"""
    job = _get_job(state, job_id, username)
    return await _answer(job, job.wait_status)


@router.get("/{job_id}/batches/{n}")
async def get_job_batch(
    job_id: str,
    n: Annotated[int, Path(ge=1)],
    username: str = Depends(require_access_token),
    state: ServerState = Depends(get_server_state),
) -> _JobResponse:
    """Batch `n` of the result, asking for it acknowledging batch n - 1, see Job.get_batch().
    409 if the execution isn't over, or if `n` is neither the last batch sent nor the next one."""
    job = _get_job(state, job_id, username)
    try:
        return await _answer(job, lambda: job.get_batch(n))
    except JobSequenceError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_job(
    job_id: str,
    username: str = Depends(require_access_token),
    state: ServerState = Depends(get_server_state),
) -> Response:
    """Stops the job, or acknowledges its last batch: what it holds is freed in the background"""
    job = _get_job(state, job_id, username)
    if job.status is JobStatus.RUNNING:
        log(f"Job {job_id} stopped by '{username}' while executing", logging.INFO)
    state.resources.jobs.cancel(job_id, username)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
