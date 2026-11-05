"""
Integration tests for the query logs in the admin webui: the "Query Logs" page (routes/admin_query_logs.py)
and the usage columns of the users and queries lists.

`logs_admin_client` turns the query logs on in a per-test copy of the test KeePass database before building
the app, so create_app() goes through its real startup path (ServerDriven created, database created or
migrated by check_db()). Executions are then written straight to that database.
"""

import contextlib
import re
import shutil
import sqlite3
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import Pytre_Core.kee as kee_module
from Pytre_Core.context import AppContext
from Pytre_Core.logs_central import LATEST_VERSION, SERVER_DB
from Pytre_Server.app import create_app
from Pytre_Server.query_logs_reader import QueryLogsSource
from Pytre_Server.routes import admin_query_logs
from tests.integration.conftest import (
    TEST_DB_PASSWORD,
    TEST_DB_PATH,
    TEST_QUERY_ID,
    TEST_USER_PASSWORD,
    TEST_USERNAME,
)

pytestmark = pytest.mark.integration

LAST_RUN = datetime(2026, 11, 7, 9, 0, tzinfo=timezone.utc)


def _enable_query_logs(monkeypatch, tmp_path):
    db_copy = tmp_path / "Pytre_test.db"
    shutil.copy(TEST_DB_PATH, db_copy)
    monkeypatch.setattr(kee_module, "KEE_FILE", db_copy)

    logs_folder = tmp_path / "logs"
    logs_folder.mkdir()
    settings = AppContext(master_key=TEST_DB_PASSWORD).settings
    settings.logs_are_on = True
    settings.logs_folder = logs_folder
    settings.save()
    return logs_folder


def _logged_in_client() -> TestClient:
    client = TestClient(create_app(master_key=TEST_DB_PASSWORD))
    client.post("/admin/login", data={"username": TEST_USERNAME, "password": TEST_USER_PASSWORD})
    return client


@pytest.fixture
def logs_admin_client(monkeypatch, tmp_path) -> TestClient:
    _enable_query_logs(monkeypatch, tmp_path)
    return _logged_in_client()


def _add_executions(client: TestClient, count: int = 1, start: datetime = LAST_RUN) -> None:
    rows = [("test", TEST_USERNAME, "", TEST_QUERY_ID, start.isoformat(), i, 1000 * i, None) for i in range(count)]
    assert client.app.state.server.resources.execution_logs.insert_into_central_db(rows)  # type: ignore


def _db_path(client: TestClient):
    return client.app.state.server.resources.execution_logs.db_path  # type: ignore


def _error_message(html: str) -> str | None:
    match = re.search(r'class="error form-error"[^>]*>([^<]+)<', html)
    return match.group(1) if match else None


# ------------------------------------------------------------------------------------------
# Startup
# ------------------------------------------------------------------------------------------
def test_startup_creates_the_query_logs_database(logs_admin_client):
    """Not only on the first logged execution: the page and the lists must work right after a start"""
    db_path = _db_path(logs_admin_client)

    assert db_path.name == SERVER_DB
    assert db_path.exists()


