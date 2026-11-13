"""
Integration tests for the /admin/servers/{import,export} routes.
"""

import csv
import io
import logging

import pytest

from Pytre_Core.context import AppContext
from tests.integration.conftest import TEST_DB_PASSWORD, TEST_USERNAME
from tests.resources.build_test_keepass import TEST_SQL_SERVER

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


# ================================================================================
# Passwords in the export: only from the server itself
# ================================================================================


def _exported_rows(response) -> dict[str, dict[str, str]]:
    """Exported rows by server id."""
    rows = csv.DictReader(io.StringIO(response.content.decode("utf-8-sig")), delimiter=";")
    return {row["Id"]: row for row in rows}


def _stored_server(server_id: str):
    """The server as stored in the KeePass database the test client uses."""
    app_ctx = AppContext(master_key=TEST_DB_PASSWORD)
    return app_ctx.servers.get_all_servers(reload=True)[server_id]


def test_remote_export_leaves_passwords_blank(admin_client, caplog):
    with caplog.at_level(logging.INFO):
        response = admin_client.get("/admin/servers/export")

    assert response.status_code == 200
    assert _exported_rows(response)[TEST_SQL_SERVER["id"]]["Password"] == ""
    assert any(
        r.levelno == logging.INFO
        and r.message == f"Servers exported without their passwords by admin '{TEST_USERNAME}'"
        for r in caplog.records
    )


def test_local_export_includes_passwords_and_logs_a_warning(local_admin_client, caplog):
    with caplog.at_level(logging.INFO):
        response = local_admin_client.get("/admin/servers/export")

    assert _exported_rows(response)[TEST_SQL_SERVER["id"]]["Password"] == TEST_SQL_SERVER["password"]
    assert any(
        r.levelno == logging.WARNING
        and r.message == f"Servers exported with their passwords by admin '{TEST_USERNAME}'"
        for r in caplog.records
    )


def test_reimporting_a_remote_export_keeps_the_passwords(isolated_admin_client):
    exported = isolated_admin_client.get("/admin/servers/export").content

    response = isolated_admin_client.post(
        "/admin/servers/import", files={"file": ("servers.csv", exported, "text/csv")}, follow_redirects=False
    )

    assert response.status_code == 303
    assert _stored_server(TEST_SQL_SERVER["id"]).password == TEST_SQL_SERVER["password"]


# ================================================================================
# Import: a target change needs the password
# ================================================================================


def _export_with(client, server_id: str, **changes: str) -> bytes:
    """The remote export of `client`, with `changes` applied to the row of `server_id`."""
    exported = client.get("/admin/servers/export").content.decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(exported), delimiter=";"))
    for row in rows:
        if row["Id"] == server_id:
            row.update(changes)

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(rows[0]), delimiter=";")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8-sig")


def test_import_refuses_a_new_host_without_the_password_and_changes_nothing(isolated_admin_client):
    """A remote export has no password: importing it back with another host would send the stored password there."""
    content = _export_with(isolated_admin_client, TEST_SQL_SERVER["id"], Host="attacker.example.com")

    files = {"file": ("servers.csv", content, "text/csv")}
    response = isolated_admin_client.post("/admin/servers/import", files=files)

    assert response.status_code == 200
    assert "requires its password" in response.text
    assert "Nothing was imported" in response.text
    assert _stored_server(TEST_SQL_SERVER["id"]).host == TEST_SQL_SERVER["host"]


def test_import_accepts_a_new_host_with_the_password(isolated_admin_client):
    content = _export_with(isolated_admin_client, TEST_SQL_SERVER["id"], Host="new.example.com", Password="n3w")

    response = isolated_admin_client.post(
        "/admin/servers/import", files={"file": ("servers.csv", content, "text/csv")}, follow_redirects=False
    )

    assert response.status_code == 303
    server = _stored_server(TEST_SQL_SERVER["id"])
    assert (server.host, server.password) == ("new.example.com", "n3w")


# ================================================================================
# Export confirmation
# ================================================================================


def test_servers_list_warns_a_local_export_includes_the_passwords(local_admin_client):
    page = local_admin_client.get("/admin/servers").text

    assert "This export includes the passwords of the servers, in clear text." in page


def test_servers_list_tells_a_remote_export_has_no_password(admin_client):
    page = admin_client.get("/admin/servers").text

    assert "Passwords are only included when exporting from the server itself" in page
    assert "pytreConfirmDownload(this.dataset.confirm, '/admin/servers/export')" in page  # asks before exporting
