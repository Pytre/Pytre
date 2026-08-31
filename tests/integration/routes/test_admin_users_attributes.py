"""
Integration tests for the /admin/users/attributes routes - adding/removing a custom-attribute
column across every user (mirrors Pytre_Client/app_users.py's AttribsDialog).
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


def test_attributes_page_renders(admin_client):
    response = admin_client.get("/admin/users/attributes")
    assert response.status_code == 200
    assert "<form" in response.text


def test_add_attribute_appears_on_every_user_form(isolated_admin_client):
    response = isolated_admin_client.post(
        "/admin/users/attributes/add", data={"name": "service"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/users?msg=Attribute+added"

    new_form = isolated_admin_client.get("/admin/users/new")
    assert 'name="attrib__service"' in new_form.text

    list_page = isolated_admin_client.get("/admin/users")
    assert "service" in list_page.text


def test_add_attribute_created_blank_for_existing_users(isolated_admin_client):
    from Pytre_Core.context import AppContext

    isolated_admin_client.post(
        "/admin/users/new",
        data={"username": "attrib_user", "title": "", "msg_login": "", "auth_type": "none", "password": ""},
        follow_redirects=False,
    )
    isolated_admin_client.post("/admin/users/attributes/add", data={"name": "region"}, follow_redirects=False)

    list_page = isolated_admin_client.get("/admin/users")
    uuid = _find_uuid(list_page.text, "attrib_user")

    edit_page = isolated_admin_client.get(f"/admin/users/{uuid}/edit")
    assert 'id="attrib__region"' in edit_page.text

    app_ctx = AppContext(master_key="password")
    user = app_ctx.users.find_user_by_uuid(uuid)
    assert user is not None
    assert user.attribs_cust.get("region") == ""


def test_add_attribute_blank_name_reshows_form_with_error(isolated_admin_client):
    response = isolated_admin_client.post("/admin/users/attributes/add", data={"name": "   "})
    assert response.status_code == 200
    assert "required" in response.text.lower()


def test_add_attribute_rejects_reserved_key(isolated_admin_client):
    response = isolated_admin_client.post("/admin/users/attributes/add", data={"name": "password"})
    assert response.status_code == 200
    assert "reserved internally" in response.text


def test_add_attribute_rejects_existing_name_case_insensitive(isolated_admin_client):
    isolated_admin_client.post("/admin/users/attributes/add", data={"name": "Service"}, follow_redirects=False)
    response = isolated_admin_client.post("/admin/users/attributes/add", data={"name": "service"})
    assert response.status_code == 200
    assert "already exists" in response.text


def test_remove_attribute_disappears_everywhere(isolated_admin_client):
    isolated_admin_client.post("/admin/users/attributes/add", data={"name": "temp_attrib"}, follow_redirects=False)

    response = isolated_admin_client.post(
        "/admin/users/attributes/remove", data={"names": ["temp_attrib"]}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/users?msg=Attribute(s)+removed"

    new_form = isolated_admin_client.get("/admin/users/new")
    assert 'name="attrib__temp_attrib"' not in new_form.text


def test_remove_attribute_without_selection_reshows_form_with_error(isolated_admin_client):
    isolated_admin_client.post("/admin/users/attributes/add", data={"name": "keep_me"}, follow_redirects=False)
    response = isolated_admin_client.post("/admin/users/attributes/remove", data={"names": []})
    assert response.status_code == 200
    assert "No attribute selected" in response.text