def test_startup_migrates_an_older_query_logs_database(monkeypatch, tmp_path):
    logs_folder = _enable_query_logs(monkeypatch, tmp_path)
    with contextlib.closing(sqlite3.connect(logs_folder / SERVER_DB)) as conn:
        conn.execute(
            "CREATE TABLE QUERIES_EXEC (SERVER_ID TEXT, USER_ID TEXT NOT NULL, USER_NAME TEXT, QUERY TEXT NOT NULL, "
            "START TEXT NOT NULL, DURATION_SECS INTEGER, NB_ROWS INTEGER, PARAMETERS TEXT)"
        )
        conn.execute("PRAGMA user_version = 2")
        conn.commit()

    client = _logged_in_client()

    with contextlib.closing(sqlite3.connect(logs_folder / SERVER_DB)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == LATEST_VERSION
    response = client.get(f"/admin/query-logs?source={QueryLogsSource.TOP_QUERIES}")
    assert _error_message(response.text) is None


# ------------------------------------------------------------------------------------------
# Users and queries lists
# ------------------------------------------------------------------------------------------
def test_users_list_has_no_last_run_column_when_query_logs_are_off(admin_client):
    assert "Last run" not in admin_client.get("/admin/users").text


def test_users_list_shows_the_last_run_of_each_user(logs_admin_client):
    _add_executions(logs_admin_client)

    response = logs_admin_client.get("/admin/users")

    assert "Last run" in response.text
    assert f'data-ts="{int(LAST_RUN.timestamp())}"' in response.text


def test_users_list_hides_the_column_when_query_logs_cant_be_read(logs_admin_client):
    _db_path(logs_admin_client).unlink()

    response = logs_admin_client.get("/admin/users")

    assert response.status_code == 200
    assert "Last run" not in response.text


def test_queries_list_has_no_usage_columns_when_query_logs_are_off(admin_client):
    assert "Nb Runs" not in admin_client.get("/admin/queries").text


def test_queries_list_shows_zero_runs_for_a_query_never_executed(logs_admin_client):
    response = logs_admin_client.get("/admin/queries")

    assert "Nb Runs" in response.text
    assert 'data-num="0"' in response.text


def test_queries_list_shows_runs_and_last_run_matched_on_the_query_file_id(logs_admin_client):
    _add_executions(logs_admin_client, count=3)

    response = logs_admin_client.get("/admin/queries")

    assert 'data-num="3"' in response.text
    assert f'data-ts="{int(LAST_RUN.timestamp())}"' in response.text


# ------------------------------------------------------------------------------------------
# Query Logs page
# ------------------------------------------------------------------------------------------
def test_query_logs_page_requires_admin_session(client):
    response = client.get("/admin/query-logs", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/admin/login")


def test_query_logs_page_says_when_query_logs_are_off(admin_client):
    response = admin_client.get("/admin/query-logs")

    assert response.status_code == 200
    assert "Query logs are disabled" in (_error_message(response.text) or "")


def test_query_logs_page_says_when_query_logs_cant_be_read(logs_admin_client):
    _db_path(logs_admin_client).unlink()

    response = logs_admin_client.get("/admin/query-logs")

    assert response.status_code == 200
    assert "Query logs unavailable" in (_error_message(response.text) or "")


@pytest.mark.parametrize("source", list(QueryLogsSource))
def test_query_logs_page_shows_every_source(logs_admin_client, source):
    _add_executions(logs_admin_client, count=2)

    response = logs_admin_client.get(f"/admin/query-logs?source={source}")

    assert response.status_code == 200
    assert _error_message(response.text) is None
    assert f'<option value="{source}" selected>{source.label}</option>' in response.text
    assert response.text.count("<tr>") >= 2  # header row and at least one data row


def test_query_logs_page_shows_dates_as_timestamps_and_numbers_as_raw_values(logs_admin_client):
    _add_executions(logs_admin_client, count=2)

    response = logs_admin_client.get("/admin/query-logs")

    assert f'data-ts="{int(LAST_RUN.timestamp())}"' in response.text
    assert 'data-num="1000"' in response.text  # NB_ROWS of the second execution


def test_query_logs_page_paginates(logs_admin_client, monkeypatch):
    monkeypatch.setattr(admin_query_logs, "PAGE_SIZE", 2)
    _add_executions(logs_admin_client, count=3)

    first = logs_admin_client.get("/admin/query-logs?page=1").text
    second = logs_admin_client.get("/admin/query-logs?page=2").text

    assert first.count("<tr>") == 3  # header + 2 rows
    assert 'value="2">Next' in first and 'value="2" disabled' not in first
    assert second.count("<tr>") == 2  # header + the last row
    assert 'value="3" disabled>Next' in second


@pytest.mark.parametrize("query", ["source=sqlite_master", "page=0", "page=abc"])
def test_query_logs_page_rejects_invalid_parameters(logs_admin_client, query):
    assert logs_admin_client.get(f"/admin/query-logs?{query}").status_code == 422


def test_sidebar_shows_query_logs_and_server_logs_links(admin_client):
    response = admin_client.get("/admin/query-logs")

    assert 'href="/admin/query-logs">Query Logs<' in response.text
    assert 'href="/admin/logs">Server Logs<' in response.text
