"""
Integration tests for the /jobs routes (Pytre_Server/routes/client_jobs.py): a job is created once its request is
checked (Pytre_Server/routes/client_execution.py), then its status and batches are asked for. The SQL driver is
faked except in the test marked `requires_docker`, which reads a real result.

The TestClient is used as a context manager: its event loop then lives for the whole test, as the server's does,
so a job created by a request goes on running between two requests.
"""

import asyncio
import hashlib
import json
import time

import pytest
from fastapi.testclient import TestClient

import Pytre_Core.kee as kee_module
from Pytre_Core.sql_query.query_drivers_server import AsyncSqlConnectionError, PyMssqlDriver
from Pytre_Server import jobs as jobs_module
from Pytre_Server.app import create_app
from tests.integration.conftest import (
    TEST_DB_PASSWORD,
    TEST_DB_PATH,
    TEST_DISPLAY_FORMAT,
    TEST_QUERY_ID,
    TEST_SERVER_ID,
    TEST_USERNAME,
    requires_docker,
)
from tests.resources.build_test_keepass import TEST_USER_PASSWORD

pytestmark = pytest.mark.integration

BATCH_SIZE = 1  # every result is read in several batches, a single row one too: its last batch is empty


@pytest.fixture
def jobs_client(monkeypatch):
    monkeypatch.setattr(kee_module, "KEE_FILE", TEST_DB_PATH)
    monkeypatch.setattr(jobs_module, "JOB_WAIT_SECS", 0.2)
    with TestClient(create_app(master_key=TEST_DB_PASSWORD, batch_size=BATCH_SIZE)) as client:
        yield client


@pytest.fixture
def headers(jobs_client) -> dict[str, str]:
    response = jobs_client.post("/login", json={"username": TEST_USERNAME, "password": TEST_USER_PASSWORD})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


class _FakeResult:
    def __init__(self, rows: list[tuple]):
        self._rows = rows
        self.closed = False

    def columns(self) -> list[str]:
        return ["id", "label"]

    async def fetch(self, n: int) -> list[tuple]:
        batch, self._rows = self._rows[:n], self._rows[n:]
        return batch

    async def close(self) -> None:
        self.closed = True


class _FakeExecution:
    """What every execution on the mssql test server gives: `rows`, after `delay` seconds. Keeps the SQL parameters
    of the last execution."""

    def __init__(self) -> None:
        self.rows: list[tuple] = [(1, "a"), (2, "b"), (3, "c")]
        self.delay: float = 0
        self.params: dict | None = None


@pytest.fixture
def fake_execution(monkeypatch) -> _FakeExecution:
    execution = _FakeExecution()

    async def fake_execute(_driver, _template, params):
        execution.params = params
        await asyncio.sleep(execution.delay)
        return _FakeResult(list(execution.rows))

    monkeypatch.setattr(PyMssqlDriver, "execute", fake_execute)
    return execution


def _request_body(client: TestClient, headers: dict, **changes) -> dict:
    params = {"server_id": TEST_SERVER_ID, "display_format": json.dumps(TEST_DISPLAY_FORMAT)}
    queries = {q["name"]: q for q in client.get("/queries", params=params, headers=headers).json()}
    template = queries[TEST_QUERY_ID]["cmd_template"]

    body = {
        "server_id": TEST_SERVER_ID,
        "query_id": TEST_QUERY_ID,
        "template_hash": hashlib.sha256(template.encode("utf-8")).hexdigest(),
        "display_format": TEST_DISPLAY_FORMAT,
        "display_parameters": {"@1": ""},
    }
    return body | changes


def _create(client: TestClient, headers: dict) -> str:
    response = client.post("/jobs", json=_request_body(client, headers), headers=headers)
    assert response.status_code == 201
    return response.json()["job_id"]


def _wait_ready(client: TestClient, headers: dict, job_id: str) -> dict:
    for _ in range(50):
        body = client.get(f"/jobs/{job_id}", headers=headers).json()
        if body["status"] != "running":
            return body
    pytest.fail("the job never ended")


def _read_all(client: TestClient, headers: dict, job_id: str) -> tuple[list, list]:
    columns, rows, n = [], [], 1
    while True:
        body = client.get(f"/jobs/{job_id}/batches/{n}", headers=headers).json()
        if body["status"] == "pending":
            continue
        assert body["status"] == "ok", body
        columns = body.get("columns", columns)
        rows += body["rows"]
        if body["last"]:
            return columns, rows
        n += 1


def _slots_all_free(client: TestClient) -> bool:
    state = client.app.state.server  # type: ignore  # the app built by create_app()
    semaphore = state.resources.user_semaphores[TEST_USERNAME]
    for _ in range(50):
        if semaphore._value == state.max_concurrency_per_user:
            return True
        time.sleep(0.02)  # closed in the background
    return False


