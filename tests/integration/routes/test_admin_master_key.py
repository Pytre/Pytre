"""
Integration tests for the /admin/master-key routes (Pytre_Server/routes/admin_master_key.py).

Uses the same in-memory keyring stand-in as tests/unit/server/test_master_key.py, monkeypatched
at the `keyring` module level - never touches the real OS credential store, and is independent
of the KeePass test database (these routes never read/write Pytre.db), so the shared admin clients are enough -
no need for an isolated per-test DB copy, except to check change-db-password is refused remotely.

The master key actions only answer local connections: they're tested with `local_admin_client`, while
`admin_client` stands for an admin connected from another machine.
"""

import logging
import re

import keyring
import keyring.errors
import pytest

from Pytre_Core.kee import Kee
from tests.integration.conftest import TEST_USERNAME

pytestmark = pytest.mark.integration


@pytest.fixture
def fake_keyring(monkeypatch):
    """In-memory stand-in for the OS keyring backend."""
    store: dict[tuple[str, str], str] = {}

    def fake_get_password(service, username):
        return store.get((service, username))

    def fake_set_password(service, username, password):
        store[(service, username)] = password

    def fake_delete_password(service, username):
        if (service, username) not in store:
            raise keyring.errors.PasswordDeleteError("not found")
        del store[(service, username)]

    monkeypatch.setattr(keyring, "get_password", fake_get_password)
    monkeypatch.setattr(keyring, "set_password", fake_set_password)
    monkeypatch.setattr(keyring, "delete_password", fake_delete_password)

    return store


