"""
Integration tests for the /admin/queries routes (Pytre_Server/routes/admin_queries.py) - list,
edit (hash exemption only), reload. Read-only against the real, shared queries folder (nothing
here writes .sql files), so `admin_client` (no per-test isolation needed) is used throughout.
"""

import logging
import re
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import Pytre_Core.kee as kee_module
from Pytre_Core.settings import Settings
from Pytre_Core.sql_query.file_parser import FileParser
from Pytre_Server.app import create_app
from Pytre_Server.query_catalog import QueryCatalog
from tests.integration.conftest import TEST_DB_PASSWORD, TEST_DB_PATH, TEST_USERNAME
from tests.resources.build_test_keepass import TEST_USER_PASSWORD

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


def test_reload_queries_keeps_the_db_pool_size(monkeypatch):
    """--db-pool-size lives in ServerResources, which a reload hands over to the new ServerState."""
    monkeypatch.setattr(kee_module, "KEE_FILE", TEST_DB_PATH)
    app = create_app(master_key=TEST_DB_PASSWORD, db_pool_size=7)
    client = TestClient(app)
    client.post("/admin/login", data={"username": TEST_USERNAME, "password": TEST_USER_PASSWORD})
    assert app.state.server.resources.db_pool_size == 7

    client.post("/admin/queries/reload")

    assert app.state.server.resources.db_pool_size == 7


def test_reload_queries_reports_a_failed_reload(admin_client, monkeypatch):
    def _raise(folder):
        raise ValueError("boom")

    monkeypatch.setattr(QueryCatalog, "load", staticmethod(_raise))

    response = admin_client.post("/admin/queries/reload")

    assert response.status_code == 200
    assert "Reload failed: boom" in response.text


def test_reload_keeps_the_queries_when_the_folder_is_missing(admin_client, tmp_path):
    """A folder unavailable for a moment (network share...) must not empty every user's query list."""
    admin_client.app.state.server.app_ctx.settings.queries_folder = tmp_path / "missing"

    response = admin_client.post("/admin/queries/reload", follow_redirects=False)
    response = admin_client.get(response.headers["location"])

    assert "Reload failed" in response.text
    assert TEST_QUERY_ID in response.text


# ------------------------------------------------------------------------------------------
# Startup
# ------------------------------------------------------------------------------------------
def test_server_starts_without_queries_when_the_folder_is_missing(monkeypatch, tmp_path, caplog):
    """The queries folder is set from the admin webui: the server must start anyway for it to be fixed."""
    missing_folder = tmp_path / "missing"
    original_load = Settings.load

    def load_with_a_missing_folder(self, reload: bool = False) -> None:
        original_load(self, reload)
        self.queries_folder = missing_folder

    monkeypatch.setattr(Settings, "load", load_with_a_missing_folder)
    monkeypatch.setattr(kee_module, "KEE_FILE", TEST_DB_PATH)

    with caplog.at_level(logging.WARNING):
        app = create_app(master_key=TEST_DB_PASSWORD)

    assert app.state.server.query_catalog.parsers == []
    assert any("No query loaded" in r.message and r.levelno == logging.WARNING for r in caplog.records)

    client = TestClient(app)
    client.post("/admin/login", data={"username": TEST_USERNAME, "password": TEST_USER_PASSWORD})
    response = client.get("/admin/queries")
    assert response.status_code == 200
    assert str(missing_folder) in response.text  # the load error is shown to the admin


def test_reload_queries_reports_a_build_error_in_the_flash_message(admin_client, tmp_path):
    """A query whose file parses fine but fails to build into a Query (here: an invalid parameter
    name) used to vanish silently from every user's /queries list, with nothing shown anywhere -
    reload must now surface it too, not just a load_files()-level parse failure."""
    (tmp_path / "BAD.sql").write_text("/*\ncode: BAD\n*/\nDECLARE\n@!X as int = 1  -- Desc|ENTRY\n;\nSELECT 1")
    admin_client.app.state.server.app_ctx.settings.queries_folder = tmp_path

    response = admin_client.post("/admin/queries/reload", follow_redirects=False)
    response = admin_client.get(response.headers["location"])

    assert "Queries reloaded (1 loaded, 1 error(s))" in response.text
    assert "BAD.sql" in response.text


