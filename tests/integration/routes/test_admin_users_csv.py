"""
Integration tests for the /admin/users/{import,export} routes.
"""

import csv
import io
import logging

import pytest

from Pytre_Core.context import AppContext
from Pytre_Core.users import User, verify_password
from tests.conftest import audit_fields, audit_records
from tests.integration.conftest import TEST_DB_PASSWORD, TEST_USER_PASSWORD, TEST_USERNAME
from tests.integration.routes.conftest import TESTCLIENT_HOST

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
    csv_content = "id;display name;admin;groups\nimported_user;Imported User;0;\n"

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


# ================================================================================
# Password hashes in the export: only from the server itself
# ================================================================================


def _exported_rows(response) -> dict[str, dict[str, str]]:
    """Exported rows by user id."""
    rows = csv.DictReader(io.StringIO(response.content.decode("utf-8-sig")), delimiter=";")
    return {row["Id"]: row for row in rows}


def test_remote_export_has_no_password_hash(admin_client, caplog):
    with caplog.at_level(logging.INFO):
        response = admin_client.get("/admin/users/export")

    assert "Password Hash" not in _exported_rows(response)[TEST_USERNAME]
    [record] = audit_records(caplog, "user.export")
    assert record.levelno == logging.INFO
    assert record.message.startswith(f"Users exported without their password hashes by admin '{TEST_USERNAME}' from ")


def test_local_export_includes_password_hashes_and_logs_a_warning(local_admin_client, caplog):
    with caplog.at_level(logging.INFO):
        response = local_admin_client.get("/admin/users/export")

    assert verify_password(TEST_USER_PASSWORD, _exported_rows(response)[TEST_USERNAME]["Password Hash"])
    [record] = audit_records(caplog, "user.export")
    assert record.levelno == logging.WARNING
    assert record.message == f"Users exported with their password hashes by admin '{TEST_USERNAME}' from 127.0.0.1"
    assert audit_fields(record)["actor"] == TEST_USERNAME


def test_reimporting_a_remote_export_keeps_the_passwords(isolated_admin_client):
    exported = isolated_admin_client.get("/admin/users/export").content

    response = isolated_admin_client.post(
        "/admin/users/import", files={"file": ("users.csv", exported, "text/csv")}, follow_redirects=False
    )

    assert response.status_code == 303
    user = User(AppContext(master_key=TEST_DB_PASSWORD).users, username=TEST_USERNAME)
    assert verify_password(TEST_USER_PASSWORD, user.password_hash)


def test_users_list_warns_a_local_export_includes_the_password_hashes(local_admin_client):
    page = local_admin_client.get("/admin/users").text

    assert "This export includes the password hashes of the users." in page


def test_users_list_tells_a_remote_export_has_no_password_hash(admin_client):
    page = admin_client.get("/admin/users").text

    assert "Password hashes are only included when exporting from the server itself" in page
    assert "pytreConfirmDownload(this.dataset.confirm, '/admin/users/export')" in page  # asks before exporting


# ================================================================================
# Audit log
# ================================================================================


def test_import_is_audited_for_each_user_it_created_or_updated_then_summed_up(isolated_admin_client, caplog):
    isolated_admin_client.post(
        "/admin/users/new", data={"username": "unchanged", "display_name": "Same", "auth_type": "none", "password": ""}
    )
    csv_content = (
        "id;display name;admin;groups\n"
        "imported_user;Imported User;0;\n"
        f"{TEST_USERNAME};Renamed;1;\n"
        "unchanged;Same;0;\n"
    )
    caplog.clear()

    with caplog.at_level(logging.INFO):
        isolated_admin_client.post(
            "/admin/users/import", files={"file": ("users.csv", csv_content.encode("utf-8-sig"), "text/csv")}
        )

    events = [(audit_fields(r)["event"], audit_fields(r)["target"]) for r in audit_records(caplog)]
    assert events == [
        ("user.update", TEST_USERNAME),
        ("user.create", "imported_user"),
        ("user.import", None),
    ]  # "unchanged" left out
    [update] = audit_records(caplog, "user.update")
    assert audit_fields(update)["changes"] == {"display_name": ["Test User", "Renamed"]}
    assert "(CSV import)" in update.message
    [summary] = audit_records(caplog, "user.import")
    assert summary.message == f"Users imported by admin '{TEST_USERNAME}' (1 created, 1 updated) from {TESTCLIENT_HOST}"


def test_import_making_a_user_admin_is_audited_as_a_warning(isolated_admin_client, caplog):
    csv_content = "id;display name;admin;groups\nimported_admin;Imported Admin;1;\n"

    with caplog.at_level(logging.INFO):
        isolated_admin_client.post(
            "/admin/users/import", files={"file": ("users.csv", csv_content.encode("utf-8-sig"), "text/csv")}
        )

    [record] = audit_records(caplog, "user.create")
    assert record.levelno == logging.WARNING
    assert audit_fields(record)["changes"]["admin"] == [None, True]


def test_failed_import_is_not_audited(isolated_admin_client, caplog):
    csv_content = "id;description\nincomplete_user;Incomplete\n"

    with caplog.at_level(logging.INFO):
        isolated_admin_client.post(
            "/admin/users/import", files={"file": ("users.csv", csv_content.encode("utf-8-sig"), "text/csv")}
        )

    assert audit_records(caplog) == []
