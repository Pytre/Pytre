"""
Integration tests for the /admin/users/attributes routes - adding/removing a custom-attribute
column across every user (mirrors Pytre_Client/app_users.py's AttribsDialog).
"""

import logging
import re

import pytest

from tests.conftest import audit_fields, audit_records
from tests.integration.conftest import TEST_USERNAME
from tests.integration.routes.conftest import TESTCLIENT_HOST

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
        data={"username": "attrib_user", "display_name": "", "auth_type": "none", "password": ""},
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


# ================================================================================
# Audit log
# ================================================================================


def test_add_attribute_is_audited(isolated_admin_client, caplog):
    with caplog.at_level(logging.INFO):
        isolated_admin_client.post("/admin/users/attributes/add", data={"name": "site"})

    [record] = audit_records(caplog)
    assert record.levelno == logging.INFO
    assert record.message == f"User attribute 'site' added by admin '{TEST_USERNAME}' from {TESTCLIENT_HOST}"
    assert audit_fields(record)["event"] == "user.attribute.add"
    assert audit_fields(record)["target"] == "site"


def test_remove_attributes_is_audited_as_a_warning_for_each_one(isolated_admin_client, caplog):
    """Removing an attribute erases its value for every user, and these values may filter the data of queries"""
    for name in ("site", "region"):
        isolated_admin_client.post("/admin/users/attributes/add", data={"name": name})
    caplog.clear()

    with caplog.at_level(logging.INFO):
        isolated_admin_client.post("/admin/users/attributes/remove", data={"names": ["site", "region"]})

    records = audit_records(caplog, "user.attribute.remove")
    assert [audit_fields(r)["target"] for r in records] == ["site", "region"]
    assert all(r.levelno == logging.WARNING for r in records)
    assert records[0].message == (
        f"User attribute 'site' removed from every user by admin '{TEST_USERNAME}' from {TESTCLIENT_HOST}"
    )