def test_reload_queries_reports_no_errors_for_a_valid_query(admin_client, tmp_path):
    (tmp_path / "GOOD.sql").write_text("/*\ncode: GOOD\n*/\nSELECT 1")
    admin_client.app.state.server.app_ctx.settings.queries_folder = tmp_path

    response = admin_client.post("/admin/queries/reload", follow_redirects=False)
    response = admin_client.get(response.headers["location"])

    assert "Queries reloaded (1 loaded)" in response.text


def test_queries_list_shows_build_error_after_reload_without_the_flash_message(admin_client, tmp_path):
    """The build-error banner must persist on a plain GET /admin/queries, not just the one
    reload's flash redirect - it comes from the stored catalog, not a query-string message."""
    (tmp_path / "BAD.sql").write_text("/*\ncode: BAD\n*/\nDECLARE\n@!X as int = 1  -- Desc|ENTRY\n;\nSELECT 1")
    admin_client.app.state.server.app_ctx.settings.queries_folder = tmp_path
    admin_client.post("/admin/queries/reload")

    response = admin_client.get("/admin/queries")

    assert "BAD.sql" in response.text


# ------------------------------------------------------------------------------------------
# Edit (hash exemption only)
# ------------------------------------------------------------------------------------------
def test_query_edit_form_shows_every_field_read_only_except_hours(admin_client):
    response = admin_client.get(f"/admin/queries/{TEST_QUERY_ID}/edit")

    assert response.status_code == 200
    for field_id in ("code", "description", "hidden", "servers", "groups"):
        tag_match = re.search(rf'<input[^>]*id="{field_id}"[^>]*>', response.text)
        assert tag_match is not None, f"{field_id} input not found"
        assert "readonly" in tag_match.group(0) or "disabled" in tag_match.group(0)

    # "until" is display-only - a <div>, not a form control, filled in and never submitted by JS
    assert re.search(r'<div[^>]*id="until"[^>]*class="field-static"', response.text) is not None

    hours_tag = re.search(r'<input[^>]*id="hours"[^>]*>', response.text)
    assert hours_tag is not None
    assert "readonly" not in hours_tag.group(0)
    assert "disabled" not in hours_tag.group(0)


