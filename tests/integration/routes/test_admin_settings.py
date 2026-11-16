"""
Integration tests for the /admin/settings routes (Pytre_Server/routes/admin_settings.py) -
general settings shown/edited via the admin webui. They can only be saved from the server itself: saving uses
`isolated_local_admin_client`, a remote connection `isolated_admin_client`.
"""

import logging
import re

import pytest

from Pytre_Core.context import AppContext
from tests.conftest import audit_fields, audit_records
from tests.integration.conftest import TEST_USERNAME

pytestmark = pytest.mark.integration


def _stored_settings():
    return AppContext(master_key="password").settings


def _fieldset_tag(html: str) -> str:
    match = re.search(r"<fieldset[^>]*>", html)
    assert match is not None, "settings fieldset not found"
    return match.group(0)


# ================================================================================
# Page
# ================================================================================


def test_settings_page_shows_current_values(admin_client):
    response = admin_client.get("/admin/settings")

    assert response.status_code == 200
    assert "tests/resources/queries" in response.text
    assert 'id="logs_are_on"' in response.text


def test_settings_page_is_read_only_from_a_remote_connection(admin_client):
    page = admin_client.get("/admin/settings").text

    assert "disabled" in _fieldset_tag(page)
    assert '<button type="submit">Save</button>' not in page
    assert "These settings can only be changed from the server itself" in page
    assert "tests/resources/queries" in page  # still shown


def test_settings_page_is_editable_from_the_server_itself(local_admin_client):
    page = local_admin_client.get("/admin/settings").text

    assert "disabled" not in _fieldset_tag(page)
    assert '<button type="submit">Save</button>' in page
    assert "These settings can only be changed from the server itself" not in page


# ================================================================================
# Saving from a remote connection: refused
# ================================================================================


def test_settings_save_is_refused_from_a_remote_connection(isolated_admin_client, tmp_path):
    response = isolated_admin_client.post(
        "/admin/settings", data={"queries_folder": str(tmp_path), "logs_folder": ""}, follow_redirects=False
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Only available from the server itself"
    assert "tests/resources/queries" in str(_stored_settings().queries_folder)


def test_settings_save_refusal_is_logged_as_a_warning(isolated_admin_client, tmp_path, caplog):
    with caplog.at_level(logging.INFO):
        isolated_admin_client.post("/admin/settings", data={"queries_folder": str(tmp_path), "logs_folder": ""})

    [record] = audit_records(caplog, "local_action.failed")
    assert record.levelno == logging.WARNING
    assert f"Local-only action refused for admin '{TEST_USERNAME}' on '/admin/settings'" in record.message
    assert audit_records(caplog, "settings.update") == []


def test_remote_connection_cannot_turn_the_execution_logs_off(isolated_admin_client):
    """An admin could otherwise stop the logs before running their own queries, to leave no trace."""
    logs_were_on = _stored_settings().logs_are_on

    isolated_admin_client.post("/admin/settings", data={"queries_folder": "", "logs_folder": ""})

    assert _stored_settings().logs_are_on is logs_were_on


def test_settings_save_through_a_reverse_proxy_is_refused(isolated_local_admin_client, tmp_path):
    """A request from the loopback address but carrying a forwarding header went through a proxy on the server."""
    response = isolated_local_admin_client.post(
        "/admin/settings",
        data={"queries_folder": str(tmp_path), "logs_folder": ""},
        headers={"X-Forwarded-For": "203.0.113.7"},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert "tests/resources/queries" in str(_stored_settings().queries_folder)


# ================================================================================
# Saving from the server itself
# ================================================================================


def test_settings_save_succeeds_and_persists(isolated_local_admin_client, tmp_path):
    new_folder = str(tmp_path)

    response = isolated_local_admin_client.post(
        "/admin/settings",
        data={"queries_folder": new_folder, "logs_folder": ""},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/settings?msg=Settings+saved"

    settings = _stored_settings()
    assert str(settings.queries_folder) == new_folder
    assert settings.logs_are_on is False


def test_settings_save_is_logged_as_a_warning_with_the_admin_username(isolated_local_admin_client, tmp_path, caplog):
    with caplog.at_level(logging.INFO):
        isolated_local_admin_client.post(
            "/admin/settings", data={"queries_folder": str(tmp_path), "logs_folder": ""}
        )

    [record] = audit_records(caplog, "settings.update")
    assert record.levelno == logging.WARNING
    assert record.message.startswith(f"Settings changed by admin '{TEST_USERNAME}' from 127.0.0.1: ")
    assert audit_fields(record)["changes"]["queries_folder"][1] == str(tmp_path)
    assert audit_fields(record)["target"] is None


def test_settings_save_failure_is_not_logged(isolated_local_admin_client, caplog):
    """A failed save (invalid folder) must not be logged as a successful change."""
    with caplog.at_level(logging.INFO):
        isolated_local_admin_client.post(
            "/admin/settings", data={"queries_folder": "/does/not/exist", "logs_folder": ""}
        )

    assert audit_records(caplog) == []


def test_settings_save_invalid_queries_folder_shows_error_without_corrupting_settings(isolated_local_admin_client):
    """Regression test: a failed save() must not leave the shared Settings singleton mutated
    with the bad, unsaved value."""
    original = isolated_local_admin_client.get("/admin/settings")
    assert "tests/resources/queries" in original.text

    response = isolated_local_admin_client.post(
        "/admin/settings",
        data={"queries_folder": "/does/not/exist", "logs_folder": ""},
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert "Folder doesn&#39;t exist" in response.text or "Folder doesn't exist" in response.text
    assert "/does/not/exist" in response.text  # redisplays what was submitted, for correction

    # the shared settings singleton must still hold the last known-good value, not the bad one
    still_current = isolated_local_admin_client.get("/admin/settings")
    assert "tests/resources/queries" in still_current.text
    assert "/does/not/exist" not in still_current.text

    assert "does" not in str(_stored_settings().queries_folder)


def test_settings_save_error_redisplays_an_editable_form(isolated_local_admin_client):
    """The redisplayed form is the local one: editable, with its Save button, without the local-only message."""
    page = isolated_local_admin_client.post(
        "/admin/settings", data={"queries_folder": "/does/not/exist", "logs_folder": ""}
    ).text

    assert "disabled" not in _fieldset_tag(page)
    assert '<button type="submit">Save</button>' in page
    assert "These settings can only be changed from the server itself" not in page


def test_settings_save_invalid_logs_folder_raises_only_when_logs_enabled(isolated_local_admin_client, tmp_path):
    response = isolated_local_admin_client.post(
        "/admin/settings",
        data={
            "queries_folder": str(tmp_path),
            "logs_are_on": "1",
            "logs_folder": "/does/not/exist",
        },
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert "Folder doesn&#39;t exist" in response.text or "Folder doesn't exist" in response.text


def test_settings_save_invalid_logs_folder_ok_when_logs_disabled(isolated_local_admin_client, tmp_path):
    response = isolated_local_admin_client.post(
        "/admin/settings",
        data={"queries_folder": str(tmp_path), "logs_folder": "/does/not/exist"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/settings?msg=Settings+saved"


def test_settings_save_without_change_is_not_audited(isolated_local_admin_client, tmp_path, caplog):
    data = {"queries_folder": str(tmp_path), "logs_folder": str(tmp_path)}
    isolated_local_admin_client.post("/admin/settings", data=data)
    caplog.clear()

    with caplog.at_level(logging.INFO):
        response = isolated_local_admin_client.post("/admin/settings", data=data, follow_redirects=False)

    assert response.status_code == 303
    assert audit_records(caplog) == []