# ------------------------------------------------------------------------------
# A job from its creation to its last batch
# ------------------------------------------------------------------------------


def test_a_job_runs_then_its_result_is_read_by_batches(jobs_client, headers, fake_execution):
    response = jobs_client.post("/jobs", json=_request_body(jobs_client, headers), headers=headers)

    assert response.status_code == 201
    assert response.json()["status"] == "running"
    job_id = response.json()["job_id"]

    assert _wait_ready(jobs_client, headers, job_id) == {"status": "ready"}
    columns, rows = _read_all(jobs_client, headers, job_id)

    assert columns == ["id", "label"]
    assert rows == [list(row) for row in fake_execution.rows]
    assert jobs_client.delete(f"/jobs/{job_id}", headers=headers).status_code == 204
    assert jobs_client.get(f"/jobs/{job_id}", headers=headers).status_code == 404
    assert _slots_all_free(jobs_client)


def test_a_job_still_executing_is_running(jobs_client, headers, fake_execution):
    fake_execution.delay = 5
    job_id = _create(jobs_client, headers)

    assert jobs_client.get(f"/jobs/{job_id}", headers=headers).json() == {"status": "running"}

    jobs_client.delete(f"/jobs/{job_id}", headers=headers)


def test_a_job_stopped_while_executing_frees_the_users_slot(jobs_client, headers, fake_execution):
    fake_execution.delay = 5
    job_id = _create(jobs_client, headers)

    assert jobs_client.delete(f"/jobs/{job_id}", headers=headers).status_code == 204
    assert _slots_all_free(jobs_client)


def test_a_job_failing_to_connect_reports_the_server_unavailable(jobs_client, headers, monkeypatch):
    async def _raise_unreachable(self):
        raise AsyncSqlConnectionError("Failed to connect: connection refused.")

    monkeypatch.setattr(PyMssqlDriver, "connect", _raise_unreachable)
    job_id = _create(jobs_client, headers)

    body = _wait_ready(jobs_client, headers, job_id)

    assert body["status"] == "error"
    assert body["error"] == {
        "type": "AsyncSqlConnectionError",
        "message": "Server unavailable: Failed to connect: connection refused.",
    }
    assert _slots_all_free(jobs_client)


def test_a_batch_before_the_end_of_the_execution_is_refused(jobs_client, headers, fake_execution):
    fake_execution.delay = 5
    job_id = _create(jobs_client, headers)

    assert jobs_client.get(f"/jobs/{job_id}/batches/1", headers=headers).status_code == 409

    jobs_client.delete(f"/jobs/{job_id}", headers=headers)


def test_a_batch_out_of_sequence_is_refused(jobs_client, headers, fake_execution):
    job_id = _create(jobs_client, headers)
    _wait_ready(jobs_client, headers, job_id)

    assert jobs_client.get(f"/jobs/{job_id}/batches/2", headers=headers).status_code == 409


def test_a_batch_number_below_1_is_invalid(jobs_client, headers, fake_execution):
    job_id = _create(jobs_client, headers)

    assert jobs_client.get(f"/jobs/{job_id}/batches/0", headers=headers).status_code == 422


# ------------------------------------------------------------------------------
# Refused requests
# ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method, path", [("post", "/jobs"), ("get", "/jobs/x"), ("get", "/jobs/x/batches/1"), ("delete", "/jobs/x")]
)
def test_jobs_require_an_access_token(jobs_client, method, path):
    assert getattr(jobs_client, method)(path).status_code == 401


@pytest.mark.parametrize("path", ["/jobs/unknown", "/jobs/unknown/batches/1"])
def test_an_unknown_job_is_not_found(jobs_client, headers, path):
    assert jobs_client.get(path, headers=headers).status_code == 404


def test_deleting_an_unknown_job_is_not_found(jobs_client, headers):
    assert jobs_client.delete("/jobs/unknown", headers=headers).status_code == 404


def test_a_job_of_another_user_is_not_found(jobs_client, headers, fake_execution):
    job_id = _create(jobs_client, headers)
    jobs_client.app.state.server.resources.jobs.get(job_id, TEST_USERNAME).username = "someone_else"

    assert jobs_client.get(f"/jobs/{job_id}", headers=headers).status_code == 404
    assert jobs_client.delete(f"/jobs/{job_id}", headers=headers).status_code == 404


