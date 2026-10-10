"""
Integration test for the POST /execute route (real query_id + hash + parameters,
never raw SQL). Uses the shared `client` fixture (dedicated test KeePass database) for every
test - only the two tests doing a real SQL execution also need the local mssql-test docker
container (marked with `requires_docker`).
"""

import json

import pytest

from tests.integration.conftest import (
    TEST_DISPLAY_FORMAT,
    TEST_QUERY_ID,
    TEST_SERVER_ID,
    TEST_USERNAME,
    requires_docker,
)

pytestmark = pytest.mark.integration


def _get_real_hash(client, auth_headers, server_id: str, query_id: str) -> str:
    params = {"server_id": server_id, "display_format": json.dumps(TEST_DISPLAY_FORMAT)}
    response = client.get("/queries", params=params, headers=auth_headers)
    queries = {q["name"]: q for q in response.json()}
    return queries[query_id]["cmd_template"]


@requires_docker
def test_execute_returns_rows_with_correct_hash(client, auth_headers):
    import hashlib

    template = _get_real_hash(client, auth_headers, TEST_SERVER_ID, TEST_QUERY_ID)
    template_hash = hashlib.sha256(template.encode("utf-8")).hexdigest()

    response = client.post(
        "/execute",
        json={
            "server_id": TEST_SERVER_ID,
            "query_id": TEST_QUERY_ID,
            "template_hash": template_hash,
            "display_format": TEST_DISPLAY_FORMAT,
            "display_parameters": {"@1": ""},
        },
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/x-ndjson"
    lines = [json.loads(line) for line in response.text.splitlines() if line]

    # first line is always a {"__columns__": [...]} header, data lines are {"__row__": [...]}
    # positional lists (see Pytre_Server/result_streaming.py)
    columns = lines[0]["__columns__"]
    rows = [dict(zip(columns, line["__row__"])) for line in lines[1:]]

    assert set(columns) == {"id", "label", "filter_date"}
    assert len(rows) > 0
    assert set(rows[0].keys()) == {"id", "label", "filter_date"}


def test_execute_only_builds_the_requested_query(client, auth_headers, monkeypatch):
    """Regression: /execute used to build every query in the catalog (and re-log errors for
    every unrelated broken one) on every call, just to use one of them - it must only build
    the requested query_id."""
    from Pytre_Server.routes import client as client_routes

    original = client_routes.build_queries_from_parsers
    captured_parser_counts = []

    def spy(app_ctx, user_ctx, parsers):
        captured_parser_counts.append(len(parsers))
        return original(app_ctx, user_ctx, parsers)

    monkeypatch.setattr(client_routes, "build_queries_from_parsers", spy)

    client.post(
        "/execute",
        json={
            "server_id": TEST_SERVER_ID,
            "query_id": TEST_QUERY_ID,
            "template_hash": "x",
            "display_format": TEST_DISPLAY_FORMAT,
            "display_parameters": {"@1": ""},
        },
        headers=auth_headers,
    )

    assert captured_parser_counts == [1]


def test_execute_rejects_unknown_query_id(client, auth_headers):
    response = client.post(
        "/execute",
        json={
            "server_id": TEST_SERVER_ID,
            "query_id": "DOES_NOT_EXIST",
            "template_hash": "x",
            "display_format": TEST_DISPLAY_FORMAT,
        },
        headers=auth_headers,
    )

    assert response.status_code == 404


def test_execute_rejects_unknown_server_id(client, auth_headers):
    response = client.post(
        "/execute",
        json={
            "server_id": "nope_server",
            "query_id": TEST_QUERY_ID,
            "template_hash": "x",
            "display_format": TEST_DISPLAY_FORMAT,
        },
        headers=auth_headers,
    )

    assert response.status_code == 404


def test_execute_rejects_stale_hash(client, auth_headers):
    response = client.post(
        "/execute",
        json={
            "server_id": TEST_SERVER_ID,
            "query_id": TEST_QUERY_ID,
            "template_hash": "stale_hash_value",
            "display_format": TEST_DISPLAY_FORMAT,
        },
        headers=auth_headers,
    )

    assert response.status_code == 409


def test_execute_rejects_parameter_mismatch(client, auth_headers):
    """An unknown/missing parameter is caught by the exact parameter-set check rather than by
    set_display_values()'s KeyError - same staleness family as a stale hash, hence 409 rather
    than 400."""
    import hashlib

    template = _get_real_hash(client, auth_headers, TEST_SERVER_ID, TEST_QUERY_ID)
    template_hash = hashlib.sha256(template.encode("utf-8")).hexdigest()

    response = client.post(
        "/execute",
        json={
            "server_id": TEST_SERVER_ID,
            "query_id": TEST_QUERY_ID,
            "template_hash": template_hash,
            "display_format": TEST_DISPLAY_FORMAT,
            "display_parameters": {"@unknown": "x"},
        },
        headers=auth_headers,
    )

    assert response.status_code == 409


@requires_docker
def test_execute_ignores_stale_hash_when_exempted(client, auth_headers):
    """A query listed in resources.ignore_hash_until, with a still-future date, must let a
    stale template_hash through - the hash check is the only thing waived, not the query
    itself (real parameters and real execution still apply, hence requires_docker)."""
    from datetime import datetime, timedelta, timezone

    client.app.state.server.resources.ignore_hash_until[TEST_QUERY_ID] = datetime.now(timezone.utc) + timedelta(days=1)

    response = client.post(
        "/execute",
        json={
            "server_id": TEST_SERVER_ID,
            "query_id": TEST_QUERY_ID,
            "template_hash": "stale_hash_value",
            "display_format": TEST_DISPLAY_FORMAT,
            "display_parameters": {"@1": ""},
        },
        headers=auth_headers,
    )

    assert response.status_code == 200


def test_execute_still_checks_parameters_when_hash_exempted(client, auth_headers):
    """The parameter-set check must stay active even while the hash check is waived - the
    hash exemption only covers SQL body drift, not the parameter contract (see point 10:
    template_hash never covers the DECLARE block, so it can't stand in for this check)."""
    from datetime import datetime, timedelta, timezone

    client.app.state.server.resources.ignore_hash_until[TEST_QUERY_ID] = datetime.now(timezone.utc) + timedelta(days=1)

    response = client.post(
        "/execute",
        json={
            "server_id": TEST_SERVER_ID,
            "query_id": TEST_QUERY_ID,
            "template_hash": "stale_hash_value",
            "display_format": TEST_DISPLAY_FORMAT,
            "display_parameters": {"@unknown": "x"},
        },
        headers=auth_headers,
    )

    assert response.status_code == 409


def test_execute_rejects_stale_hash_once_exemption_expired(client, auth_headers):
    """A past exemption date must not waive the hash check - it should behave exactly as if
    no exemption had ever been set (regression guard against an off-by-one on the comparison
    direction, e.g. accidentally using < instead of > against datetime.now())."""
    from datetime import datetime, timedelta, timezone

    client.app.state.server.resources.ignore_hash_until[TEST_QUERY_ID] = datetime.now(timezone.utc) - timedelta(days=1)

    response = client.post(
        "/execute",
        json={
            "server_id": TEST_SERVER_ID,
            "query_id": TEST_QUERY_ID,
            "template_hash": "stale_hash_value",
            "display_format": TEST_DISPLAY_FORMAT,
        },
        headers=auth_headers,
    )

    assert response.status_code == 409


def test_execute_reports_pool_saturated_as_ndjson_error(client, auth_headers, monkeypatch):
    """AsyncSqlConnectionError from driver.execute() (e.g. pool exhausted, connection_timeout_secs
    elapsed) must surface as a {"__error__": ...} NDJSON line, not an HTTP error status - the
    response has already started (200) by the time this is known, since _run_query may have
    already sent "__ping__" lines while waiting for the query. Must not leak the caller's
    concurrency slot either way (the per-user semaphore must be released, not left locked).
    The driver itself is monkeypatched, so no real docker connection is attempted - the
    fastmssql -> AsyncSqlConnectionError translation itself is unit-tested separately in
    tests/unit/core/test_query_drivers_async.py."""
    import hashlib

    from Pytre_Core.sql_query.query_drivers_async import AsyncSqlConnectionError, FastMssqlDriver

    async def _raise_pool_timeout(self, template, params):
        raise AsyncSqlConnectionError("Connection pool timeout - all connections are busy.")

    monkeypatch.setattr(FastMssqlDriver, "execute", _raise_pool_timeout)

    template = _get_real_hash(client, auth_headers, TEST_SERVER_ID, TEST_QUERY_ID)
    template_hash = hashlib.sha256(template.encode("utf-8")).hexdigest()

    response = client.post(
        "/execute",
        json={
            "server_id": TEST_SERVER_ID,
            "query_id": TEST_QUERY_ID,
            "template_hash": template_hash,
            "display_format": TEST_DISPLAY_FORMAT,
            "display_parameters": {"@1": ""},
        },
        headers=auth_headers,
    )

    assert response.status_code == 200
    error = json.loads(response.text.splitlines()[0])["__error__"]
    assert error["type"] == "AsyncSqlConnectionError"
    assert error["message"] == "Server unavailable: Connection pool timeout - all connections are busy."
    assert not client.app.state.server.resources.user_semaphores[TEST_USERNAME].locked()


def test_execute_reports_server_unreachable_as_ndjson_error(client, auth_headers, monkeypatch):
    """AsyncSqlConnectionError from the initial connect() (server down/unreachable) must also
    surface as a {"__error__": ...} NDJSON line - same reasoning and semaphore-leak concern
    as above. The driver itself is monkeypatched, so no real docker connection is attempted."""
    import hashlib

    from Pytre_Core.sql_query.query_drivers_async import AsyncSqlConnectionError, FastMssqlDriver

    async def _raise_unreachable(self):
        raise AsyncSqlConnectionError("Failed to connect: connection refused.")

    monkeypatch.setattr(FastMssqlDriver, "connect", _raise_unreachable)

    template = _get_real_hash(client, auth_headers, TEST_SERVER_ID, TEST_QUERY_ID)
    template_hash = hashlib.sha256(template.encode("utf-8")).hexdigest()

    response = client.post(
        "/execute",
        json={
            "server_id": TEST_SERVER_ID,
            "query_id": TEST_QUERY_ID,
            "template_hash": template_hash,
            "display_format": TEST_DISPLAY_FORMAT,
            "display_parameters": {"@1": ""},
        },
        headers=auth_headers,
    )

    assert response.status_code == 200
    error = json.loads(response.text.splitlines()[0])["__error__"]
    assert error["type"] == "AsyncSqlConnectionError"
    assert error["message"] == "Server unavailable: Failed to connect: connection refused."
    assert not client.app.state.server.resources.user_semaphores[TEST_USERNAME].locked()


def test_execute_rejects_when_user_at_concurrency_limit(client, auth_headers):
    """A user already at their concurrency limit (semaphore pre-locked here to avoid
    firing real concurrent requests) must get a clean 429, not wait indefinitely."""
    import asyncio
    import hashlib

    template = _get_real_hash(client, auth_headers, TEST_SERVER_ID, TEST_QUERY_ID)
    template_hash = hashlib.sha256(template.encode("utf-8")).hexdigest()

    client.app.state.server.resources.user_semaphores[TEST_USERNAME] = asyncio.Semaphore(0)  # already exhausted

    response = client.post(
        "/execute",
        json={
            "server_id": TEST_SERVER_ID,
            "query_id": TEST_QUERY_ID,
            "template_hash": template_hash,
            "display_format": TEST_DISPLAY_FORMAT,
            "display_parameters": {"@1": ""},
        },
        headers=auth_headers,
    )

    assert response.status_code == 429


def test_execute_rejects_invalid_parameter_value(client, auth_headers):
    import hashlib

    template = _get_real_hash(client, auth_headers, TEST_SERVER_ID, TEST_QUERY_ID)
    template_hash = hashlib.sha256(template.encode("utf-8")).hexdigest()

    response = client.post(
        "/execute",
        json={
            "server_id": TEST_SERVER_ID,
            "query_id": TEST_QUERY_ID,
            "template_hash": template_hash,
            "display_format": TEST_DISPLAY_FORMAT,
            "display_parameters": {"@1": "not-a-date"},
        },
        headers=auth_headers,
    )

    assert response.status_code == 400


def _capture_sent_params(monkeypatch) -> dict:
    """Patches the SQL driver so /execute stops right before the database, capturing the
    SQL parameters it would have sent - no docker container needed."""
    from Pytre_Core.sql_query.query_drivers_async import AsyncSqlConnectionError, FastMssqlDriver

    captured = {}

    async def _capture(self, template, params):
        captured.update(params)
        raise AsyncSqlConnectionError("stopped by test")

    monkeypatch.setattr(FastMssqlDriver, "execute", _capture)
    return captured


@pytest.mark.parametrize(
    ("date_format", "display_value"),
    [
        ("%d/%m/%Y", "10/03/2025"),
        ("%m/%d/%Y", "03/10/2025"),
        ("%Y-%m-%d", "2025-03-10"),
    ],
)
def test_execute_parses_parameters_with_client_display_format(
    client, auth_headers, monkeypatch, date_format, display_value
):
    """A date parameter is parsed with the display format sent by the client, not the server's one -
    the same date typed in different formats reaches the database as the same value."""
    import hashlib

    captured = _capture_sent_params(monkeypatch)
    template = _get_real_hash(client, auth_headers, TEST_SERVER_ID, TEST_QUERY_ID)
    template_hash = hashlib.sha256(template.encode("utf-8")).hexdigest()

    response = client.post(
        "/execute",
        json={
            "server_id": TEST_SERVER_ID,
            "query_id": TEST_QUERY_ID,
            "template_hash": template_hash,
            "display_format": {"date_format": date_format, "decimal_separator": ","},
            "display_parameters": {"@1": display_value},
        },
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert captured == {"@1": "2025-03-10"}


def test_execute_rejects_missing_display_format(client, auth_headers):
    response = client.post(
        "/execute",
        json={
            "server_id": TEST_SERVER_ID,
            "query_id": TEST_QUERY_ID,
            "template_hash": "x",
            "display_parameters": {"@1": ""},
        },
        headers=auth_headers,
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    "display_format",
    [
        {"date_format": "%d/%m", "decimal_separator": ","},  # no year
        {"date_format": "not-a-date", "decimal_separator": ","},
        {"date_format": "%d/%m/%Y", "decimal_separator": ",,"},
    ],
)
def test_execute_rejects_invalid_display_format(client, auth_headers, display_format):
    response = client.post(
        "/execute",
        json={
            "server_id": TEST_SERVER_ID,
            "query_id": TEST_QUERY_ID,
            "template_hash": "x",
            "display_format": display_format,
            "display_parameters": {"@1": ""},
        },
        headers=auth_headers,
    )

    assert response.status_code == 422
