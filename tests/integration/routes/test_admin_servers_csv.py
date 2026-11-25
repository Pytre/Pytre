"""
Integration tests for the /admin/servers/{import,export} routes.
"""

import csv
import io
import logging
from unittest.mock import AsyncMock, Mock

import pytest

from Pytre_Core.context import AppContext
from Pytre_Server.server_state import _DriverEntry
from tests.conftest import audit_fields, audit_records
from tests.integration.conftest import TEST_DB_PASSWORD, TEST_USERNAME
from tests.integration.routes.conftest import TESTCLIENT_HOST
from tests.resources.build_test_keepass import TEST_SQL_SERVER

pytestmark = pytest.mark.integration

_CSV_HEADER = (
    "id;description;type;user;password;charset;database;host;port;server;login_timeout;timeout;"
    "trust_certificate;grp_authorized"
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
    [record] = audit_records(caplog, "server.export")
    assert record.levelno == logging.INFO
    assert record.message.startswith(f"Servers exported without their passwords by admin '{TEST_USERNAME}' from ")


def test_local_export_includes_passwords_and_logs_a_warning(local_admin_client, caplog):
    with caplog.at_level(logging.INFO):
        response = local_admin_client.get("/admin/servers/export")

    assert _exported_rows(response)[TEST_SQL_SERVER["id"]]["Password"] == TEST_SQL_SERVER["password"]
    [record] = audit_records(caplog, "server.export")
    assert record.levelno == logging.WARNING
    assert record.message == f"Servers exported with their passwords by admin '{TEST_USERNAME}' from 127.0.0.1"
    assert audit_fields(record)["actor"] == TEST_USERNAME


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
# Import: applied at once
# ================================================================================


def test_import_applies_the_imported_servers_to_the_next_executions_at_once(isolated_admin_client):
    """The servers executions look up are replaced, and every driver dropped: the next ones use the new values"""
    content = _export_with(isolated_admin_client, TEST_SQL_SERVER["id"], Max_Concurrency="7")
    state = isolated_admin_client.app.state.server
    fake_driver = Mock()
    fake_driver.disconnect = AsyncMock()
    state.resources.driver_entries[TEST_SQL_SERVER["id"]] = _DriverEntry(driver=fake_driver)

    isolated_admin_client.post("/admin/servers/import", files={"file": ("servers.csv", content, "text/csv")})

    assert state.app_ctx.servers.servers_dict[TEST_SQL_SERVER["id"]].max_concurrency == 7
    assert state.resources.driver_entries == {}


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


# ================================================================================
# Audit log
# ================================================================================


def test_import_is_audited_for_each_server_it_created_or_updated_then_summed_up(isolated_admin_client, caplog):
    content = _export_with(isolated_admin_client, TEST_SQL_SERVER["id"], Description="Renamed")
    content += "imported_server;Imported;mssql;sa;pwd;UTF-8;master;127.0.0.1;1433;;60;300;30;false;\n".encode()
    caplog.clear()

    with caplog.at_level(logging.INFO):
        isolated_admin_client.post("/admin/servers/import", files={"file": ("servers.csv", content, "text/csv")})

    events = [(audit_fields(r)["event"], audit_fields(r)["target"]) for r in audit_records(caplog)]
    assert events == [
        ("server.update", TEST_SQL_SERVER["id"]),
        ("server.create", "imported_server"),
        ("server.import", None),
    ]  # the other server of the export is unchanged: left out
    [update] = audit_records(caplog, "server.update")
    assert audit_fields(update)["changes"] == {"description": [TEST_SQL_SERVER["description"], "Renamed"]}
    assert "(CSV import)" in update.message
    [create] = audit_records(caplog, "server.create")
    assert audit_fields(create)["changes"]["password"] == "changed"
    assert "pwd" not in create.message
    [summary] = audit_records(caplog, "server.import")
    assert summary.message == f"Servers imported by admin '{TEST_USERNAME}' (1 created, 1 updated) from {TESTCLIENT_HOST}"


def test_refused_import_is_not_audited(isolated_admin_client, caplog):
    content = _export_with(isolated_admin_client, TEST_SQL_SERVER["id"], Host="attacker.example.com")
    caplog.clear()

    with caplog.at_level(logging.INFO):
        isolated_admin_client.post("/admin/servers/import", files={"file": ("servers.csv", content, "text/csv")})

    assert audit_records(caplog) == []
