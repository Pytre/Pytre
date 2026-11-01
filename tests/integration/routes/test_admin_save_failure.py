"""
Integration tests for a settings database that can't be written (insufficient access rights).

The failure is simulated at PyKeePass.save() so the real Kee.save_db() runs, including the reload
discarding the unsaved changes - except for the last test, which uses a real read-only folder.
"""

import logging
import os
from unittest.mock import patch

import pytest
from pykeepass import PyKeePass

from Pytre_Core.users import User, Users
from tests.integration.conftest import TEST_USER_PASSWORD, TEST_USERNAME

pytestmark = pytest.mark.integration

ERROR_TEXT = "check write access"


def _writes_denied():
    return patch.object(PyKeePass, "save", side_effect=PermissionError(13, "Permission denied"))


def _user_uuid(client, username: str = TEST_USERNAME) -> str:
    return User(client.app.state.server.app_ctx.users, username=username).uuid


def _new_user_data(username: str) -> dict:
    return {"username": username, "title": "", "auth_type": "none", "password": ""}


# ------------------------------------------------------------------------------------------
# Forms: the error is displayed and what was typed is kept
# ------------------------------------------------------------------------------------------
def test_new_user_failure_shows_the_error_and_redisplays_a_new_user_form(isolated_admin_client, caplog):
    with _writes_denied(), caplog.at_level(logging.INFO):
        response = isolated_admin_client.post("/admin/users/new", data=_new_user_data("denied_user"))

    assert response.status_code == 200
    assert ERROR_TEXT in response.text
    assert "denied_user" in response.text
    assert 'action="/admin/users/new"' in response.text  # its entry was discarded, it can't be an edit
    assert not any("saved by admin" in r.message for r in caplog.records)
    assert "denied_user" not in isolated_admin_client.get("/admin/users").text


def test_new_user_can_be_saved_once_writing_works_again(isolated_admin_client):
    with _writes_denied():
        isolated_admin_client.post("/admin/users/new", data=_new_user_data("retried_user"))

    response = isolated_admin_client.post(
        "/admin/users/new", data=_new_user_data("retried_user"), follow_redirects=False
    )

    assert response.status_code == 303
    assert "retried_user" in isolated_admin_client.get("/admin/users").text


def test_edit_user_failure_keeps_the_edit_form_and_discards_the_change(isolated_admin_client):
    uuid = _user_uuid(isolated_admin_client)
    data = {"username": "renamed_user", "title": "", "auth_type": "basic", "password": ""}

    with _writes_denied():
        response = isolated_admin_client.post(f"/admin/users/{uuid}/edit", data=data)

    assert response.status_code == 200
    assert ERROR_TEXT in response.text
    assert f'action="/admin/users/{uuid}/edit"' in response.text
    assert "renamed_user" not in isolated_admin_client.get("/admin/users").text


def test_new_server_failure_shows_the_error_and_redisplays_a_new_server_form(isolated_admin_client):
    data = {
        "id": "denied_server",
        "description": "",
        "login": "",
        "password": "",
        "type": "SQL Server",
        "login_timeout": "60",
        "timeout": "300",
    }

    with _writes_denied():
        response = isolated_admin_client.post("/admin/servers/new", data=data)

    assert response.status_code == 200
    assert ERROR_TEXT in response.text
    assert 'action="/admin/servers/new"' in response.text
    assert "denied_server" not in isolated_admin_client.get("/admin/servers").text


def test_new_sso_provider_failure_shows_the_error_and_redisplays_a_new_provider_form(isolated_admin_client):
    issuer = "https://denied.example.com"
    data = {
        "label": "",
        "issuer": issuer,
        "client_id": "pytre-client",
        "client_secret": "s3cret",
        "scopes": "openid",
        "username_claim": "preferred_username",
        "public_base_url": "https://pytre.example.com",
        "enabled": "1",
    }

    with _writes_denied():
        response = isolated_admin_client.post("/admin/sso/new", data=data)

    assert response.status_code == 200
    assert ERROR_TEXT in response.text
    assert 'action="/admin/sso/new"' in response.text
    assert issuer not in isolated_admin_client.get("/admin/sso").text


