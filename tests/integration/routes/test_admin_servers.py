"""
Integration tests for the /admin/servers routes (Pytre_Server/routes/admin_servers.py) - list,
create, edit, delete. Uses `isolated_admin_client` for anything that writes to the KeePass database.
"""

import logging
import re
from unittest.mock import AsyncMock, Mock

import pytest

from Pytre_Server.server_state import _DriverEntry
from tests.integration.conftest import TEST_USERNAME

pytestmark = pytest.mark.integration


def _find_uuid(html: str, server_id: str) -> str:
    """Extracts a server's edit-link uuid from the rendered list page, by scanning the row that
    contains its id."""
    for row in html.split("<tr>"):
        if f">{server_id}<" in row:
            match = re.search(r"/admin/servers/([0-9a-f-]+)/edit", row)
            assert match is not None
            return match.group(1)
    raise AssertionError(f"{server_id} not found in list page")


def test_servers_list_renders_empty(admin_client):
    response = admin_client.get("/admin/servers")
    assert response.status_code == 200
    assert "<table" in response.text


def test_servers_list_shows_existing_server(admin_client):
    response = admin_client.get("/admin/servers")
    assert response.status_code == 200
    assert "test" in response.text


def test_create_server_succeeds_and_appears_in_list(isolated_admin_client):
    response = isolated_admin_client.post(
        "/admin/servers/new",
        data={
            "id": "new_server",
            "description": "New Server",
            "login": "sa",
            "password": "s3cret!",
            "type": "SQL Server",
            "charset": "UTF-8",
            "database": "master",
            "host": "127.0.0.1",
            "port": "1433",
            "server": "",
            "login_timeout": "60",
            "timeout": "300",
            "groups": ["sales", "it", "ops"],
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/servers?msg=Server+saved"

    list_page = isolated_admin_client.get("/admin/servers")
    assert "new_server" in list_page.text
    assert "New Server" in list_page.text
    assert "it" in list_page.text and "ops" in list_page.text


def test_create_server_is_logged_with_the_admin_username(isolated_admin_client, caplog):
    with caplog.at_level(logging.INFO):
        isolated_admin_client.post(
            "/admin/servers/new",
            data={
                "id": "logged_server",
                "description": "",
                "login": "",
                "password": "",
                "type": "SQL Server",
                "login_timeout": "60",
                "timeout": "300",
            },
        )

    assert any(
        f"Server 'logged_server' saved by admin '{TEST_USERNAME}'" in r.message for r in caplog.records
    )


def test_create_server_round_trip_keeps_each_field_distinct(isolated_admin_client):
    """Regression test for a decimal shift bug found in review: _save_server() used to bind its
    positional arguments to the wrong parameters, silently scrambling id/login/password/type/
    charset/database/host/port/server into each other's slots. Every field here has a distinct,
    unmistakable value so any such shift would be caught."""
    data = {
        "id": "roundtrip_srv",
        "description": "DESC_VAL",
        "login": "LOGIN_VAL",
        "password": "PWD_VAL",
        "type": "PostgreSQL",
        "charset": "CHARSET_VAL",
        "database": "DATABASE_VAL",
        "host": "HOST_VAL",
        "port": "PORT_VAL",
        "server": "SERVER_VAL",
        "login_timeout": "42",
        "timeout": "99",
    }
    response = isolated_admin_client.post("/admin/servers/new", data=data, follow_redirects=False)
    assert response.status_code == 303

    list_page = isolated_admin_client.get("/admin/servers")
    uuid = _find_uuid(list_page.text, "roundtrip_srv")
    edit_page = isolated_admin_client.get(f"/admin/servers/{uuid}/edit").text

    for field in ("description", "login", "host", "port", "database", "server", "charset", "login_timeout", "timeout"):
        match = re.search(rf'id="{field}"[^>]*value="([^"]*)"', edit_page)
        assert match is not None, f"{field} input not found"
        assert match.group(1) == data[field], f"{field} did not round-trip correctly: got {match.group(1)!r}"
    assert '<option value="PostgreSQL" selected>' in edit_page


def test_create_server_trust_certificate_checkbox_round_trips(isolated_admin_client):
    base_data = {
        "id": "trust_srv",
        "description": "",
        "login": "sa",
        "password": "pwd",
        "type": "SQL Server",
        "login_timeout": "60",
        "timeout": "300",
    }

    response = isolated_admin_client.post(
        "/admin/servers/new", data={**base_data, "trust_server_certificate": "1"}, follow_redirects=False
    )
    assert response.status_code == 303

    list_page = isolated_admin_client.get("/admin/servers")
    uuid = _find_uuid(list_page.text, "trust_srv")
    edit_page = isolated_admin_client.get(f"/admin/servers/{uuid}/edit").text
    checkbox = re.search(r'<input[^>]*id="trust_server_certificate"[^>]*>', edit_page)
    assert checkbox is not None
    assert "checked" in checkbox.group(0)

    # unchecking it (box simply absent from the submitted form) must persist as False
    response = isolated_admin_client.post(f"/admin/servers/{uuid}/edit", data=base_data, follow_redirects=False)
    assert response.status_code == 303

    edit_page = isolated_admin_client.get(f"/admin/servers/{uuid}/edit").text
    checkbox = re.search(r'<input[^>]*id="trust_server_certificate"[^>]*>', edit_page)
    assert checkbox is not None
    assert "checked" not in checkbox.group(0)


def test_create_server_blank_id_reports_error(isolated_admin_client):
    """ServerForm accepts an empty string (a required Form(...) parameter would have been treated as
    missing and answered with a 422), so the request reaches Server.save() and its own blank-id
    check - the form is re-rendered with that message."""
    response = isolated_admin_client.post(
        "/admin/servers/new",
        data={"id": "", "description": "", "login": "", "password": "", "type": "SQL Server", "login_timeout": "60", "timeout": "300"},
    )
    assert response.status_code == 200
    match = re.search(r'<p class="error form-error">(.*?)</p>', response.text)
    assert match is not None
    assert "must have an id" in match.group(1)


def test_create_server_duplicate_id_reports_error(isolated_admin_client):
    isolated_admin_client.post(
        "/admin/servers/new",
        data={"id": "dup_server", "description": "", "login": "", "password": "", "type": "SQL Server", "login_timeout": "60", "timeout": "300"},
        follow_redirects=False,
    )

    response = isolated_admin_client.post(
        "/admin/servers/new",
        data={"id": "dup_server", "description": "Second", "login": "", "password": "", "type": "SQL Server", "login_timeout": "60", "timeout": "300"},
    )
    assert response.status_code == 200
    assert "<form" in response.text  # re-rendered form, not a redirect
    match = re.search(r'<p class="error form-error">(.*?)</p>', response.text)
    assert match is not None
    assert "dup_server" in match.group(1)


def test_edit_server_updates_fields(isolated_admin_client):
    isolated_admin_client.post(
        "/admin/servers/new",
        data={"id": "edit_me", "description": "Before", "login": "", "password": "", "type": "SQL Server", "login_timeout": "60", "timeout": "300"},
        follow_redirects=False,
    )
    list_page = isolated_admin_client.get("/admin/servers")
    uuid = _find_uuid(list_page.text, "edit_me")

    response = isolated_admin_client.post(
        f"/admin/servers/{uuid}/edit",
        data={
            "id": "edit_me",
            "description": "After",
            "login": "sa",
            "password": "",
            "type": "PostgreSQL",
            "host": "newhost",
            "login_timeout": "60",
            "timeout": "300",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    edit_page = isolated_admin_client.get(f"/admin/servers/{uuid}/edit")
    assert 'value="After"' in edit_page.text
    assert 'value="newhost"' in edit_page.text
    assert '<option value="PostgreSQL" selected>' in edit_page.text


def test_edit_server_preserves_password_when_field_left_blank(isolated_admin_client):
    """Regression: _save_server() used to unconditionally overwrite the password from the form,
    so leaving the field blank on edit (the normal case - the form never redisplays a stored
    password) silently blanked out a real, already-configured password."""
    isolated_admin_client.post(
        "/admin/servers/new",
        data={
            "id": "keep_pwd",
            "description": "",
            "login": "sa",
            "password": "s3cret!",
            "type": "SQL Server",
            "login_timeout": "60",
            "timeout": "300",
        },
        follow_redirects=False,
    )
    list_page = isolated_admin_client.get("/admin/servers")
    uuid = _find_uuid(list_page.text, "keep_pwd")

    response = isolated_admin_client.post(
        f"/admin/servers/{uuid}/edit",
        data={
            "id": "keep_pwd",
            "description": "Updated",
            "login": "sa",
            "password": "",
            "type": "SQL Server",
            "login_timeout": "60",
            "timeout": "300",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    from Pytre_Core.context import AppContext

    app_ctx = AppContext(master_key="password")
    server = app_ctx.servers.find_server_by_uuid(uuid)
    assert server is not None
    assert server.password == "s3cret!"


def test_edit_unknown_uuid_redirects_to_list_with_error(isolated_admin_client):
    response = isolated_admin_client.get("/admin/servers/00000000-0000-0000-0000-000000000000/edit", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/servers?error=Server+not+found"


def test_delete_single_server(isolated_admin_client):
    isolated_admin_client.post(
        "/admin/servers/new",
        data={"id": "delete_me", "description": "", "login": "", "password": "", "type": "SQL Server", "login_timeout": "60", "timeout": "300"},
        follow_redirects=False,
    )
    list_page = isolated_admin_client.get("/admin/servers")
    uuid = _find_uuid(list_page.text, "delete_me")

    response = isolated_admin_client.post(f"/admin/servers/{uuid}/delete", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/servers?msg=Server+deleted"

    list_page_after = isolated_admin_client.get("/admin/servers")
    assert "delete_me" not in list_page_after.text


def test_delete_unknown_uuid_redirects_with_error(isolated_admin_client):
    response = isolated_admin_client.post(
        "/admin/servers/00000000-0000-0000-0000-000000000000/delete", follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/servers?error=Server+not+found"


def test_delete_single_server_is_logged_with_the_admin_username(isolated_admin_client, caplog):
    isolated_admin_client.post(
        "/admin/servers/new",
        data={
            "id": "delete_me_logged",
            "description": "",
            "login": "",
            "password": "",
            "type": "SQL Server",
            "login_timeout": "60",
            "timeout": "300",
        },
    )
    list_page = isolated_admin_client.get("/admin/servers")
    uuid = _find_uuid(list_page.text, "delete_me_logged")

    with caplog.at_level(logging.INFO):
        isolated_admin_client.post(f"/admin/servers/{uuid}/delete")

    assert any(
        f"Server 'delete_me_logged' deleted by admin '{TEST_USERNAME}'" in r.message for r in caplog.records
    )


def test_bulk_delete_removes_all_selected(isolated_admin_client):
    for server_id in ("bulk_a", "bulk_b"):
        isolated_admin_client.post(
            "/admin/servers/new",
            data={"id": server_id, "description": "", "login": "", "password": "", "type": "SQL Server", "login_timeout": "60", "timeout": "300"},
            follow_redirects=False,
        )
    list_page = isolated_admin_client.get("/admin/servers")
    uuid_a = _find_uuid(list_page.text, "bulk_a")
    uuid_b = _find_uuid(list_page.text, "bulk_b")

    response = isolated_admin_client.post(
        "/admin/servers/delete", data={"uuids": [uuid_a, uuid_b]}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/servers?msg=2+server(s)+deleted"

    list_page_after = isolated_admin_client.get("/admin/servers")
    assert "bulk_a" not in list_page_after.text
    assert "bulk_b" not in list_page_after.text


def test_bulk_delete_is_logged_per_server_with_the_admin_username(isolated_admin_client, caplog):
    for server_id in ("bulk_log_a", "bulk_log_b"):
        isolated_admin_client.post(
            "/admin/servers/new",
            data={"id": server_id, "description": "", "login": "", "password": "", "type": "SQL Server", "login_timeout": "60", "timeout": "300"},
        )
    list_page = isolated_admin_client.get("/admin/servers")
    uuid_a = _find_uuid(list_page.text, "bulk_log_a")
    uuid_b = _find_uuid(list_page.text, "bulk_log_b")

    with caplog.at_level(logging.INFO):
        isolated_admin_client.post("/admin/servers/delete", data={"uuids": [uuid_a, uuid_b]})

    assert any(
        f"Server 'bulk_log_a' deleted by admin '{TEST_USERNAME}' (bulk)" in r.message for r in caplog.records
    )
    assert any(
        f"Server 'bulk_log_b' deleted by admin '{TEST_USERNAME}' (bulk)" in r.message for r in caplog.records
    )


def test_bulk_delete_reports_partial_failure(isolated_admin_client):
    isolated_admin_client.post(
        "/admin/servers/new",
        data={"id": "bulk_real", "description": "", "login": "", "password": "", "type": "SQL Server", "login_timeout": "60", "timeout": "300"},
        follow_redirects=False,
    )
    list_page = isolated_admin_client.get("/admin/servers")
    uuid_real = _find_uuid(list_page.text, "bulk_real")
    fake_uuid = "00000000-0000-0000-0000-000000000000"

    response = isolated_admin_client.post(
        "/admin/servers/delete", data={"uuids": [uuid_real, fake_uuid]}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/servers?msg=1+server(s)+deleted,+1+error(s)"


# ------------------------------------------------------------------------------------------
# Reload
# ------------------------------------------------------------------------------------------
def test_reload_servers_redirects_to_the_list(isolated_admin_client):
    """Post/Redirect/Get: the browser must land back on a GET-able URL, never stay parked on
    this POST-only one (a page refresh there would resubmit the POST)."""
    response = isolated_admin_client.post("/admin/servers/reload", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/admin/servers?msg=")

    response = isolated_admin_client.get(response.headers["location"])
    assert "Servers reloaded (0 connection(s) reset)" in response.text


def test_reload_servers_reports_a_failed_reload(isolated_admin_client, monkeypatch):
    """Only the explicit reload=True call fails - the fallback re-render right after (which
    calls get_all_servers() with its reload=False default) must still succeed so the error
    page itself can be shown."""
    from Pytre_Core.servers import Servers

    original = Servers.get_all_servers

    def _raise_on_reload(self, reload=False, grp_filter=None):
        if reload:
            raise RuntimeError("boom")
        return original(self, reload=reload, grp_filter=grp_filter)

    monkeypatch.setattr(Servers, "get_all_servers", _raise_on_reload)

    response = isolated_admin_client.post("/admin/servers/reload")

    assert response.status_code == 200
    assert "Reload failed: boom" in response.text


def test_reload_servers_picks_up_a_change_made_outside_the_webui(isolated_admin_client):
    """A KeePass edit made through an independent Servers instance (e.g. the desktop client,
    or a direct KeePass edit) isn't visible to the already-running app until an explicit reload -
    servers_list()'s default get_all_servers() call doesn't reload from disk on its own."""
    isolated_admin_client.post(
        "/admin/servers/new",
        data={"id": "outside_edit", "description": "Before", "login": "", "password": "", "type": "SQL Server", "login_timeout": "60", "timeout": "300"},
        follow_redirects=False,
    )

    from Pytre_Core.context import AppContext

    other_app_ctx = AppContext(master_key="password")
    server = other_app_ctx.servers.servers_dict["outside_edit"]
    server.description = "Changed outside the webui"
    server.save()

    stale_page = isolated_admin_client.get("/admin/servers")
    assert "Changed outside the webui" not in stale_page.text

    isolated_admin_client.post("/admin/servers/reload")

    fresh_page = isolated_admin_client.get("/admin/servers")
    assert "Changed outside the webui" in fresh_page.text


def test_save_server_resets_the_cached_connection_for_that_server(isolated_admin_client):
    isolated_admin_client.post(
        "/admin/servers/new",
        data={"id": "edit_conn", "description": "Before", "login": "", "password": "", "type": "SQL Server", "login_timeout": "60", "timeout": "300"},
        follow_redirects=False,
    )
    list_page = isolated_admin_client.get("/admin/servers")
    uuid = _find_uuid(list_page.text, "edit_conn")

    fake_driver = Mock()
    fake_driver.disconnect = AsyncMock()
    resources = isolated_admin_client.app.state.server.resources
    resources.driver_entries["edit_conn"] = _DriverEntry(driver=fake_driver)

    isolated_admin_client.post(
        f"/admin/servers/{uuid}/edit",
        data={"id": "edit_conn", "description": "After", "login": "", "password": "", "type": "SQL Server", "login_timeout": "60", "timeout": "300"},
        follow_redirects=False,
    )

    assert "edit_conn" not in resources.driver_entries


def test_delete_server_resets_the_cached_connection_for_that_server(isolated_admin_client):
    isolated_admin_client.post(
        "/admin/servers/new",
        data={"id": "delete_conn", "description": "", "login": "", "password": "", "type": "SQL Server", "login_timeout": "60", "timeout": "300"},
        follow_redirects=False,
    )
    list_page = isolated_admin_client.get("/admin/servers")
    uuid = _find_uuid(list_page.text, "delete_conn")

    fake_driver = Mock()
    fake_driver.disconnect = AsyncMock()
    resources = isolated_admin_client.app.state.server.resources
    resources.driver_entries["delete_conn"] = _DriverEntry(driver=fake_driver)

    isolated_admin_client.post(f"/admin/servers/{uuid}/delete", follow_redirects=False)

    assert "delete_conn" not in resources.driver_entries


def test_bulk_delete_resets_cached_connections_for_each_deleted_server(isolated_admin_client):
    for server_id in ("bulk_conn_a", "bulk_conn_b"):
        isolated_admin_client.post(
            "/admin/servers/new",
            data={"id": server_id, "description": "", "login": "", "password": "", "type": "SQL Server", "login_timeout": "60", "timeout": "300"},
            follow_redirects=False,
        )
    list_page = isolated_admin_client.get("/admin/servers")
    uuid_a = _find_uuid(list_page.text, "bulk_conn_a")
    uuid_b = _find_uuid(list_page.text, "bulk_conn_b")

    resources = isolated_admin_client.app.state.server.resources
    for server_id in ("bulk_conn_a", "bulk_conn_b"):
        fake_driver = Mock()
        fake_driver.disconnect = AsyncMock()
        resources.driver_entries[server_id] = _DriverEntry(driver=fake_driver)

    isolated_admin_client.post("/admin/servers/delete", data={"uuids": [uuid_a, uuid_b]}, follow_redirects=False)

    assert "bulk_conn_a" not in resources.driver_entries
    assert "bulk_conn_b" not in resources.driver_entries