def test_query_edit_form_unknown_file_id_redirects_to_list_with_error(admin_client):
    response = admin_client.get("/admin/queries/DOES_NOT_EXIST/edit", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/queries?error=Query+not+found"


def _future_ts(hours: int = 24) -> int:
    return int((datetime.now(timezone.utc) + timedelta(hours=hours)).timestamp())


def _past_ts(hours: int = 24) -> int:
    return int((datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp())


def test_ignore_hash_set_then_reflected_in_list_and_form(admin_client):
    until_ts = _future_ts()

    response = admin_client.post(
        f"/admin/queries/{TEST_QUERY_ID}/ignore-hash", data={"until_ts": str(until_ts)}, follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/admin/queries?msg=Hash+check+for+{TEST_QUERY_ID}+updated"

    # the list/form don't render a human date themselves anymore - a browser converts data-ts /
    # data-until-ts to the viewer's local time client-side (see pytreFormatLocal in base.html)
    list_page = admin_client.get("/admin/queries")
    assert f'data-ts="{until_ts}"' in list_page.text

    edit_page = admin_client.get(f"/admin/queries/{TEST_QUERY_ID}/edit")
    assert f'data-until-ts="{until_ts}"' in edit_page.text
    assert f'value="{until_ts}"' in edit_page.text  # the hidden until_ts input, pre-filled


def test_ignore_hash_set_persists_a_brand_new_exemption(admin_client):
    """Regression: saving an exemption when none existed yet must actually be stored, not just
    report success without writing anything (the very first version of this route's rewrite did
    exactly that - a boolean condition inverted around `current_datetime is None`)."""
    until_ts = _future_ts()

    admin_client.post(f"/admin/queries/{TEST_QUERY_ID}/ignore-hash", data={"until_ts": str(until_ts)})

    stored = admin_client.app.state.server.resources.ignore_hash_until[TEST_QUERY_ID]
    assert stored == datetime.fromtimestamp(until_ts, tz=timezone.utc)


def test_ignore_hash_set_is_logged_with_the_admin_username(admin_client, caplog):
    until_ts = _future_ts()

    with caplog.at_level(logging.INFO):
        admin_client.post(f"/admin/queries/{TEST_QUERY_ID}/ignore-hash", data={"until_ts": str(until_ts)})

    assert any(
        f"Hash-check exemption for '{TEST_QUERY_ID}' set" in r.message and f"by admin '{TEST_USERNAME}'" in r.message
        for r in caplog.records
    )


def test_ignore_hash_set_same_timestamp_is_reported_unchanged(admin_client):
    until_ts = _future_ts()
    admin_client.post(f"/admin/queries/{TEST_QUERY_ID}/ignore-hash", data={"until_ts": str(until_ts)})

    response = admin_client.post(
        f"/admin/queries/{TEST_QUERY_ID}/ignore-hash", data={"until_ts": str(until_ts)}, follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/admin/queries?msg=Hash+exemption+for+{TEST_QUERY_ID}+unchanged"


def test_ignore_hash_set_empty_clears_the_exemption(admin_client):
    admin_client.post(f"/admin/queries/{TEST_QUERY_ID}/ignore-hash", data={"until_ts": str(_future_ts())})

    response = admin_client.post(
        f"/admin/queries/{TEST_QUERY_ID}/ignore-hash", data={"until_ts": ""}, follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/admin/queries?msg=Hash+exemption+for+{TEST_QUERY_ID}+cleared"
    assert TEST_QUERY_ID not in admin_client.app.state.server.resources.ignore_hash_until


def test_ignore_hash_clear_is_logged_with_the_admin_username(admin_client, caplog):
    admin_client.post(f"/admin/queries/{TEST_QUERY_ID}/ignore-hash", data={"until_ts": str(_future_ts())})

    with caplog.at_level(logging.INFO):
        admin_client.post(f"/admin/queries/{TEST_QUERY_ID}/ignore-hash", data={"until_ts": ""})

    assert any(
        f"Hash-check exemption for '{TEST_QUERY_ID}' cleared by admin '{TEST_USERNAME}'" in r.message
        for r in caplog.records
    )


def test_ignore_hash_set_past_timestamp_clears_the_exemption(admin_client):
    """N hours <= 0 (client-computed result not after now) must behave like clearing it - the
    server applies whatever instant it receives, past included."""
    admin_client.post(f"/admin/queries/{TEST_QUERY_ID}/ignore-hash", data={"until_ts": str(_future_ts())})

    response = admin_client.post(
        f"/admin/queries/{TEST_QUERY_ID}/ignore-hash", data={"until_ts": str(_past_ts())}, follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/admin/queries?msg=Hash+exemption+for+{TEST_QUERY_ID}+cleared"
    assert TEST_QUERY_ID not in admin_client.app.state.server.resources.ignore_hash_until


def test_ignore_hash_set_unknown_file_id_redirects_with_error(admin_client):
    response = admin_client.post(
        "/admin/queries/DOES_NOT_EXIST/ignore-hash", data={"until_ts": str(_future_ts())}, follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/queries?error=Query+not+found"


def test_ignore_hash_set_invalid_timestamp_redirects_with_error(admin_client):
    response = admin_client.post(
        f"/admin/queries/{TEST_QUERY_ID}/ignore-hash", data={"until_ts": "not-a-number"}, follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/queries?error=Invalid+date"
