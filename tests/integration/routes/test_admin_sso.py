"""
Integration tests for the /admin/sso routes (Pytre_Server/routes/admin_sso.py) - list, create,
edit, delete, bulk delete. Uses `isolated_admin_client` for anything that writes to the KeePass
database.
"""

import logging
import re

import pytest

from tests.integration.conftest import TEST_USERNAME

pytestmark = pytest.mark.integration


def _find_uuid(html: str, marker: str) -> str:
    """Extracts a provider's edit-link uuid from the rendered list page, by scanning the row that
    contains `marker` (its issuer, unique per test, is the simplest unambiguous marker)."""
    for row in html.split("<tr>"):
        if f">{marker}<" in row:
            match = re.search(r"/admin/sso/([0-9a-f-]+)/edit", row)
            assert match is not None
            return match.group(1)
    raise AssertionError(f"{marker} not found in list page")


def _new_provider_data(issuer: str, **overrides) -> dict:
    data = {
        "label": "",
        "issuer": issuer,
        "client_id": "pytre-client",
        "client_secret": "s3cret",
        "scopes": "openid",
        "username_claim": "preferred_username",
        "public_base_url": "https://pytre.example.com",
        "enabled": "1",
    }
    data.update(overrides)
    return data


def test_sso_list_renders_empty(admin_client):
    response = admin_client.get("/admin/sso")
    assert response.status_code == 200
    assert "<table" in response.text


