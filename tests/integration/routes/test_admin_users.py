"""
Integration tests for the /admin/users routes (Pytre_Server/routes/admin_users.py) - list,
create, edit, delete. Uses `isolated_admin_client` for anything that writes to the KeePass database.
"""

import logging
import re

import pytest

from Pytre_Core.users import verify_password
from tests.integration.conftest import TEST_USERNAME

pytestmark = pytest.mark.integration


def _find_uuid(html: str, username: str) -> str:
    """Extracts a user's edit-link uuid from the rendered list page, by scanning the row that
    contains their username."""
    for row in html.split("<tr>"):
        if f">{username}<" in row:
            match = re.search(r"/admin/users/([0-9a-f-]+)/edit", row)
            assert match is not None
            return match.group(1)
    raise AssertionError(f"{username} not found in list page")


def test_users_list_renders_empty(admin_client):
    response = admin_client.get("/admin/users")
    assert response.status_code == 200
    assert "<table" in response.text


def test_users_list_shows_existing_user(admin_client):
    response = admin_client.get("/admin/users")
    assert response.status_code == 200
    assert "test_user" in response.text


def test_users_list_recovers_from_a_reload_failure_on_a_plain_page_view(admin_client, monkeypatch):
    """users_list() always attempts get_all_users(reload=True) on every normal page view, not
    just via the /reload button - it must fall back to reload=False + an error message rather
    than crash with an unhandled 500 if that reload fails."""
    from Pytre_Core.users import Users

    original = Users.get_all_users

    def _raise_on_reload(self, reload=True):
        if reload:
            raise RuntimeError("boom")
        return original(self, reload=reload)

    monkeypatch.setattr(Users, "get_all_users", _raise_on_reload)

    response = admin_client.get("/admin/users")

    assert response.status_code == 200
    assert "Load failed: boom" in response.text
    assert "test_user" in response.text  # falls back to the already-loaded data, not an empty list