def test_settings_failure_shows_the_error_and_restores_the_settings(isolated_admin_client, tmp_path, caplog):
    new_folder = str(tmp_path)

    with _writes_denied(), caplog.at_level(logging.INFO):
        response = isolated_admin_client.post(
            "/admin/settings", data={"queries_folder": new_folder, "logs_folder": ""}, follow_redirects=False
        )

    assert response.status_code == 200
    assert ERROR_TEXT in response.text
    assert new_folder in response.text  # redisplays what was submitted
    assert not any("Settings changed by admin" in r.message for r in caplog.records)

    still_current = isolated_admin_client.get("/admin/settings")
    assert "tests/resources/queries" in still_current.text
    assert new_folder not in still_current.text


# ------------------------------------------------------------------------------------------
# Routes with no error display of their own: os_error_handler
# ------------------------------------------------------------------------------------------
def test_delete_failure_redirects_to_the_section_with_the_error(isolated_admin_client, caplog):
    isolated_admin_client.post("/admin/users/new", data=_new_user_data("kept_user"))
    uuid = _user_uuid(isolated_admin_client, "kept_user")

    with _writes_denied(), caplog.at_level(logging.ERROR):
        response = isolated_admin_client.post(f"/admin/users/{uuid}/delete", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/admin/users?error=")
    assert any("Unhandled OSError" in r.message and "Traceback" in r.message for r in caplog.records)

    list_page = isolated_admin_client.get(response.headers["location"])
    assert ERROR_TEXT in list_page.text
    assert "kept_user" in list_page.text


def test_groups_failure_redirects_to_the_section_and_discards_the_change(isolated_admin_client):
    uuid = _user_uuid(isolated_admin_client)

    with _writes_denied():
        response = isolated_admin_client.post(
            "/admin/users/groups/add", data={"uuids": [uuid], "groups": ["denied_group"]}, follow_redirects=False
        )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/admin/users?error=")
    assert "denied_group" not in User(isolated_admin_client.app.state.server.app_ctx.users, username=TEST_USERNAME).groups


def test_get_failure_never_redirects_since_it_could_loop_on_itself(isolated_admin_client):
    with patch.object(Users, "get_cust_attribs_list", side_effect=OSError("/secret/path unreadable")):
        response = isolated_admin_client.get("/admin/users/attributes", follow_redirects=False)

    assert response.status_code == 503
    assert "/secret/path" not in response.text


def test_change_password_failure_returns_503_without_the_file_path(isolated_client):
    login = isolated_client.post("/login", json={"username": TEST_USERNAME, "password": TEST_USER_PASSWORD})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    with _writes_denied():
        response = isolated_client.post("/change-password", json={"new_password": "a-new-password"}, headers=headers)

    assert response.status_code == 503
    assert response.json() == {"detail": "Server storage error, see server logs"}

    still_valid = isolated_client.post("/login", json={"username": TEST_USERNAME, "password": TEST_USER_PASSWORD})
    assert still_valid.status_code == 200


# ------------------------------------------------------------------------------------------
# Without simulation
# ------------------------------------------------------------------------------------------
@pytest.mark.skipif(
    not hasattr(os, "geteuid") or os.geteuid() == 0, reason="needs folder permissions to apply to the current user"
)
def test_new_user_in_a_real_read_only_folder_shows_the_error(isolated_admin_client, tmp_path):
    tmp_path.chmod(0o555)  # isolated_admin_client's database copy is in this same folder
    try:
        response = isolated_admin_client.post("/admin/users/new", data=_new_user_data("denied_user"))
        list_page = isolated_admin_client.get("/admin/users")
    finally:
        tmp_path.chmod(0o755)

    assert response.status_code == 200
    assert ERROR_TEXT in response.text
    assert "denied_user" not in list_page.text
