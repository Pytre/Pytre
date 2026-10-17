"""
Integration tests for the /admin/settings routes (Pytre_Server/routes/admin_settings.py) -
general settings shown/edited via the admin webui. Uses `isolated_admin_client` for anything
that writes to the KeePass database.
"""

import logging

import pytest

from Pytre_Core.context import AppContext
from tests.integration.conftest import TEST_USERNAME

pytestmark = pytest.mark.integration


def test_settings_page_shows_current_values(admin_client):
    response = admin_client.get("/admin/settings")

    assert response.status_code == 200
    assert "tests/resources/queries" in response.text
    assert 'id="logs_are_on"' in response.text


def test_settings_save_succeeds_and_persists(isolated_admin_client, tmp_path):
    new_folder = str(tmp_path)

    response = isolated_admin_client.post(
        "/admin/settings",
        data={"queries_folder": new_folder, "logs_folder": ""},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/settings?msg=Settings+saved"

    app_ctx = AppContext(master_key="password")
    assert str(app_ctx.settings.queries_folder) == new_folder
    assert app_ctx.settings.logs_are_on is False


def test_settings_save_is_logged_with_the_admin_username(isolated_admin_client, tmp_path, caplog):
    with caplog.at_level(logging.INFO):
        isolated_admin_client.post(
            "/admin/settings", data={"queries_folder": str(tmp_path), "logs_folder": ""}
        )

    assert any(f"Settings changed by admin '{TEST_USERNAME}'" in r.message for r in caplog.records)


def test_settings_save_failure_is_not_logged(isolated_admin_client, caplog):
    """A failed save (invalid folder) must not be logged as a successful change."""
    with caplog.at_level(logging.INFO):
        isolated_admin_client.post(
            "/admin/settings", data={"queries_folder": "/does/not/exist", "logs_folder": ""}
        )

    assert not any("Settings changed by admin" in r.message for r in caplog.records)


def test_settings_save_invalid_queries_folder_shows_error_without_corrupting_settings(isolated_admin_client):
    """Regression test: a failed save() must not leave the shared Settings singleton mutated
    with the bad, unsaved value - see the mutate-then-save discussion in the plan doc."""
    original = isolated_admin_client.get("/admin/settings")
    assert "tests/resources/queries" in original.text

    response = isolated_admin_client.post(
        "/admin/settings",
        data={"queries_folder": "/does/not/exist", "logs_folder": ""},
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert "Folder doesn&#39;t exist" in response.text or "Folder doesn't exist" in response.text
    assert "/does/not/exist" in response.text  # redisplays what was submitted, for correction

    # the shared settings singleton must still hold the last known-good value, not the bad one
    still_current = isolated_admin_client.get("/admin/settings")
    assert "tests/resources/queries" in still_current.text
    assert "/does/not/exist" not in still_current.text

    app_ctx = AppContext(master_key="password")
    assert "does" not in str(app_ctx.settings.queries_folder)


def test_settings_save_invalid_logs_folder_raises_only_when_logs_enabled(isolated_admin_client, tmp_path):
    response = isolated_admin_client.post(
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


def test_settings_save_invalid_logs_folder_ok_when_logs_disabled(isolated_admin_client, tmp_path):
    response = isolated_admin_client.post(
        "/admin/settings",
        data={"queries_folder": str(tmp_path), "logs_folder": "/does/not/exist"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/settings?msg=Settings+saved"