def test_create_user_succeeds_and_appears_in_list(isolated_admin_client):
    response = isolated_admin_client.post(
        "/admin/users/new",
        data={
            "username": "new_admin",
            "title": "New Admin",
            "admin": "1",
            "auth_type": "basic",
            "password": "s3cret!",
            "groups": ["sales", "it", "ops"],
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/users?msg=User+saved"

    list_page = isolated_admin_client.get("/admin/users")
    assert "new_admin" in list_page.text
    assert "New Admin" in list_page.text
    assert "it" in list_page.text and "ops" in list_page.text


def test_create_user_is_logged_with_the_admin_username(isolated_admin_client, caplog):
    with caplog.at_level(logging.INFO):
        isolated_admin_client.post(
            "/admin/users/new",
            data={"username": "logged_user", "title": "", "auth_type": "none", "password": ""},
        )

    assert any(f"User 'logged_user' saved by admin '{TEST_USERNAME}'" in r.message for r in caplog.records)


def test_create_user_blank_username_reports_error(isolated_admin_client):
    """UserForm accepts an empty string (a required Form(...) parameter would have been treated as
    missing and answered with a 422), so the request reaches User.save() and its own blank-username
    check - the form is re-rendered with that message."""
    response = isolated_admin_client.post(
        "/admin/users/new",
        data={"username": "", "title": "", "auth_type": "none", "password": ""},
    )
    assert response.status_code == 200
    match = re.search(r'<p class="error form-error">(.*?)</p>', response.text)
    assert match is not None
    assert "Username" in match.group(1)


def test_create_user_duplicate_username_reports_error(isolated_admin_client):
    isolated_admin_client.post(
        "/admin/users/new",
        data={"username": "dup_user", "title": "", "auth_type": "none", "password": ""},
        follow_redirects=False,
    )

    response = isolated_admin_client.post(
        "/admin/users/new",
        data={"username": "dup_user", "title": "Second", "auth_type": "none", "password": ""},
    )
    assert response.status_code == 200
    assert "<form" in response.text  # re-rendered form, not a redirect
    match = re.search(r'<p class="error form-error">(.*?)</p>', response.text)
    assert match is not None
    assert "dup_user" in match.group(1)


def test_edit_user_updates_fields_including_admin_promotion(isolated_admin_client):
    isolated_admin_client.post(
        "/admin/users/new",
        data={"username": "promote_me", "title": "Before", "auth_type": "none", "password": ""},
        follow_redirects=False,
    )
    list_page = isolated_admin_client.get("/admin/users")
    uuid = _find_uuid(list_page.text, "promote_me")

    response = isolated_admin_client.post(
        f"/admin/users/{uuid}/edit",
        data={
            "username": "promote_me",
            "title": "After",
            "admin": "1",  # the exact use case a future admin login page will need
            "auth_type": "none",
            "password": "",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    edit_page = isolated_admin_client.get(f"/admin/users/{uuid}/edit")
    assert 'value="After"' in edit_page.text
    assert 'name="admin" value="1" checked' in edit_page.text


def test_create_user_without_admin_checkbox_is_not_admin(isolated_admin_client):
    """Regression: an unchecked checkbox sends no field at all, so UserForm.admin must fall back to
    False - testing it against None (as the former `str | None` parameter allowed) made every
    saved user an admin."""
    isolated_admin_client.post(
        "/admin/users/new",
        data={"username": "plain_user", "title": "", "auth_type": "none", "password": ""},
        follow_redirects=False,
    )
    uuid = _find_uuid(isolated_admin_client.get("/admin/users").text, "plain_user")

    edit_page = isolated_admin_client.get(f"/admin/users/{uuid}/edit")

    assert 'name="admin" value="1"' in edit_page.text  # the checkbox itself is rendered
    assert 'name="admin" value="1" checked' not in edit_page.text


def test_edit_user_unchecking_admin_demotes_them(isolated_admin_client):
    isolated_admin_client.post(
        "/admin/users/new",
        data={
            "username": "demote_me",
            "title": "",
            "admin": "1",
            "auth_type": "none",
            "password": "",
        },
        follow_redirects=False,
    )
    uuid = _find_uuid(isolated_admin_client.get("/admin/users").text, "demote_me")
    assert 'name="admin" value="1" checked' in isolated_admin_client.get(f"/admin/users/{uuid}/edit").text

    isolated_admin_client.post(
        f"/admin/users/{uuid}/edit",
        data={"username": "demote_me", "title": "", "auth_type": "none", "password": ""},
        follow_redirects=False,
    )

    edit_page = isolated_admin_client.get(f"/admin/users/{uuid}/edit")
    assert 'name="admin" value="1" checked' not in edit_page.text


def test_edit_user_blank_password_preserves_existing_hash(isolated_admin_client):
    from Pytre_Core.context import AppContext

    isolated_admin_client.post(
        "/admin/users/new",
        data={
            "username": "keep_pwd",
            "title": "",
            "auth_type": "basic",
            "password": "OriginalPass1",
        },
        follow_redirects=False,
    )
    list_page = isolated_admin_client.get("/admin/users")
    uuid = _find_uuid(list_page.text, "keep_pwd")

    isolated_admin_client.post(
        f"/admin/users/{uuid}/edit",
        data={
            "username": "keep_pwd",
            "title": "Updated title only",
            "auth_type": "basic",
            "password": "",  # left blank on purpose
        },
        follow_redirects=False,
    )

    app_ctx = AppContext(master_key="password")
    user = app_ctx.users.find_user_by_uuid(uuid)
    assert user is not None
    assert user.title == "Updated title only"
    assert verify_password("OriginalPass1", user.password_hash) is True


def test_edit_user_new_password_is_hashed_and_verifiable(isolated_admin_client):
    from Pytre_Core.context import AppContext

    isolated_admin_client.post(
        "/admin/users/new",
        data={"username": "change_pwd", "title": "", "auth_type": "basic", "password": "OldPass1"},
        follow_redirects=False,
    )
    list_page = isolated_admin_client.get("/admin/users")
    uuid = _find_uuid(list_page.text, "change_pwd")

    isolated_admin_client.post(
        f"/admin/users/{uuid}/edit",
        data={
            "username": "change_pwd",
            "title": "",
            "auth_type": "basic",
            "password": "NewPass2",
        },
        follow_redirects=False,
    )

    app_ctx = AppContext(master_key="password")
    user = app_ctx.users.find_user_by_uuid(uuid)
    assert user is not None
    assert verify_password("NewPass2", user.password_hash) is True
    assert verify_password("OldPass1", user.password_hash) is False


def test_edit_unknown_uuid_redirects_to_list_with_error(isolated_admin_client):
    response = isolated_admin_client.get(
        "/admin/users/00000000-0000-0000-0000-000000000000/edit", follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/users?error=User+not+found"


def test_delete_single_user(isolated_admin_client):
    isolated_admin_client.post(
        "/admin/users/new",
        data={"username": "delete_me", "title": "", "auth_type": "none", "password": ""},
        follow_redirects=False,
    )
    list_page = isolated_admin_client.get("/admin/users")
    uuid = _find_uuid(list_page.text, "delete_me")

    response = isolated_admin_client.post(f"/admin/users/{uuid}/delete", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/users?msg=User+deleted"

    list_page_after = isolated_admin_client.get("/admin/users")
    assert "delete_me" not in list_page_after.text


def test_delete_single_user_is_logged_with_the_admin_username(isolated_admin_client, caplog):
    isolated_admin_client.post(
        "/admin/users/new",
        data={"username": "delete_me_logged", "title": "", "auth_type": "none", "password": ""},
    )
    list_page = isolated_admin_client.get("/admin/users")
    uuid = _find_uuid(list_page.text, "delete_me_logged")

    with caplog.at_level(logging.INFO):
        isolated_admin_client.post(f"/admin/users/{uuid}/delete")

    assert any(f"User 'delete_me_logged' deleted by admin '{TEST_USERNAME}'" in r.message for r in caplog.records)


def test_delete_unknown_uuid_redirects_with_error(isolated_admin_client):
    response = isolated_admin_client.post(
        "/admin/users/00000000-0000-0000-0000-000000000000/delete", follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/users?error=User+not+found"


def test_bulk_delete_removes_all_selected(isolated_admin_client):
    for username in ("bulk_a", "bulk_b"):
        isolated_admin_client.post(
            "/admin/users/new",
            data={"username": username, "title": "", "auth_type": "none", "password": ""},
            follow_redirects=False,
        )
    list_page = isolated_admin_client.get("/admin/users")
    uuid_a = _find_uuid(list_page.text, "bulk_a")
    uuid_b = _find_uuid(list_page.text, "bulk_b")

    response = isolated_admin_client.post(
        "/admin/users/delete", data={"uuids": [uuid_a, uuid_b]}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/users?msg=2+user(s)+deleted"

    list_page_after = isolated_admin_client.get("/admin/users")
    assert "bulk_a" not in list_page_after.text
    assert "bulk_b" not in list_page_after.text


def test_bulk_delete_is_logged_per_user_with_the_admin_username(isolated_admin_client, caplog):
    for username in ("bulk_log_a", "bulk_log_b"):
        isolated_admin_client.post(
            "/admin/users/new", data={"username": username, "title": "", "auth_type": "none", "password": ""}
        )
    list_page = isolated_admin_client.get("/admin/users")
    uuid_a = _find_uuid(list_page.text, "bulk_log_a")
    uuid_b = _find_uuid(list_page.text, "bulk_log_b")

    with caplog.at_level(logging.INFO):
        isolated_admin_client.post("/admin/users/delete", data={"uuids": [uuid_a, uuid_b]})

    assert any(f"User 'bulk_log_a' deleted by admin '{TEST_USERNAME}' (bulk)" in r.message for r in caplog.records)
    assert any(f"User 'bulk_log_b' deleted by admin '{TEST_USERNAME}' (bulk)" in r.message for r in caplog.records)


def test_bulk_delete_reports_partial_failure(isolated_admin_client):
    isolated_admin_client.post(
        "/admin/users/new",
        data={"username": "bulk_real", "title": "", "auth_type": "none", "password": ""},
        follow_redirects=False,
    )
    list_page = isolated_admin_client.get("/admin/users")
    uuid_real = _find_uuid(list_page.text, "bulk_real")
    fake_uuid = "00000000-0000-0000-0000-000000000000"

    response = isolated_admin_client.post(
        "/admin/users/delete", data={"uuids": [uuid_real, fake_uuid]}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/users?msg=1+user(s)+deleted,+1+error(s)"


# ------------------------------------------------------------------------------------------
# Reload
# ------------------------------------------------------------------------------------------
def test_reload_users_redirects_to_the_list(isolated_admin_client):
    """Post/Redirect/Get: the browser must land back on a GET-able URL, never stay parked on
    this POST-only one (a page refresh there would resubmit the POST)."""
    response = isolated_admin_client.post("/admin/users/reload", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/users?msg=Users+reloaded"

    response = isolated_admin_client.get(response.headers["location"])
    assert "Users reloaded" in response.text


def test_reload_users_reports_a_failed_reload(isolated_admin_client, monkeypatch):
    """Regression: users_list()'s own fallback (its except branch) must call get_all_users()
    with reload=False, so a persistent reload failure doesn't also break rendering the error
    page itself - only the initial reload=True attempt is meant to fail."""
    from Pytre_Core.users import Users

    original = Users.get_all_users

    def _raise_on_reload(self, reload=True):
        if reload:
            raise RuntimeError("boom")
        return original(self, reload=reload)

    monkeypatch.setattr(Users, "get_all_users", _raise_on_reload)

    response = isolated_admin_client.post("/admin/users/reload")

    assert response.status_code == 200
    assert "Load failed: boom" in response.text
    assert "Users reloaded" not in response.text


def test_reload_users_route_explicitly_requests_a_reload_from_disk(isolated_admin_client, monkeypatch):
    """users_list()'s own get_all_users() already defaults to reload=True (see admin_users.py),
    so a subsequent page view would pick up an outside change on its own regardless of this
    route - the route's own contract worth asserting is that it explicitly triggers reload=True
    itself, rather than relying on that default."""
    from Pytre_Core.users import Users

    calls: list[bool] = []
    original = Users.get_all_users

    def _spy(self, reload=True):
        calls.append(reload)
        return original(self, reload=reload)

    monkeypatch.setattr(Users, "get_all_users", _spy)

    isolated_admin_client.post("/admin/users/reload")

    assert True in calls


# --- OIDC link (_apply_oidc_link(), SSO link provider picker in user_form.html) ---


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


def _create_provider(client, issuer: str, **overrides) -> str:
    """Creates an OIDC provider via /admin/sso/new and returns its uuid."""
    client.post("/admin/sso/new", data=_new_provider_data(issuer, **overrides), follow_redirects=False)
    list_page = client.get("/admin/sso")
    for row in list_page.text.split("<tr>"):
        if f">{issuer}<" in row:
            match = re.search(r"/admin/sso/([0-9a-f-]+)/edit", row)
            assert match is not None
            return match.group(1)
    raise AssertionError(f"{issuer} not found in sso list page")


def _create_user(client, username: str, **fields: str) -> str:
    """Creates a blank user via /admin/users/new and returns its uuid - `fields` are extra form fields,
    e.g. no_all_group="1" to tick a checkbox."""
    client.post(
        "/admin/users/new",
        data={"username": username, "title": "", "auth_type": "none", "password": ""} | fields,
        follow_redirects=False,
    )
    list_page = client.get("/admin/users")
    return _find_uuid(list_page.text, username)


def _edit_user_data(username: str, **overrides) -> dict:
    data = {"username": username, "title": "", "auth_type": "none", "password": ""}
    data.update(overrides)
    return data


def test_edit_user_oidc_link_creates_a_fresh_unverified_link(isolated_admin_client):
    issuer = "https://idp-new-link.example.com"
    _create_provider(isolated_admin_client, issuer)
    user_uuid = _create_user(isolated_admin_client, "sso_link_user")

    isolated_admin_client.post(
        f"/admin/users/{user_uuid}/edit",
        data=_edit_user_data("sso_link_user", oidc_issuer=issuer, oidc_sub="sub-123"),
        follow_redirects=False,
    )

    from Pytre_Core.context import AppContext

    user = AppContext(master_key="password").users.find_user_by_uuid(user_uuid)
    assert user is not None
    link = user.oidc_link
    assert link is not None
    assert link.issuer == issuer
    assert link.sub == "sub-123"
    assert link.refresh_token == ""
    assert link.last_verified is None


def test_edit_user_oidc_link_unchanged_preserves_refresh_token_and_last_verified(isolated_admin_client):
    """The admin form never submits refresh_token/last_verified (never displayed, see the
    no-leak test below) - re-submitting the same issuer+sub must not silently wipe them."""
    from datetime import datetime, timezone

    from Pytre_Core.context import AppContext
    from Pytre_Core.users import OidcLink

    issuer = "https://idp-preserve.example.com"
    _create_provider(isolated_admin_client, issuer)
    user_uuid = _create_user(isolated_admin_client, "sso_preserve_user")

    user = AppContext(master_key="password").users.find_user_by_uuid(user_uuid)
    assert user is not None
    verified_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    user.oidc_link = OidcLink(issuer=issuer, sub="sub-abc", refresh_token="rt-abc", last_verified=verified_at)
    user.save()
    # the app's own Users/Kee.db was opened earlier and doesn't see this out-of-band write on its
    # own (see the Kee.db reload gotcha in the plan) - force it to resync from disk before editing.
    isolated_admin_client.post("/admin/users/reload")

    isolated_admin_client.post(
        f"/admin/users/{user_uuid}/edit",
        data=_edit_user_data("sso_preserve_user", oidc_issuer=issuer, oidc_sub="sub-abc"),  # unchanged
        follow_redirects=False,
    )

    reloaded = AppContext(master_key="password").users.find_user_by_uuid(user_uuid)
    assert reloaded is not None
    link = reloaded.oidc_link
    assert link is not None
    assert link.sub == "sub-abc"
    assert link.refresh_token == "rt-abc"
    assert link.last_verified == verified_at


def test_edit_user_oidc_sub_changed_resets_refresh_token_and_last_verified(isolated_admin_client):
    """A hand-typed sub has no real refresh_token behind it - a changed/new sub must not keep
    whatever secret was captured for the previous one."""
    from datetime import datetime, timezone

    from Pytre_Core.context import AppContext
    from Pytre_Core.users import OidcLink

    issuer = "https://idp-reset.example.com"
    _create_provider(isolated_admin_client, issuer)
    user_uuid = _create_user(isolated_admin_client, "sso_reset_user")

    user = AppContext(master_key="password").users.find_user_by_uuid(user_uuid)
    assert user is not None
    user.oidc_link = OidcLink(
        issuer=issuer, sub="old-sub", refresh_token="rt-old", last_verified=datetime(2026, 1, 1, tzinfo=timezone.utc)
    )
    user.save()
    isolated_admin_client.post("/admin/users/reload")

    isolated_admin_client.post(
        f"/admin/users/{user_uuid}/edit",
        data=_edit_user_data("sso_reset_user", oidc_issuer=issuer, oidc_sub="new-sub"),
        follow_redirects=False,
    )

    reloaded = AppContext(master_key="password").users.find_user_by_uuid(user_uuid)
    assert reloaded is not None
    link = reloaded.oidc_link
    assert link is not None
    assert link.sub == "new-sub"
    assert link.refresh_token == ""
    assert link.last_verified is None


def test_edit_user_oidc_link_changed_provider_resets_refresh_token(isolated_admin_client):
    """Switching the selected provider (even keeping the same sub) must also drop the old
    secrets - a sub recycled by a different IdP must never inherit the previous link's
    refresh_token."""
    from datetime import datetime, timezone

    from Pytre_Core.context import AppContext
    from Pytre_Core.users import OidcLink

    old_issuer = "https://idp-old.example.com"
    new_issuer = "https://idp-new.example.com"
    _create_provider(isolated_admin_client, old_issuer)
    _create_provider(isolated_admin_client, new_issuer)
    user_uuid = _create_user(isolated_admin_client, "sso_switch_user")

    user = AppContext(master_key="password").users.find_user_by_uuid(user_uuid)
    assert user is not None
    user.oidc_link = OidcLink(
        issuer=old_issuer,
        sub="sub-abc",
        refresh_token="rt-old",
        last_verified=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    user.save()
    isolated_admin_client.post("/admin/users/reload")

    isolated_admin_client.post(
        f"/admin/users/{user_uuid}/edit",
        data=_edit_user_data("sso_switch_user", oidc_issuer=new_issuer, oidc_sub="sub-abc"),
        follow_redirects=False,
    )

    reloaded = AppContext(master_key="password").users.find_user_by_uuid(user_uuid)
    assert reloaded is not None
    link = reloaded.oidc_link
    assert link is not None
    assert link.issuer == new_issuer
    assert link.sub == "sub-abc"
    assert link.refresh_token == ""
    assert link.last_verified is None


def test_edit_user_oidc_sub_blank_keeps_the_provider_and_resets_the_sub(isolated_admin_client):
    """A provider without sub reserves the account to that provider: the sub is set again on the
    next login through it - the link is kept, only its sub and verification state are reset."""
    from Pytre_Core.context import AppContext
    from Pytre_Core.users import OidcLink

    issuer = "https://idp-reset-sub.example.com"
    _create_provider(isolated_admin_client, issuer)
    user_uuid = _create_user(isolated_admin_client, "sso_reset_sub_user")

    user = AppContext(master_key="password").users.find_user_by_uuid(user_uuid)
    assert user is not None
    user.oidc_link = OidcLink(issuer=issuer, sub="old-sub", refresh_token="rt-old")
    user.save()
    isolated_admin_client.post("/admin/users/reload")

    isolated_admin_client.post(
        f"/admin/users/{user_uuid}/edit",
        data=_edit_user_data("sso_reset_sub_user", oidc_issuer=issuer, oidc_sub=""),
        follow_redirects=False,
    )

    reloaded = AppContext(master_key="password").users.find_user_by_uuid(user_uuid)
    assert reloaded is not None
    assert reloaded.oidc_link == OidcLink(issuer=issuer, sub="")


def test_edit_user_new_oidc_user_with_a_provider_and_no_sub_keeps_the_provider(isolated_admin_client):
    issuer = "https://idp-reserved.example.com"
    _create_provider(isolated_admin_client, issuer)

    user_uuid = _create_user(isolated_admin_client, "sso_reserved_user", auth_type="oidc", oidc_issuer=issuer)

    user = isolated_admin_client.app.state.server.app_ctx.users.find_user_by_uuid(user_uuid)
    assert user is not None
    assert user.oidc_link is not None
    assert (user.oidc_link.issuer, user.oidc_link.sub) == (issuer, "")


def test_edit_user_any_provider_removes_the_link(isolated_admin_client):
    """ "Any" is the blank provider option: with it the form disables and clears the sub, so both
    fields come blank - the link is removed and the first login may go through any provider."""
    from Pytre_Core.context import AppContext
    from Pytre_Core.users import OidcLink

    issuer = "https://idp-blank-provider.example.com"
    _create_provider(isolated_admin_client, issuer)
    user_uuid = _create_user(isolated_admin_client, "sso_blank_provider_user")

    user = AppContext(master_key="password").users.find_user_by_uuid(user_uuid)
    assert user is not None
    user.oidc_link = OidcLink(issuer=issuer, sub="to-be-removed")
    user.save()
    isolated_admin_client.post("/admin/users/reload")

    isolated_admin_client.post(
        f"/admin/users/{user_uuid}/edit",
        data=_edit_user_data("sso_blank_provider_user", oidc_issuer="", oidc_sub=""),
        follow_redirects=False,
    )

    reloaded = AppContext(master_key="password").users.find_user_by_uuid(user_uuid)
    assert reloaded is not None
    assert reloaded.oidc_link is None


def test_edit_user_sub_without_provider_is_refused_and_keeps_the_link(isolated_admin_client):
    """A sub is only meaningful within its issuer: a sub sent without provider (the form disables the
    field, so only a hand-made request can) is reported instead of being silently dropped"""
    from Pytre_Core.context import AppContext
    from Pytre_Core.users import OidcLink

    issuer = "https://idp-sub-alone.example.com"
    _create_provider(isolated_admin_client, issuer)
    user_uuid = _create_user(isolated_admin_client, "sso_sub_alone_user")

    user = AppContext(master_key="password").users.find_user_by_uuid(user_uuid)
    assert user is not None
    user.oidc_link = OidcLink(issuer=issuer, sub="kept-sub")
    user.save()
    isolated_admin_client.post("/admin/users/reload")

    response = isolated_admin_client.post(
        f"/admin/users/{user_uuid}/edit",
        data=_edit_user_data("sso_sub_alone_user", oidc_issuer="", oidc_sub="kept-sub"),
        follow_redirects=False,
    )

    assert response.status_code == 200  # the form is redisplayed, no redirect to the list
    assert "Select a provider for the sub" in response.text

    reloaded = AppContext(master_key="password").users.find_user_by_uuid(user_uuid)
    assert reloaded is not None
    assert reloaded.oidc_link is not None
    assert (reloaded.oidc_link.issuer, reloaded.oidc_link.sub) == (issuer, "kept-sub")


def test_edit_user_omitted_oidc_fields_also_remove_the_link(isolated_admin_client):
    """If oidc_issuer/oidc_sub are absent from the submission entirely, the server must treat
    that the same as a blank value, not as "leave it alone"."""
    from Pytre_Core.context import AppContext
    from Pytre_Core.users import OidcLink

    issuer = "https://idp-omit.example.com"
    _create_provider(isolated_admin_client, issuer)
    user_uuid = _create_user(isolated_admin_client, "sso_omit_user")

    user = AppContext(master_key="password").users.find_user_by_uuid(user_uuid)
    assert user is not None
    user.oidc_link = OidcLink(issuer=issuer, sub="to-be-removed")
    user.save()
    isolated_admin_client.post("/admin/users/reload")

    isolated_admin_client.post(
        f"/admin/users/{user_uuid}/edit",
        data=_edit_user_data("sso_omit_user"),  # no oidc_issuer/oidc_sub keys at all
        follow_redirects=False,
    )

    reloaded = AppContext(master_key="password").users.find_user_by_uuid(user_uuid)
    assert reloaded is not None
    assert reloaded.oidc_link is None


def test_edit_user_oidc_link_renders_in_the_form_without_leaking_the_refresh_token(isolated_admin_client):
    from datetime import datetime, timezone

    from Pytre_Core.context import AppContext
    from Pytre_Core.users import OidcLink

    issuer = "https://idp-display.example.com"
    _create_provider(isolated_admin_client, issuer, label="Display IdP")
    user_uuid = _create_user(isolated_admin_client, "sso_display_user")

    user = AppContext(master_key="password").users.find_user_by_uuid(user_uuid)
    assert user is not None
    user.oidc_link = OidcLink(
        issuer=issuer,
        sub="displayed-sub",
        refresh_token="super-secret-rt",
        last_verified=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    user.save()
    isolated_admin_client.post("/admin/users/reload")

    edit_page = isolated_admin_client.get(f"/admin/users/{user_uuid}/edit").text
    assert "Display IdP" in edit_page
    assert f'value="{issuer}" selected' in edit_page
    assert 'value="displayed-sub"' in edit_page
    assert "super-secret-rt" not in edit_page


def test_edit_user_oidc_link_shows_the_issuer_even_if_its_provider_was_deleted(isolated_admin_client):
    """A link whose issuer no longer matches any configured provider must still be visible (and
    its sub still editable) rather than silently disappearing from the dropdown."""
    from Pytre_Core.context import AppContext
    from Pytre_Core.users import OidcLink

    _create_provider(isolated_admin_client, "https://still-configured.example.com")
    user_uuid = _create_user(isolated_admin_client, "sso_orphan_user")

    user = AppContext(master_key="password").users.find_user_by_uuid(user_uuid)
    assert user is not None
    user.oidc_link = OidcLink(issuer="https://deleted-idp.example.com", sub="orphan-sub")
    user.save()
    isolated_admin_client.post("/admin/users/reload")

    edit_page = isolated_admin_client.get(f"/admin/users/{user_uuid}/edit").text
    assert "https://deleted-idp.example.com" in edit_page
    assert 'value="orphan-sub"' in edit_page


# ================================================================================
# "Restrict queries to the selected groups" option (User.no_all_group)
# ================================================================================


def _list_row_cells(html: str, username: str) -> list[str]:
    """Text of each cell of the user's row in the list page: edit, select, id, title, auth, admin,
    groups, restriction, then the custom attributes"""
    for row in html.split("<tr>"):
        if f">{username}<" in row:
            return [cell.strip() for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.DOTALL)]
    raise AssertionError(f"{username} not found in list page")


def test_create_user_with_groups_restriction_saves_it(isolated_admin_client):
    uuid = _create_user(isolated_admin_client, "restricted_user", no_all_group="1")

    edit_page = isolated_admin_client.get(f"/admin/users/{uuid}/edit").text
    assert 'name="no_all_group" value="1" checked' in edit_page

    user = isolated_admin_client.app.state.server.app_ctx.users.find_user_by_uuid(uuid)
    assert user is not None
    assert user.no_all_group is True
    assert "all" not in user.groups


def test_create_user_without_groups_restriction_checkbox_is_not_restricted(isolated_admin_client):
    """An unchecked checkbox sends no field at all: UserForm.no_all_group must fall back to False"""
    uuid = _create_user(isolated_admin_client, "open_user")

    edit_page = isolated_admin_client.get(f"/admin/users/{uuid}/edit").text
    assert 'name="no_all_group" value="1"' in edit_page  # the checkbox itself is rendered
    assert 'name="no_all_group" value="1" checked' not in edit_page

    user = isolated_admin_client.app.state.server.app_ctx.users.find_user_by_uuid(uuid)
    assert user is not None
    assert user.no_all_group is False
    assert "all" in user.groups


def test_edit_user_unchecking_groups_restriction_removes_it(isolated_admin_client):
    uuid = _create_user(isolated_admin_client, "unrestrict_me", no_all_group="1")

    isolated_admin_client.post(
        f"/admin/users/{uuid}/edit",
        data={"username": "unrestrict_me", "title": "", "auth_type": "none", "password": ""},
        follow_redirects=False,
    )

    edit_page = isolated_admin_client.get(f"/admin/users/{uuid}/edit").text
    assert 'name="no_all_group" value="1" checked' not in edit_page


def test_users_list_shows_the_groups_restriction(isolated_admin_client):
    _create_user(isolated_admin_client, "restricted_in_list", no_all_group="1")
    _create_user(isolated_admin_client, "open_in_list")

    list_page = isolated_admin_client.get("/admin/users").text

    assert _list_row_cells(list_page, "restricted_in_list")[7] == "Yes"
    assert _list_row_cells(list_page, "open_in_list")[7] == ""


def test_users_list_shows_the_auth_type_and_the_oidc_provider(isolated_admin_client):
    from Pytre_Core.users import OidcLink

    issuer = "https://idp-auth-column.example.com"
    _create_provider(isolated_admin_client, issuer, label="Auth IdP")
    _create_user(isolated_admin_client, "auth_none_user")
    _create_user(isolated_admin_client, "auth_basic_user", auth_type="basic", password="a-password")
    _create_user(isolated_admin_client, "auth_any_user", auth_type="oidc")
    _create_user(isolated_admin_client, "auth_provider_user", auth_type="oidc", oidc_issuer=issuer)
    orphan_uuid = _create_user(isolated_admin_client, "auth_orphan_user", auth_type="oidc")

    users = isolated_admin_client.app.state.server.app_ctx.users
    orphan = users.find_user_by_uuid(orphan_uuid)
    assert orphan is not None
    orphan.oidc_link = OidcLink(issuer="https://deleted-idp.example.com", sub="sub-1")
    orphan.save()

    list_page = isolated_admin_client.get("/admin/users").text

    assert _list_row_cells(list_page, "auth_none_user")[4] == "None"
    assert _list_row_cells(list_page, "auth_basic_user")[4] == "Basic"
    assert _list_row_cells(list_page, "auth_any_user")[4] == "OIDC: Any"
    assert _list_row_cells(list_page, "auth_provider_user")[4] == "OIDC: Auth IdP"
    assert _list_row_cells(list_page, "auth_orphan_user")[4] == "OIDC: unknown"
