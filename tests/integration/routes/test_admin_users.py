"""
Integration tests for the /admin/users routes (Pytre_Server/routes/admin_users.py) - list,
create, edit, delete. Uses `isolated_admin_client` for anything that writes to the KeePass database.
"""

import re

import pytest

from Pytre_Core.users import verify_password

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
            "msg_login": "Welcome",
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


def test_create_user_blank_username_reports_error(isolated_admin_client):
    """UserForm accepts an empty string (a required Form(...) parameter would have been treated as
    missing and answered with a 422), so the request reaches User.save() and its own blank-username
    check - the form is re-rendered with that message."""
    response = isolated_admin_client.post(
        "/admin/users/new",
        data={"username": "", "title": "", "msg_login": "", "auth_type": "none", "password": ""},
    )
    assert response.status_code == 200
    match = re.search(r'<p class="error form-error">(.*?)</p>', response.text)
    assert match is not None
    assert "Username" in match.group(1)


def test_create_user_duplicate_username_reports_error(isolated_admin_client):
    isolated_admin_client.post(
        "/admin/users/new",
        data={"username": "dup_user", "title": "", "msg_login": "", "auth_type": "none", "password": ""},
        follow_redirects=False,
    )

    response = isolated_admin_client.post(
        "/admin/users/new",
        data={"username": "dup_user", "title": "Second", "msg_login": "", "auth_type": "none", "password": ""},
    )
    assert response.status_code == 200
    assert "<form" in response.text  # re-rendered form, not a redirect
    match = re.search(r'<p class="error form-error">(.*?)</p>', response.text)
    assert match is not None
    assert "dup_user" in match.group(1)


def test_edit_user_updates_fields_including_admin_promotion(isolated_admin_client):
    isolated_admin_client.post(
        "/admin/users/new",
        data={"username": "promote_me", "title": "Before", "msg_login": "", "auth_type": "none", "password": ""},
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
            "msg_login": "",
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
        data={"username": "plain_user", "title": "", "msg_login": "", "auth_type": "none", "password": ""},
        follow_redirects=False,
    )
    uuid = _find_uuid(isolated_admin_client.get("/admin/users").text, "plain_user")

    edit_page = isolated_admin_client.get(f"/admin/users/{uuid}/edit")

    assert 'name="admin" value="1"' in edit_page.text  # the checkbox itself is rendered
    assert 'name="admin" value="1" checked' not in edit_page.text


def test_edit_user_unchecking_admin_demotes_them(isolated_admin_client):
    isolated_admin_client.post(
        "/admin/users/new",
        data={"username": "demote_me", "title": "", "admin": "1", "msg_login": "", "auth_type": "none", "password": ""},
        follow_redirects=False,
    )
    uuid = _find_uuid(isolated_admin_client.get("/admin/users").text, "demote_me")
    assert 'name="admin" value="1" checked' in isolated_admin_client.get(f"/admin/users/{uuid}/edit").text

    isolated_admin_client.post(
        f"/admin/users/{uuid}/edit",
        data={"username": "demote_me", "title": "", "msg_login": "", "auth_type": "none", "password": ""},
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
            "msg_login": "",
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
            "msg_login": "",
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
        data={"username": "change_pwd", "title": "", "msg_login": "", "auth_type": "basic", "password": "OldPass1"},
        follow_redirects=False,
    )
    list_page = isolated_admin_client.get("/admin/users")
    uuid = _find_uuid(list_page.text, "change_pwd")

    isolated_admin_client.post(
        f"/admin/users/{uuid}/edit",
        data={
            "username": "change_pwd",
            "title": "",
            "msg_login": "",
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
    response = isolated_admin_client.get("/admin/users/00000000-0000-0000-0000-000000000000/edit", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/users?error=User+not+found"


def test_delete_single_user(isolated_admin_client):
    isolated_admin_client.post(
        "/admin/users/new",
        data={"username": "delete_me", "title": "", "msg_login": "", "auth_type": "none", "password": ""},
        follow_redirects=False,
    )
    list_page = isolated_admin_client.get("/admin/users")
    uuid = _find_uuid(list_page.text, "delete_me")

    response = isolated_admin_client.post(f"/admin/users/{uuid}/delete", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/users?msg=User+deleted"

    list_page_after = isolated_admin_client.get("/admin/users")
    assert "delete_me" not in list_page_after.text


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
            data={"username": username, "title": "", "msg_login": "", "auth_type": "none", "password": ""},
            follow_redirects=False,
        )
    list_page = isolated_admin_client.get("/admin/users")
    uuid_a = _find_uuid(list_page.text, "bulk_a")
    uuid_b = _find_uuid(list_page.text, "bulk_b")

    response = isolated_admin_client.post("/admin/users/delete", data={"uuids": [uuid_a, uuid_b]}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/users?msg=2+user(s)+deleted"

    list_page_after = isolated_admin_client.get("/admin/users")
    assert "bulk_a" not in list_page_after.text
    assert "bulk_b" not in list_page_after.text


def test_bulk_delete_reports_partial_failure(isolated_admin_client):
    isolated_admin_client.post(
        "/admin/users/new",
        data={"username": "bulk_real", "title": "", "msg_login": "", "auth_type": "none", "password": ""},
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