def test_page_requires_admin_session(client):
    """No loopback/session -> redirected to login, same gate as every other /admin/* route."""
    response = client.get("/admin/master-key", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/admin/login")


def test_page_shows_no_key_stored_by_default(local_admin_client, fake_keyring):
    response = local_admin_client.get("/admin/master-key")

    assert response.status_code == 200
    assert "no master key stored" in response.text
    assert ">Reveal<" not in response.text


def test_master_key_page_does_not_inline_the_set_form(local_admin_client, fake_keyring):
    """The set form (password/confirm fields) lives on its own page (/admin/master-key/set-form,
    opened as a modal by the "Set" button's JS) - master_key.html itself only ever shows the
    Reveal/Set/Delete action buttons, never the form."""
    response = local_admin_client.get("/admin/master-key")

    assert 'id="set-form"' not in response.text

    actions_div_tag = re.search(r'<div class="form-actions" id="master-key-actions"[^>]*>', response.text)
    assert actions_div_tag is not None
    assert "hidden" not in actions_div_tag.group(0)


def test_set_form_page_shows_the_password_fields(local_admin_client, fake_keyring):
    """GET /set-form is a dedicated page (loaded standalone or as a modal fragment) - unlike the
    old inline design, its form is never collapsed/hidden."""
    response = local_admin_client.get("/admin/master-key/set-form")

    assert response.status_code == 200
    set_form_tag = re.search(r'<form[^>]*id="set-form"[^>]*>', response.text)
    assert set_form_tag is not None
    assert "hidden" not in set_form_tag.group(0)
    assert 'id="password"' in response.text
    assert 'id="confirm_password"' in response.text


def test_set_rejects_empty_password(local_admin_client, fake_keyring):
    response = local_admin_client.post("/admin/master-key/set", data={"password": "", "confirm_password": ""})

    assert response.status_code == 200
    assert "Password is required" in response.text
    assert not fake_keyring


def test_set_rejects_mismatched_passwords(local_admin_client, fake_keyring):
    response = local_admin_client.post("/admin/master-key/set", data={"password": "abc", "confirm_password": "xyz"})

    assert response.status_code == 200
    assert "Passwords do not match" in response.text
    assert not fake_keyring


def test_set_error_keeps_the_form_visible_for_correction(local_admin_client, fake_keyring):
    """Regression: re-rendering after a failed /set submission must keep the password fields
    visible - otherwise the error message is shown with no way to see/fix what was just
    submitted. On error, /set re-renders master_key_set_form.html (not master_key.html) - so
    there's no master-key-actions div to check here, unlike the old inline design."""
    response = local_admin_client.post("/admin/master-key/set", data={"password": "abc", "confirm_password": "xyz"})

    set_form_tag = re.search(r'<form[^>]*id="set-form"[^>]*>', response.text)
    assert set_form_tag is not None
    assert "hidden" not in set_form_tag.group(0)
    assert 'id="password"' in response.text
    assert 'id="confirm_password"' in response.text


def test_set_stores_the_new_value(local_admin_client, fake_keyring):
    response = local_admin_client.post(
        "/admin/master-key/set", data={"password": "s3cret!", "confirm_password": "s3cret!"}, follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/master-key?msg=Master+key+saved"
    assert ("Pytre_Server", "master_key") in fake_keyring
    assert fake_keyring[("Pytre_Server", "master_key")] == "s3cret!"


def test_set_is_logged_with_the_admin_username(local_admin_client, fake_keyring, caplog):
    with caplog.at_level(logging.INFO):
        local_admin_client.post("/admin/master-key/set", data={"password": "s3cret!", "confirm_password": "s3cret!"})

    assert any(f"Master key set/changed in keyring by admin '{TEST_USERNAME}'" in r.message for r in caplog.records)


def test_page_reflects_key_exists_after_set(local_admin_client, fake_keyring):
    local_admin_client.post("/admin/master-key/set", data={"password": "s3cret!", "confirm_password": "s3cret!"})

    response = local_admin_client.get("/admin/master-key")

    assert "a master key is stored" in response.text
    assert ">Reveal<" in response.text


def test_reveal_returns_the_stored_value_as_json(local_admin_client, fake_keyring):
    """/reveal is fetched client-side (see master_key.html) rather than submitted as a <form> -
    a real form submission would navigate the browser to the action URL, changing the address
    bar, which is exactly what the JSON+fetch design avoids. The Reveal/Hide toggle itself is
    pure client-side JS afterward and isn't exercised by these server-side tests."""
    local_admin_client.post("/admin/master-key/set", data={"password": "s3cret!", "confirm_password": "s3cret!"})

    response = local_admin_client.post("/admin/master-key/reveal")

    assert response.status_code == 200
    assert response.json() == {"value": "s3cret!"}


def test_reveal_is_logged_with_the_admin_username(local_admin_client, fake_keyring, caplog):
    local_admin_client.post("/admin/master-key/set", data={"password": "s3cret!", "confirm_password": "s3cret!"})

    with caplog.at_level(logging.INFO):
        local_admin_client.post("/admin/master-key/reveal")

    assert any(f"Master key revealed by admin '{TEST_USERNAME}'" in r.message for r in caplog.records)


def test_reveal_returns_null_if_nothing_stored(local_admin_client, fake_keyring):
    response = local_admin_client.post("/admin/master-key/reveal")

    assert response.status_code == 200
    assert response.json() == {"value": None}


def test_page_never_server_renders_the_value(local_admin_client, fake_keyring):
    """The page itself must never embed the secret in its HTML - only the dedicated JSON
    endpoint above does, fetched on demand by the Reveal button."""
    local_admin_client.post("/admin/master-key/set", data={"password": "s3cret!", "confirm_password": "s3cret!"})

    response = local_admin_client.get("/admin/master-key")

    assert "s3cret!" not in response.text
    assert ">Reveal<" in response.text


def test_delete_removes_the_stored_value(local_admin_client, fake_keyring):
    local_admin_client.post("/admin/master-key/set", data={"password": "s3cret!", "confirm_password": "s3cret!"})

    response = local_admin_client.post("/admin/master-key/delete", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/master-key?msg=Master+key+deleted"
    assert not fake_keyring

    page = local_admin_client.get("/admin/master-key")
    assert "no master key stored" in page.text


def test_delete_is_logged_with_the_admin_username(local_admin_client, fake_keyring, caplog):
    local_admin_client.post("/admin/master-key/set", data={"password": "s3cret!", "confirm_password": "s3cret!"})

    with caplog.at_level(logging.INFO):
        local_admin_client.post("/admin/master-key/delete")

    assert any(f"Master key deleted from keyring by admin '{TEST_USERNAME}'" in r.message for r in caplog.records)


def test_delete_is_a_noop_if_nothing_stored(local_admin_client, fake_keyring):
    """delete_master_key() swallows PasswordDeleteError - deleting an already-empty key must not 500."""
    response = local_admin_client.post("/admin/master-key/delete", follow_redirects=False)

    assert response.status_code == 303


def test_page_heading(admin_client, fake_keyring):
    """The page itself stays visible to a remote admin"""
    response = admin_client.get("/admin/master-key")

    assert "System keyring" in response.text


# ================================================================================
# Local connections only
# ================================================================================

LOCAL_ONLY_REQUESTS = [
    ("get", "/admin/master-key/set-form", {}),
    ("post", "/admin/master-key/reveal", {}),
    ("post", "/admin/master-key/delete", {}),
    ("post", "/admin/master-key/set", {"password": "s3cret!", "confirm_password": "s3cret!"}),
    ("post", "/admin/master-key/change-db-password", {"password": "n3w!", "confirm_password": "n3w!"}),
]
LOCAL_ONLY_IDS = ["set-form", "reveal", "delete", "set", "change-db-password"]


def send(test_client, method: str, path: str, data: dict, **kwargs):
    if method == "get":
        return test_client.get(path, follow_redirects=False, **kwargs)
    return test_client.post(path, data=data, follow_redirects=False, **kwargs)


@pytest.mark.parametrize("method, path, data", LOCAL_ONLY_REQUESTS, ids=LOCAL_ONLY_IDS)
def test_master_key_actions_are_refused_to_a_remote_admin(
    isolated_admin_client, fake_keyring, monkeypatch, caplog, method, path, data
):
    """A valid admin session isn't enough: a stolen one can't be used from another machine. Nothing is changed,
    the database included (isolated copy, in case the check ever stopped working), and the refusal is logged."""
    fake_keyring[("Pytre_Server", "master_key")] = "stored"
    pwd_changes: list[str] = []
    monkeypatch.setattr(Kee, "pwd_change", lambda _self, password, **_kwargs: pwd_changes.append(password))

    with caplog.at_level(logging.WARNING):
        response = send(isolated_admin_client, method, path, data)

    assert response.status_code == 403
    assert "stored" not in response.text
    assert fake_keyring == {("Pytre_Server", "master_key"): "stored"}
    assert pwd_changes == []
    expected_log = f"Local-only action refused for admin '{TEST_USERNAME}' on {path}: not a local connection"
    assert any(expected_log in record.message for record in caplog.records)


@pytest.mark.parametrize("header", ["X-Forwarded-For", "X-Real-IP", "Forwarded"])
def test_master_key_actions_are_refused_through_a_reverse_proxy(local_admin_client, fake_keyring, header):
    """A proxy on the server itself connects from a loopback address: its forwarding headers give it away"""
    fake_keyring[("Pytre_Server", "master_key")] = "stored"

    response = local_admin_client.post("/admin/master-key/reveal", headers={header: "203.0.113.5"})

    assert response.status_code == 403
    assert "stored" not in response.text


@pytest.mark.parametrize("method, path, data", LOCAL_ONLY_REQUESTS, ids=LOCAL_ONLY_IDS)
def test_master_key_actions_without_session_redirect_to_login(client, fake_keyring, method, path, data):
    """The session is checked first: no 403 hinting at the route, the usual login redirect"""
    response = send(client, method, path, data)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/admin/login")
    assert not fake_keyring


def test_page_shows_the_status_but_no_action_to_a_remote_admin(admin_client, fake_keyring):
    fake_keyring[("Pytre_Server", "master_key")] = "s3cret!"

    response = admin_client.get("/admin/master-key")

    assert response.status_code == 200
    assert "a master key is stored" in response.text
    assert 'id="master-key-local-only"' in response.text
    assert "only available from the server itself" in response.text
    assert 'id="master-key-actions"' not in response.text
    assert ">Reveal<" not in response.text
    assert "/admin/master-key/reveal" not in response.text
    assert "s3cret!" not in response.text


def test_page_shows_the_actions_to_a_local_admin(local_admin_client, fake_keyring):
    fake_keyring[("Pytre_Server", "master_key")] = "s3cret!"

    response = local_admin_client.get("/admin/master-key")

    assert 'id="master-key-actions"' in response.text
    assert ">Reveal<" in response.text
    assert 'id="master-key-local-only"' not in response.text


def test_home_page_links_to_master_key_management(admin_client):
    response = admin_client.get("/admin")

    assert response.status_code == 200
    assert '<a class="tool" href="/admin/master-key"' in response.text
    assert "Available once admin authentication is in place" not in response.text
