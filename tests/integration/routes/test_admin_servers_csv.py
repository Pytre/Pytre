"""
Integration tests for the /admin/servers/{import,export} routes.
"""

import pytest

pytestmark = pytest.mark.integration

_CSV_HEADER = (
    "id;description;type;user;password;charset;database;host;port;server;login_timeout;timeout;"
    "trust_server_certificate;grp_authorized"
)


def test_import_form_renders(admin_client):
    response = admin_client.get("/admin/servers/import")
    assert response.status_code == 200
    assert "<form" in response.text
    assert 'enctype="multipart/form-data"' in response.text


def test_export_returns_attachment_with_expected_content(admin_client):
    response = admin_client.get("/admin/servers/export")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"] == 'attachment; filename="servers_export.csv"'
    assert "test" in response.text


def test_import_creates_a_new_server(isolated_admin_client):
    csv_content = (
        f"{_CSV_HEADER}\n"
        "imported_server;Imported Server;mssql;sa;pwd;UTF-8;master;127.0.0.1;1433;;60;300;false;\n"
    )

    response = isolated_admin_client.post(
        "/admin/servers/import",
        files={"file": ("servers.csv", csv_content.encode("utf-8-sig"), "text/csv")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/servers?msg=Servers+imported"

    list_page = isolated_admin_client.get("/admin/servers")
    assert "imported_server" in list_page.text
    assert "Imported Server" in list_page.text


def test_import_missing_required_column_reshows_form_with_error(isolated_admin_client):
    csv_content = "id;description\nincomplete_server;Incomplete\n"

    response = isolated_admin_client.post(
        "/admin/servers/import", files={"file": ("servers.csv", csv_content.encode("utf-8-sig"), "text/csv")}
    )
    assert response.status_code == 200
    assert "Missing columns" in response.text