def test_create_provider_succeeds_and_appears_in_list(isolated_admin_client):
    response = isolated_admin_client.post(
        "/admin/sso/new",
        data=_new_provider_data("https://idp1.example.com", label="Company SSO"),
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/sso?msg=Provider+saved"

    list_page = isolated_admin_client.get("/admin/sso")
    assert "Company SSO" in list_page.text
    assert "https://idp1.example.com" in list_page.text
    assert ">Yes<" in list_page.text  # enabled and usable, both default true here


def test_create_provider_is_logged_with_the_admin_username(isolated_admin_client, caplog):
    with caplog.at_level(logging.INFO):
        isolated_admin_client.post("/admin/sso/new", data=_new_provider_data("https://idp-logged.example.com"))

    assert any(
        f"OIDC provider 'https://idp-logged.example.com' saved by admin '{TEST_USERNAME}'" in r.message
        for r in caplog.records
    )


def test_create_provider_round_trip_keeps_each_field_distinct(isolated_admin_client):
    data = _new_provider_data(
        "https://idp-roundtrip.example.com",
        label="LABEL_VAL",
        client_id="CLIENTID_VAL",
        client_secret="SECRET_VAL",
        scopes="SCOPES_VAL",
        username_claim="CLAIM_VAL",
        public_base_url="https://base.example.com",
    )
    response = isolated_admin_client.post("/admin/sso/new", data=data, follow_redirects=False)
    assert response.status_code == 303

    list_page = isolated_admin_client.get("/admin/sso")
    uuid = _find_uuid(list_page.text, "https://idp-roundtrip.example.com")
    edit_page = isolated_admin_client.get(f"/admin/sso/{uuid}/edit").text

    for field in ("label", "issuer", "client_id", "scopes", "username_claim", "public_base_url"):
        match = re.search(rf'id="{field}"[^>]*value="([^"]*)"', edit_page)
        assert match is not None, f"{field} input not found"
        assert match.group(1) == data[field], f"{field} did not round-trip correctly: got {match.group(1)!r}"


def test_create_provider_blank_issuer_reports_error(isolated_admin_client):
    response = isolated_admin_client.post("/admin/sso/new", data=_new_provider_data(""))
    assert response.status_code == 200
    match = re.search(r'<p class="error form-error">(.*?)</p>', response.text)
    assert match is not None
    assert "issuer" in match.group(1)


def test_create_provider_blank_client_id_reports_error(isolated_admin_client):
    data = _new_provider_data("https://idp-blank-cid.example.com", client_id="")
    response = isolated_admin_client.post("/admin/sso/new", data=data)
    assert response.status_code == 200
    match = re.search(r'<p class="error form-error">(.*?)</p>', response.text)
    assert match is not None
    assert "client id" in match.group(1)


def test_create_provider_duplicate_issuer_reports_error(isolated_admin_client):
    data = _new_provider_data("https://idp-dup.example.com")
    isolated_admin_client.post("/admin/sso/new", data=data, follow_redirects=False)

    response = isolated_admin_client.post("/admin/sso/new", data=data)
    assert response.status_code == 200
    assert "<form" in response.text  # re-rendered form, not a redirect
    match = re.search(r'<p class="error form-error">(.*?)</p>', response.text)
    assert match is not None
    assert "idp-dup.example.com" in match.group(1)


def test_edit_provider_updates_fields(isolated_admin_client):
    isolated_admin_client.post(
        "/admin/sso/new",
        data=_new_provider_data("https://idp-edit.example.com", label="Before"),
        follow_redirects=False,
    )
    list_page = isolated_admin_client.get("/admin/sso")
    uuid = _find_uuid(list_page.text, "https://idp-edit.example.com")

    response = isolated_admin_client.post(
        f"/admin/sso/{uuid}/edit",
        data=_new_provider_data("https://idp-edit.example.com", label="After", scopes="openid email"),
        follow_redirects=False,
    )
    assert response.status_code == 303

    edit_page = isolated_admin_client.get(f"/admin/sso/{uuid}/edit").text
    assert 'value="After"' in edit_page
    assert 'value="openid email"' in edit_page


def test_edit_provider_preserves_client_secret_when_field_left_blank(isolated_admin_client):
    """Regression guard: apply_to() must only overwrite client_secret when one was submitted -
    the form never redisplays a stored secret, so leaving it blank on edit is the normal case."""
    isolated_admin_client.post(
        "/admin/sso/new",
        data=_new_provider_data("https://idp-keep-secret.example.com", client_secret="original-secret"),
        follow_redirects=False,
    )
    list_page = isolated_admin_client.get("/admin/sso")
    uuid = _find_uuid(list_page.text, "https://idp-keep-secret.example.com")

    response = isolated_admin_client.post(
        f"/admin/sso/{uuid}/edit",
        data=_new_provider_data("https://idp-keep-secret.example.com", label="Updated", client_secret=""),
        follow_redirects=False,
    )
    assert response.status_code == 303

    from Pytre_Core.context import AppContext

    app_ctx = AppContext(master_key="password")
    provider = app_ctx.oidc_providers.find_provider_by_uuid(uuid)
    assert provider is not None
    assert provider.client_secret == "original-secret"


def test_edit_unknown_uuid_redirects_to_list_with_error(isolated_admin_client):
    response = isolated_admin_client.get(
        "/admin/sso/00000000-0000-0000-0000-000000000000/edit", follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/sso?error=Provider+not+found"


def test_delete_single_provider(isolated_admin_client):
    isolated_admin_client.post(
        "/admin/sso/new", data=_new_provider_data("https://idp-delete.example.com"), follow_redirects=False
    )
    list_page = isolated_admin_client.get("/admin/sso")
    uuid = _find_uuid(list_page.text, "https://idp-delete.example.com")

    response = isolated_admin_client.post(f"/admin/sso/{uuid}/delete", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/sso?msg=Provider+deleted"

    list_page_after = isolated_admin_client.get("/admin/sso")
    assert "idp-delete.example.com" not in list_page_after.text


def test_delete_unknown_uuid_redirects_with_error(isolated_admin_client):
    response = isolated_admin_client.post(
        "/admin/sso/00000000-0000-0000-0000-000000000000/delete", follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/sso?error=Provider+not+found"


def test_delete_single_provider_is_logged_with_the_admin_username(isolated_admin_client, caplog):
    isolated_admin_client.post("/admin/sso/new", data=_new_provider_data("https://idp-delete-logged.example.com"))
    list_page = isolated_admin_client.get("/admin/sso")
    uuid = _find_uuid(list_page.text, "https://idp-delete-logged.example.com")

    with caplog.at_level(logging.INFO):
        isolated_admin_client.post(f"/admin/sso/{uuid}/delete")

    assert any(
        f"OIDC provider 'https://idp-delete-logged.example.com' deleted by admin '{TEST_USERNAME}'" in r.message
        for r in caplog.records
    )


def test_bulk_delete_removes_all_selected(isolated_admin_client):
    """Also a regression guard for a real bug found in review: sso_delete_bulk() once called
    `provider.delete` without parentheses - a no-op that still counted as a success, so this
    would report both providers deleted while leaving them untouched in the list below."""
    for issuer in ("https://idp-bulk-a.example.com", "https://idp-bulk-b.example.com"):
        isolated_admin_client.post("/admin/sso/new", data=_new_provider_data(issuer), follow_redirects=False)
    list_page = isolated_admin_client.get("/admin/sso")
    uuid_a = _find_uuid(list_page.text, "https://idp-bulk-a.example.com")
    uuid_b = _find_uuid(list_page.text, "https://idp-bulk-b.example.com")

    response = isolated_admin_client.post(
        "/admin/sso/delete", data={"uuids": [uuid_a, uuid_b]}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/sso?msg=2+provider(s)+deleted"

    list_page_after = isolated_admin_client.get("/admin/sso")
    assert "idp-bulk-a" not in list_page_after.text
    assert "idp-bulk-b" not in list_page_after.text


def test_bulk_delete_is_logged_per_provider_with_the_admin_username(isolated_admin_client, caplog):
    for issuer in ("https://idp-bulk-log-a.example.com", "https://idp-bulk-log-b.example.com"):
        isolated_admin_client.post("/admin/sso/new", data=_new_provider_data(issuer))
    list_page = isolated_admin_client.get("/admin/sso")
    uuid_a = _find_uuid(list_page.text, "https://idp-bulk-log-a.example.com")
    uuid_b = _find_uuid(list_page.text, "https://idp-bulk-log-b.example.com")

    with caplog.at_level(logging.INFO):
        isolated_admin_client.post("/admin/sso/delete", data={"uuids": [uuid_a, uuid_b]})

    assert any(
        f"OIDC provider 'https://idp-bulk-log-a.example.com' deleted by admin '{TEST_USERNAME}' (bulk)" in r.message
        for r in caplog.records
    )
    assert any(
        f"OIDC provider 'https://idp-bulk-log-b.example.com' deleted by admin '{TEST_USERNAME}' (bulk)" in r.message
        for r in caplog.records
    )


def test_bulk_delete_reports_partial_failure(isolated_admin_client):
    isolated_admin_client.post(
        "/admin/sso/new", data=_new_provider_data("https://idp-bulk-real.example.com"), follow_redirects=False
    )
    list_page = isolated_admin_client.get("/admin/sso")
    uuid_real = _find_uuid(list_page.text, "https://idp-bulk-real.example.com")
    fake_uuid = "00000000-0000-0000-0000-000000000000"

    response = isolated_admin_client.post(
        "/admin/sso/delete", data={"uuids": [uuid_real, fake_uuid]}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/sso?msg=1+provider(s)+deleted,+1+error(s)"


# ------------------------------------------------------------------------------------------
# Reload
# ------------------------------------------------------------------------------------------
def test_reload_providers_redirects_to_the_list(isolated_admin_client):
    """Post/Redirect/Get: the browser must land back on a GET-able URL, never stay parked on
    this POST-only one (a page refresh there would resubmit the POST)."""
    response = isolated_admin_client.post("/admin/sso/reload", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/admin/sso?msg=")

    response = isolated_admin_client.get(response.headers["location"])
    assert "OIDC providers reloaded (0 loaded)" in response.text


def test_reload_providers_reports_a_failed_reload(isolated_admin_client, monkeypatch):
    """Only the explicit reload=True call fails - the fallback re-render right after (which
    calls get_all_providers() with its reload=False default) must still succeed so the error
    page itself can be shown."""
    from Pytre_Core.oidc_providers import OidcProviders

    original = OidcProviders.get_all_providers

    def _raise_on_reload(self, reload=False):
        if reload:
            raise RuntimeError("boom")
        return original(self, reload=reload)

    monkeypatch.setattr(OidcProviders, "get_all_providers", _raise_on_reload)

    response = isolated_admin_client.post("/admin/sso/reload")

    assert response.status_code == 200
    assert "Reload failed: boom" in response.text


def test_reload_providers_picks_up_a_change_made_outside_the_webui(isolated_admin_client):
    """A KeePass edit made through an independent OidcProviders instance isn't visible to the
    already-running app until an explicit reload - sso_list()'s default get_all_providers() call
    doesn't reload from disk on its own."""
    isolated_admin_client.post(
        "/admin/sso/new",
        data=_new_provider_data("https://idp-outside.example.com", label="Before"),
        follow_redirects=False,
    )

    from Pytre_Core.context import AppContext

    other_app_ctx = AppContext(master_key="password")
    provider = other_app_ctx.oidc_providers.get_all_providers()["https://idp-outside.example.com"]
    provider.label = "Changed outside the webui"
    provider.save()

    stale_page = isolated_admin_client.get("/admin/sso")
    assert "Changed outside the webui" not in stale_page.text

    isolated_admin_client.post("/admin/sso/reload")

    fresh_page = isolated_admin_client.get("/admin/sso")
    assert "Changed outside the webui" in fresh_page.text


# ------------------------------------------------------------------------------------------
# Users OIDC links follow their provider (issuer change, deletion)
# ------------------------------------------------------------------------------------------


def _create_provider(client, issuer: str, **overrides) -> str:
    client.post("/admin/sso/new", data=_new_provider_data(issuer, **overrides), follow_redirects=False)
    return _find_uuid(client.get("/admin/sso").text, issuer)


def _link_user(client, username: str, issuer: str, sub: str) -> None:
    """Creates an OIDC user linked to `issuer`, through the server's own Users - no reload needed"""
    from datetime import datetime, timezone

    from Pytre_Core.users import AuthType, OidcLink, User

    user = User(client.app.state.server.app_ctx.users, detect_user=False)
    user.username = username
    user.auth_type = AuthType.OIDC
    user.oidc_link = OidcLink(
        issuer=issuer, sub=sub, refresh_token=f"rt-{sub}", last_verified=datetime(2026, 1, 1, tzinfo=timezone.utc)
    )
    user.save()


def _link_of(client, username: str):
    from Pytre_Core.users import User

    return User(client.app.state.server.app_ctx.users, username=username).oidc_link


def _list_row_cells(html: str, marker: str) -> list[str]:
    """Text of each cell of a provider's row: edit, select, label, issuer, client id, users, enabled, usable"""
    for row in html.split("<tr>"):
        if f">{marker}<" in row:
            return [cell.strip() for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.DOTALL)]
    raise AssertionError(f"{marker} not found in list page")


def test_edit_provider_issuer_moves_the_links_of_its_users(isolated_admin_client, caplog):
    old_issuer, new_issuer = "https://idp-old.example.com", "https://idp-new.example.com"
    uuid = _create_provider(isolated_admin_client, old_issuer)
    _link_user(isolated_admin_client, "moved_a", old_issuer, "sub-a")
    _link_user(isolated_admin_client, "moved_b", old_issuer, "sub-b")

    with caplog.at_level(logging.INFO):
        response = isolated_admin_client.post(
            f"/admin/sso/{uuid}/edit", data=_new_provider_data(new_issuer, client_secret=""), follow_redirects=False
        )

    assert response.status_code == 303
    for username, sub in (("moved_a", "sub-a"), ("moved_b", "sub-b")):
        link = _link_of(isolated_admin_client, username)
        assert link is not None
        assert (link.issuer, link.sub) == (new_issuer, sub)
        assert link.refresh_token == ""  # issued by the old issuer
        assert link.last_verified is None
    assert any(f"2 user link(s) moved from '{old_issuer}'" in r.message for r in caplog.records)

    # a later SSO login through the provider, now reporting the new issuer, finds the user by its sub
    found = isolated_admin_client.app.state.server.app_ctx.users.find_user_by_oidc_link(new_issuer, "sub-a")
    assert found is not None
    assert found.username == "moved_a"


def test_edit_provider_label_only_keeps_the_links_untouched(isolated_admin_client):
    issuer = "https://idp-relabel.example.com"
    uuid = _create_provider(isolated_admin_client, issuer, label="Before")
    _link_user(isolated_admin_client, "relabel_user", issuer, "sub-r")

    isolated_admin_client.post(
        f"/admin/sso/{uuid}/edit", data=_new_provider_data(issuer, label="After", client_secret="")
    )

    link = _link_of(isolated_admin_client, "relabel_user")
    assert link is not None
    assert (link.issuer, link.sub, link.refresh_token) == (issuer, "sub-r", "rt-sub-r")


def test_edit_provider_refused_keeps_the_links_untouched(isolated_admin_client):
    """An issuer already used by another provider is refused: nothing is saved, no link moves"""
    issuer_a, issuer_b = "https://idp-refused-a.example.com", "https://idp-refused-b.example.com"
    uuid_a = _create_provider(isolated_admin_client, issuer_a)
    _create_provider(isolated_admin_client, issuer_b)
    _link_user(isolated_admin_client, "refused_user", issuer_a, "sub-x")

    response = isolated_admin_client.post(
        f"/admin/sso/{uuid_a}/edit", data=_new_provider_data(issuer_b, client_secret=""), follow_redirects=False
    )

    assert response.status_code == 200  # form redisplayed with the error
    link = _link_of(isolated_admin_client, "refused_user")
    assert link is not None
    assert link.issuer == issuer_a
    # the redisplayed form still counts the users of the saved issuer, not of the refused one
    assert 'data-saved-issuer="' + issuer_a + '"' in response.text
    assert 'data-linked-count="1"' in response.text


def test_delete_provider_removes_the_links_of_its_users_only(isolated_admin_client, caplog):
    issuer, other_issuer = "https://idp-deleted.example.com", "https://idp-kept.example.com"
    uuid = _create_provider(isolated_admin_client, issuer)
    _create_provider(isolated_admin_client, other_issuer)
    _link_user(isolated_admin_client, "unlinked_user", issuer, "sub-d")
    _link_user(isolated_admin_client, "kept_user", other_issuer, "sub-k")

    with caplog.at_level(logging.INFO):
        isolated_admin_client.post(f"/admin/sso/{uuid}/delete")

    assert _link_of(isolated_admin_client, "unlinked_user") is None
    kept = _link_of(isolated_admin_client, "kept_user")
    assert kept is not None
    assert kept.issuer == other_issuer
    assert any(f"'{issuer}' deleted by admin '{TEST_USERNAME}', 1 user link(s) removed" in r.message for r in caplog.records)


def test_bulk_delete_removes_the_links_and_reports_them(isolated_admin_client):
    issuer_a, issuer_b = "https://idp-bulk-links-a.example.com", "https://idp-bulk-links-b.example.com"
    uuid_a = _create_provider(isolated_admin_client, issuer_a)
    uuid_b = _create_provider(isolated_admin_client, issuer_b)
    _link_user(isolated_admin_client, "bulk_a1", issuer_a, "sub-a1")
    _link_user(isolated_admin_client, "bulk_a2", issuer_a, "sub-a2")
    _link_user(isolated_admin_client, "bulk_b1", issuer_b, "sub-b1")

    response = isolated_admin_client.post(
        "/admin/sso/delete", data={"uuids": [uuid_a, uuid_b]}, follow_redirects=False
    )

    assert response.headers["location"] == "/admin/sso?msg=2+provider(s)+deleted,+3+user+link(s)+removed"
    for username in ("bulk_a1", "bulk_a2", "bulk_b1"):
        assert _link_of(isolated_admin_client, username) is None


def test_provider_form_shows_the_number_of_linked_users(isolated_admin_client):
    issuer = "https://idp-counted.example.com"
    uuid = _create_provider(isolated_admin_client, issuer)
    _link_user(isolated_admin_client, "counted_b", issuer, "sub-b")
    _link_user(isolated_admin_client, "counted_a", issuer, "sub-a")

    form = isolated_admin_client.get(f"/admin/sso/{uuid}/edit").text

    assert "2 linked user(s)" in form
    assert 'title="counted_a, counted_b"' in form  # sorted usernames, shown on hover
    assert 'data-linked-count="2"' in form
    assert "linked user(s) will lose their SSO link" in form  # delete confirmation warns about them


def test_provider_form_shows_zero_linked_users_for_an_existing_provider_only(isolated_admin_client):
    uuid = _create_provider(isolated_admin_client, "https://idp-unused.example.com")

    edit_form = isolated_admin_client.get(f"/admin/sso/{uuid}/edit").text
    new_form = isolated_admin_client.get("/admin/sso/new").text

    assert "0 linked user(s)" in edit_form
    assert "will lose their SSO link" not in edit_form  # nobody to warn about
    assert "linked user(s)" not in new_form


def test_providers_list_shows_the_number_of_linked_users(isolated_admin_client):
    issuer, unused_issuer = "https://idp-list-count.example.com", "https://idp-list-unused.example.com"
    _create_provider(isolated_admin_client, issuer)
    _create_provider(isolated_admin_client, unused_issuer)
    _link_user(isolated_admin_client, "list_count_user", issuer, "sub-l")

    list_page = isolated_admin_client.get("/admin/sso").text

    assert _list_row_cells(list_page, issuer)[5] == "1"
    assert _list_row_cells(list_page, unused_issuer)[5] == "0"
    assert 'data-linked="1"' in list_page  # used by the bulk delete confirmation
