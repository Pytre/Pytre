"""
Integration tests for the /admin/queries routes (Pytre_Server/routes/admin_queries.py) - list,
edit (hash exemption only), reload. Read-only against the real, shared queries folder (nothing
here writes .sql files), so `admin_client` (no per-test isolation needed) is used throughout.
"""

import re
from dataclasses import replace

import pytest

from Pytre_Core.sql_query.file_parser import FileParser
from Pytre_Server.query_catalog import QueryCatalog

pytestmark = pytest.mark.integration

TEST_QUERY_ID = "TEST_QUERY"


def _swap_catalog_with_one_query(client, tmp_path, sql_content: str, filename: str = "SYNTH.sql") -> str:
    """Replaces the running app's query catalog with a single synthetic query, built from a file
    written to `tmp_path` (never the shared tests/resources/queries folder) - lets a test control
    header fields (hide, servers, grp_authorized) without depending on/mutating the real catalog.
    Returns the new query's file_id."""
    sql_file = tmp_path / filename
    sql_file.write_text(sql_content, encoding="utf-8")
    parser = FileParser(sql_file)
    catalog = QueryCatalog(parsers=[parser], load_errors=[], by_file_id={parser.filename.stem: parser})
    client.app.state.server = replace(client.app.state.server, query_catalog=catalog)
    return parser.filename.stem


# ------------------------------------------------------------------------------------------
# List
# ------------------------------------------------------------------------------------------
def test_queries_list_shows_the_test_query(admin_client):
    response = admin_client.get("/admin/queries")

    assert response.status_code == 200
    assert TEST_QUERY_ID in response.text
    assert "requête synthétique pour les tests" in response.text  # avoid the apostrophe: Jinja escapes it


def test_queries_list_has_bulk_selection_checkboxes(admin_client):
    response = admin_client.get("/admin/queries")

    assert response.status_code == 200
    assert 'name="file_ids"' in response.text
    assert 'id="select-all"' in response.text


def test_queries_list_marks_a_hidden_query_with_muted_row_style(admin_client, tmp_path):
    _swap_catalog_with_one_query(
        admin_client, tmp_path, "/*\nCode : HIDDEN_Q\nDescription : hidden one\nHide : 1\n*/\nSELECT 1"
    )

    response = admin_client.get("/admin/queries")

    row_match = re.search(r'<tr[^>]*style="color: var\(--text-muted\);"[^>]*>.*?</tr>', response.text, re.DOTALL)
    assert row_match is not None
    assert "HIDDEN_Q" in row_match.group(0)
    assert ">Yes<" in row_match.group(0)


def test_queries_list_shows_no_restriction_text_when_unrestricted(admin_client, tmp_path):
    """servers/groups restriction columns must render blank (not "(all)"/"(unrestricted)") when
    a query's header doesn't declare either - see queries_list.html."""
    _swap_catalog_with_one_query(admin_client, tmp_path, "/*\nCode : OPEN_Q\nDescription : open\n*/\nSELECT 1")

    response = admin_client.get("/admin/queries")

    assert "OPEN_Q" in response.text
    assert "(all)" not in response.text
    assert "(unrestricted)" not in response.text


# ------------------------------------------------------------------------------------------
# Reload
# ------------------------------------------------------------------------------------------
def test_reload_queries_redirects_to_the_list(admin_client):
    """Post/Redirect/Get: the browser must land back on a GET-able URL, never stay parked on
    this POST-only one (a page refresh there would resubmit the POST)."""
    response = admin_client.post("/admin/queries/reload", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/admin/queries?msg=")

    response = admin_client.get(response.headers["location"])
    assert "Queries reloaded" in response.text
    assert TEST_QUERY_ID in response.text  # rebuilt from the real folder, not emptied


def test_reload_queries_reports_a_failed_reload(admin_client, monkeypatch):
    def _raise(folder):
        raise ValueError("boom")

    monkeypatch.setattr(QueryCatalog, "load", staticmethod(_raise))

    response = admin_client.post("/admin/queries/reload")

    assert response.status_code == 200
    assert "Reload failed: boom" in response.text


# ------------------------------------------------------------------------------------------
# Edit (hash exemption only)
# ------------------------------------------------------------------------------------------
def test_query_edit_form_shows_every_field_read_only_except_the_hash_date(admin_client):
    response = admin_client.get(f"/admin/queries/{TEST_QUERY_ID}/edit")

    assert response.status_code == 200
    for field_id in ("code", "description", "hidden", "servers", "groups"):
        tag_match = re.search(rf'<input[^>]*id="{field_id}"[^>]*>', response.text)
        assert tag_match is not None, f"{field_id} input not found"
        assert "readonly" in tag_match.group(0) or "disabled" in tag_match.group(0)

    until_tag = re.search(r'<input[^>]*id="until"[^>]*>', response.text)
    assert until_tag is not None
    assert "readonly" not in until_tag.group(0)
    assert "disabled" not in until_tag.group(0)


def test_query_edit_form_unknown_file_id_redirects_to_list_with_error(admin_client):
    response = admin_client.get("/admin/queries/DOES_NOT_EXIST/edit", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/queries?error=Query+not+found"


def test_ignore_hash_set_then_reflected_in_list_and_form(admin_client):
    response = admin_client.post(
        f"/admin/queries/{TEST_QUERY_ID}/ignore-hash", data={"until": "2099-01-01"}, follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/admin/queries?msg=Hash+check+for+{TEST_QUERY_ID}+ignored+until+2099-01-01"

    list_page = admin_client.get("/admin/queries")
    assert "2099-01-01" in list_page.text

    edit_page = admin_client.get(f"/admin/queries/{TEST_QUERY_ID}/edit")
    assert 'value="2099-01-01"' in edit_page.text
    assert 'form="clear-form"' in edit_page.text  # Clear button only shows up once an exemption exists


def test_ignore_hash_set_unknown_file_id_redirects_with_error(admin_client):
    response = admin_client.post(
        "/admin/queries/DOES_NOT_EXIST/ignore-hash", data={"until": "2099-01-01"}, follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/queries?error=Query+not+found"


def test_ignore_hash_set_invalid_date_redirects_with_error(admin_client):
    response = admin_client.post(
        f"/admin/queries/{TEST_QUERY_ID}/ignore-hash", data={"until": "not-a-date"}, follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/queries?error=Invalid+date"


def test_clear_hash_exemption_removes_it(admin_client):
    admin_client.post(f"/admin/queries/{TEST_QUERY_ID}/ignore-hash", data={"until": "2099-01-01"})

    response = admin_client.post(f"/admin/queries/{TEST_QUERY_ID}/clear-hash-exemption", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == f"/admin/queries?msg=Hash+exemption+for+{TEST_QUERY_ID}+cleared"

    edit_page = admin_client.get(f"/admin/queries/{TEST_QUERY_ID}/edit")
    assert 'form="clear-form"' not in edit_page.text


def test_clear_hash_exemption_on_unset_query_is_a_noop(admin_client):
    response = admin_client.post(f"/admin/queries/{TEST_QUERY_ID}/clear-hash-exemption", follow_redirects=False)

    assert response.status_code == 303
