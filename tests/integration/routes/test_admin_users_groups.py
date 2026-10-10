"""
Integration tests for the /admin/users/groups/{add,remove} routes - bulk group assignment
across multiple selected users (mirrors Pytre_Client/app_users.py's GroupsDialog).
"""

import re

import pytest

pytestmark = pytest.mark.integration


def _find_uuid(html: str, username: str) -> str:
    for row in html.split("<tr>"):
        if f">{username}<" in row:
            match = re.search(r"/admin/users/([0-9a-f-]+)/edit", row)
            assert match is not None
            return match.group(1)
    raise AssertionError(f"{username} not found in list page")


def _create_user(client, username: str) -> str:
    client.post(
        "/admin/users/new",
        data={"username": username, "title": "", "auth_type": "none", "password": ""},
        follow_redirects=False,
    )
    list_page = client.get("/admin/users")
    return _find_uuid(list_page.text, username)


def test_groups_add_form_redirects_without_selection(isolated_admin_client):
    response = isolated_admin_client.get("/admin/users/groups/add", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/users?error=No+user+selected"


def test_groups_remove_form_redirects_without_selection(isolated_admin_client):
    response = isolated_admin_client.get("/admin/users/groups/remove", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/users?error=No+user+selected"


def test_groups_add_form_renders_with_selection(isolated_admin_client):
    uuid = _create_user(isolated_admin_client, "grp_target")
    response = isolated_admin_client.get("/admin/users/groups/add", params={"uuids": [uuid]})
    assert response.status_code == 200
    assert "<form" in response.text


def test_groups_add_applies_existing_and_new_groups_to_multiple_users(isolated_admin_client):
    from Pytre_Core.context import AppContext

    uuid_a = _create_user(isolated_admin_client, "grp_a")
    uuid_b = _create_user(isolated_admin_client, "grp_b")

    # "existing" group must actually exist first - the seeded test DB has none by default
    isolated_admin_client.post(
        "/admin/users/groups/add", data={"uuids": [uuid_a], "groups": ["sales"]}, follow_redirects=False
    )

    response = isolated_admin_client.post(
        "/admin/users/groups/add",
        data={"uuids": [uuid_a, uuid_b], "groups": ["sales", "brand_new_group"]},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/users?msg=Groups+assigned"

    app_ctx = AppContext(master_key="password")
    for uuid in (uuid_a, uuid_b):
        user = app_ctx.users.find_user_by_uuid(uuid)
        assert user is not None
        assert "sales" in user.groups
        assert "brand_new_group" in user.groups


def test_groups_add_without_any_group_reshows_form_with_error(isolated_admin_client):
    uuid = _create_user(isolated_admin_client, "grp_none")
    response = isolated_admin_client.post("/admin/users/groups/add", data={"uuids": [uuid], "groups": []})
    assert response.status_code == 200
    assert "No group selected" in response.text


def test_groups_remove_removes_group_from_selected_users(isolated_admin_client):
    uuid_a = _create_user(isolated_admin_client, "rmgrp_a")
    uuid_b = _create_user(isolated_admin_client, "rmgrp_b")
    isolated_admin_client.post(
        "/admin/users/groups/add",
        data={"uuids": [uuid_a, uuid_b], "groups": ["temp_group"]},
        follow_redirects=False,
    )

    response = isolated_admin_client.post(
        "/admin/users/groups/remove", data={"uuids": [uuid_a, uuid_b], "groups": ["temp_group"]}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/users?msg=Groups+removed"

    edit_a = isolated_admin_client.get(f"/admin/users/{uuid_a}/edit")
    assert 'value="temp_group" checked' not in edit_a.text


def test_groups_remove_without_selection_reshows_form_with_error(isolated_admin_client):
    uuid = _create_user(isolated_admin_client, "rmgrp_none")
    response = isolated_admin_client.post("/admin/users/groups/remove", data={"uuids": [uuid], "groups": []})
    assert response.status_code == 200
    assert "No group selected" in response.text