@pytest.mark.parametrize(
    "changes, expected",
    [
        ({"query_id": "UNKNOWN_QUERY"}, 404),
        ({"server_id": "UNKNOWN_SERVER"}, 404),
        ({"template_hash": "stale_hash_value"}, 409),
        ({"display_parameters": {"@unknown": "x"}}, 409),
        ({"display_parameters": {"@1": "not-a-date"}}, 400),
    ],
    ids=["unknown_query", "unknown_server", "stale_hash", "parameters_mismatch", "invalid_value"],
)
def test_a_job_is_refused_for_an_invalid_request(jobs_client, headers, changes, expected):
    body = _request_body(jobs_client, headers, **changes)

    assert jobs_client.post("/jobs", json=body, headers=headers).status_code == expected
    assert len(jobs_client.app.state.server.resources.jobs) == 0


def test_a_job_is_refused_when_the_user_is_at_the_concurrency_limit(jobs_client, headers):
    body = _request_body(jobs_client, headers)
    jobs_client.app.state.server.resources.user_semaphores[TEST_USERNAME] = asyncio.Semaphore(0)  # already exhausted

    assert jobs_client.post("/jobs", json=body, headers=headers).status_code == 429


@pytest.mark.parametrize("display_format", [None, {"date_format": "%d/%m", "decimal_separator": ","}])
def test_a_job_is_refused_for_a_missing_or_invalid_display_format(jobs_client, headers, display_format):
    body = _request_body(jobs_client, headers)
    if display_format is None:
        del body["display_format"]
    else:
        body["display_format"] = display_format

    assert jobs_client.post("/jobs", json=body, headers=headers).status_code == 422


@pytest.mark.parametrize(
    "date_format, display_value", [("%d/%m/%Y", "10/03/2025"), ("%m/%d/%Y", "03/10/2025"), ("%Y-%m-%d", "2025-03-10")]
)
def test_a_job_reads_the_parameters_with_the_client_display_format(
    jobs_client, headers, fake_execution, date_format, display_value
):
    """The same date typed in different formats reaches the database as the same value"""
    display_format = {"date_format": date_format, "decimal_separator": ","}
    body = _request_body(jobs_client, headers, display_format=display_format, display_parameters={"@1": display_value})

    job_id = jobs_client.post("/jobs", json=body, headers=headers).json()["job_id"]
    _wait_ready(jobs_client, headers, job_id)

    assert fake_execution.params == {"@1": "2025-03-10"}


def test_a_job_builds_only_the_requested_query(jobs_client, headers, fake_execution, monkeypatch):
    """Building every query of the catalog would re-log the errors of every unrelated broken one, on each job"""
    from Pytre_Server.routes import client_execution

    original = client_execution.build_queries_from_parsers
    parser_counts = []

    def spy(app_ctx, user_ctx, parsers):
        parser_counts.append(len(parsers))
        return original(app_ctx, user_ctx, parsers)

    monkeypatch.setattr(client_execution, "build_queries_from_parsers", spy)

    jobs_client.post("/jobs", json=_request_body(jobs_client, headers), headers=headers)

    assert parser_counts == [1]


def _exempt_from_the_hash_check(client: TestClient, days: int) -> None:
    from datetime import datetime, timedelta, timezone

    until = datetime.now(timezone.utc) + timedelta(days=days)
    client.app.state.server.resources.ignore_hash_until[TEST_QUERY_ID] = until  # type: ignore  # create_app()'s


def test_a_job_with_a_stale_hash_is_accepted_while_its_query_is_exempted(jobs_client, headers, fake_execution):
    _exempt_from_the_hash_check(jobs_client, days=1)
    body = _request_body(jobs_client, headers, template_hash="stale_hash_value")

    assert jobs_client.post("/jobs", json=body, headers=headers).status_code == 201


def test_a_job_still_checks_the_parameters_while_its_query_is_exempted(jobs_client, headers):
    """The exemption only covers the SQL body: the template hash never covers the parameters"""
    _exempt_from_the_hash_check(jobs_client, days=1)
    body = _request_body(jobs_client, headers, template_hash="stale_hash_value", display_parameters={"@unknown": "x"})

    assert jobs_client.post("/jobs", json=body, headers=headers).status_code == 409


def test_a_job_with_a_stale_hash_is_refused_once_the_exemption_expired(jobs_client, headers):
    _exempt_from_the_hash_check(jobs_client, days=-1)
    body = _request_body(jobs_client, headers, template_hash="stale_hash_value")

    assert jobs_client.post("/jobs", json=body, headers=headers).status_code == 409


# ------------------------------------------------------------------------------
# Real execution
# ------------------------------------------------------------------------------


@requires_docker
def test_a_job_reads_a_real_result(jobs_client, headers):
    job_id = _create(jobs_client, headers)

    assert _wait_ready(jobs_client, headers, job_id) == {"status": "ready"}
    columns, rows = _read_all(jobs_client, headers, job_id)
    jobs_client.delete(f"/jobs/{job_id}", headers=headers)

    assert columns == ["id", "label", "filter_date"]
    assert rows and all(len(row) == len(columns) for row in rows)
