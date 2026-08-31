"""
Integration tests for the /admin/users/{import,export} routes.
"""

import pytest

pytestmark = pytest.mark.integration


def test_import_form_renders(admin_client):
    response = admin_client.get("/admin/users/import")
    assert response.status_code == 200
    assert "<form" in response.text
    assert 'enctype="multipart/form-data"' in response.text


def test_export_returns_attachment_with_expected_content(admin_client):
    response = admin_client.get("/admin/users/export")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"] == 'attachment; filename="users_export.csv"'
    assert "test_user" in response.text


def test_import_creates_a_new_user(isolated_admin_client):
    csv_content = "id;description;admin;groups;login message\nimported_user;Imported User;0;;Welcome\n"

    response = isolated_admin_client.post(
        "/admin/users/import",
        files={"file": ("users.csv", csv_content.encode("utf-8-sig"), "text/csv")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/users?msg=Users+imported"

    list_page = isolated_admin_client.get("/admin/users")
    assert "imported_user" in list_page.text
    assert "Imported User" in list_page.text


def test_import_missing_required_column_reshows_form_with_error(isolated_admin_client):
    csv_content = "id;description\nincomplete_user;Incomplete\n"

    response = isolated_admin_client.post(
        "/admin/users/import", files={"file": ("users.csv", csv_content.encode("utf-8-sig"), "text/csv")}
    )
    assert response.status_code == 200
    assert "Missing columns" in response.text
