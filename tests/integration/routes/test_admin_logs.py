"""
Integration tests for the /admin/logs routes (Pytre_Server/routes/admin_logs.py) - the live
application log viewer page, its SSE tail, and its "load older" endpoint.

The underlying LogBufferHandler is a process-wide singleton (see log_buffer.py's idempotent
setup_log_buffer()), shared across every test in the same pytest-xdist worker - entries logged by
other tests are expected to already be sitting in the buffer. Assertions here therefore always
check "my distinctively-tagged message is present somewhere", never "the buffer contains exactly
N entries" or "the buffer is empty".
"""

import uuid

import pytest

from Pytre_Core.utils import log as pytre_log
from Pytre_Server.routes import admin_logs

pytestmark = pytest.mark.integration


def _unique_marker() -> str:
    return f"test-marker-{uuid.uuid4()}"


# ------------------------------------------------------------------------------------------
# Page
# ------------------------------------------------------------------------------------------
def test_logs_page_requires_admin_session(client):
    """No loopback/session -> redirected to login, same gate as every other /admin/* route."""
    response = client.get("/admin/logs", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/admin/login")


def test_logs_page_renders_level_checkboxes_and_pause_button(admin_client):
    response = admin_client.get("/admin/logs")

    assert response.status_code == 200
    for level in admin_logs.LEVELS:
        assert f'data-level="{level}"' in response.text
    assert 'id="logs-pause-btn"' in response.text


def test_logs_page_shows_a_logged_message(admin_client):
    marker = _unique_marker()
    pytre_log(marker, sublogger="test_admin_logs")

    response = admin_client.get("/admin/logs")

    assert marker in response.text


def test_logs_page_caps_rendered_entries_at_initial_page_size(admin_client, monkeypatch):
    monkeypatch.setattr(admin_logs, "INITIAL_PAGE_SIZE", 2)
    markers = [_unique_marker() for _ in range(5)]
    for marker in markers:
        pytre_log(marker, sublogger="test_admin_logs")

    response = admin_client.get("/admin/logs")

    # only the 2 most recent of the 5 just-logged markers should appear - the rest (older, and
    # anything from other tests) are left out of the initial render on purpose
    assert markers[-1] in response.text
    assert markers[-2] in response.text
    assert markers[0] not in response.text


def test_sidebar_shows_logs_link(admin_client):
    response = admin_client.get("/admin/logs")

    assert 'href="/admin/logs"' in response.text
    assert ">Logs<" in response.text


# ------------------------------------------------------------------------------------------
# Stream (SSE)
# ------------------------------------------------------------------------------------------
def test_logs_stream_requires_admin_session(client):
    response = client.get("/admin/logs/stream", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/admin/login")


### Everything past "did the request get past auth" - the stream's own generator logic
### (what it yields, when it stops) - is unit-tested directly in
### tests/unit/server/test_admin_logs.py against _stream_events(), not through a real HTTP
### round trip: Starlette's TestClient can't drive a StreamingResponse whose generator runs
### forever, since it waits for the response to finish before returning control to the test.


# ------------------------------------------------------------------------------------------
# Before (older-history pagination)
# ------------------------------------------------------------------------------------------
def test_logs_before_requires_admin_session(client):
    response = client.get("/admin/logs/before?before=1", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/admin/login")


def test_logs_before_returns_entries_preceding_the_given_seq(admin_client):
    log_buffer = admin_client.app.state.server.resources.log_buffer
    older_marker = _unique_marker()
    pytre_log(older_marker, sublogger="test_admin_logs")
    before_seq = max(e.seq for e in log_buffer.entries_since(0))
    newer_marker = _unique_marker()
    pytre_log(newer_marker, sublogger="test_admin_logs")

    response = admin_client.get(f"/admin/logs/before?before={before_seq + 1}&limit=5")

    assert response.status_code == 200
    messages = [e["message"] for e in response.json()]
    assert older_marker in messages
    assert newer_marker not in messages


def test_logs_before_is_empty_once_history_exhausted(admin_client):
    response = admin_client.get("/admin/logs/before?before=1&limit=5")

    assert response.status_code == 200
    assert response.json() == []
