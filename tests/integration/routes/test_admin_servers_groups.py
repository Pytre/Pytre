"""
Integration tests for the /admin/servers/groups/{add,remove} routes - bulk group assignment
across multiple selected servers.
"""

import logging
import re

import pytest

from tests.conftest import audit_fields, audit_records
from tests.integration.conftest import TEST_USERNAME
from tests.integration.routes.conftest import TESTCLIENT_HOST

pytestmark = pytest.mark.integration


def _find_uuid(html: str, server_id: str) -> str:
    for row in html.split("<tr>"):
        if f">{server_id}<" in row:
            match = re.search(r"/admin/servers/([0-9a-f-]+)/edit", row)
            assert match is not None
            return match.group(1)
    raise AssertionError(f"{server_id} not found in list page")


def _create_server(client, server_id: str) -> str:
    client.post(
        "/admin/servers/new",
        data={"id": server_id, "description": "", "login": "", "password": "", "type": "SQL Server", "login_timeout": "60", "timeout": "300"},
        follow_redirects=False,
    )
    list_page = client.get("/admin/servers")
    return _find_uuid(list_page.text, server_id)


def test_groups_add_form_redirects_without_selection(isolated_admin_client):
    response = isolated_admin_client.get("/admin/servers/groups/add", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/servers?error=No+server+selected"


def test_groups_remove_form_redirects_without_selection(isolated_admin_client):
    response = isolated_admin_client.get("/admin/servers/groups/remove", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/servers?error=No+server+selected"


def test_groups_add_form_renders_with_selection(isolated_admin_client):
    uuid = _create_server(isolated_admin_client, "grp_target")
    response = isolated_admin_client.get("/admin/servers/groups/add", params={"uuids": [uuid]})
    assert response.status_code == 200
    assert "<form" in response.text


def test_groups_add_applies_existing_and_new_groups_to_multiple_servers(isolated_admin_client):
    from Pytre_Core.context import AppContext

    uuid_a = _create_server(isolated_admin_client, "grp_a")
    uuid_b = _create_server(isolated_admin_client, "grp_b")

    # "existing" group must actually exist first - the seeded test DB has none by default
    isolated_admin_client.post(
        "/admin/servers/groups/add", data={"uuids": [uuid_a], "groups": ["sales"]}, follow_redirects=False
    )

    response = isolated_admin_client.post(
        "/admin/servers/groups/add",
        data={"uuids": [uuid_a, uuid_b], "groups": ["sales", "brand_new_group"]},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/servers?msg=Groups+assigned"

    app_ctx = AppContext(master_key="password")
    for uuid in (uuid_a, uuid_b):
        server = app_ctx.servers.find_server_by_uuid(uuid)
        assert server is not None
        assert "sales" in server.grp_authorized
        assert "brand_new_group" in server.grp_authorized


def test_groups_add_without_any_group_reshows_form_with_error(isolated_admin_client):
    uuid = _create_server(isolated_admin_client, "grp_none")
    response = isolated_admin_client.post("/admin/servers/groups/add", data={"uuids": [uuid], "groups": []})
    assert response.status_code == 200
    assert "No group selected" in response.text


def test_groups_remove_removes_group_from_selected_servers(isolated_admin_client):
    uuid_a = _create_server(isolated_admin_client, "rmgrp_a")
    uuid_b = _create_server(isolated_admin_client, "rmgrp_b")
    isolated_admin_client.post(
        "/admin/servers/groups/add",
        data={"uuids": [uuid_a, uuid_b], "groups": ["temp_group"]},
        follow_redirects=False,
    )

    response = isolated_admin_client.post(
        "/admin/servers/groups/remove",
        data={"uuids": [uuid_a, uuid_b], "groups": ["temp_group"]},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/servers?msg=Groups+removed"

    edit_a = isolated_admin_client.get(f"/admin/servers/{uuid_a}/edit")
    assert 'value="temp_group" checked' not in edit_a.text


def test_groups_remove_without_selection_reshows_form_with_error(isolated_admin_client):
    uuid = _create_server(isolated_admin_client, "rmgrp_none")
    response = isolated_admin_client.post("/admin/servers/groups/remove", data={"uuids": [uuid], "groups": []})
    assert response.status_code == 200
    assert "No group selected" in response.text


# ================================================================================
# Audit log
# ================================================================================


def test_groups_add_is_audited_for_each_server_it_changed(isolated_admin_client, caplog):
    uuid_a = _create_server(isolated_admin_client, "grp_audit_a")
    uuid_b = _create_server(isolated_admin_client, "grp_audit_b")
    isolated_admin_client.post("/admin/servers/groups/add", data={"uuids": [uuid_b], "groups": ["sales"]})
    caplog.clear()

    with caplog.at_level(logging.INFO):
        isolated_admin_client.post("/admin/servers/groups/add", data={"uuids": [uuid_a, uuid_b], "groups": ["sales"]})

    [record] = audit_records(caplog)  # grp_audit_b already had the group: unchanged, not logged
    assert record.message == (
        f"Server 'grp_audit_a' updated by admin '{TEST_USERNAME}' (groups added) from {TESTCLIENT_HOST}: "
        'grp_authorized [] -> ["sales"]'
    )
    assert audit_fields(record)["event"] == "server.update"


def test_groups_remove_is_audited_for_each_server_it_changed(isolated_admin_client, caplog):
    uuid_a = _create_server(isolated_admin_client, "grp_audit_a")
    isolated_admin_client.post("/admin/servers/groups/add", data={"uuids": [uuid_a], "groups": ["sales"]})
    caplog.clear()

    with caplog.at_level(logging.INFO):
        isolated_admin_client.post("/admin/servers/groups/remove", data={"uuids": [uuid_a], "groups": ["sales"]})

    [record] = audit_records(caplog)
    assert "(groups removed)" in record.message
    assert audit_fields(record)["changes"] == {"grp_authorized": [["sales"], []]}


def test_groups_audit_leaves_the_authorized_servers_of_the_app_as_they_are(isolated_admin_client):
    """The snapshots read the servers from the database, they must not replace the app's own list"""
    uuid_a = _create_server(isolated_admin_client, "grp_audit_a")
    servers = isolated_admin_client.app.state.server.app_ctx.servers
    servers_dict = servers.servers_dict

    servers.audit_snapshot()

    assert servers.servers_dict is servers_dict
    assert uuid_a in servers.audit_snapshot()
