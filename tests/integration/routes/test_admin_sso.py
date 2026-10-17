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
