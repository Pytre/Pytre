"""
Integration tests for the admin webui login/session mechanism (Pytre_Server/routes/admin_auth.py).

`client`/`isolated_client` are used deliberately here - this is the one place in the admin test
suite where actually going through the login flow is the point. Every other admin route test
uses `admin_client`/`isolated_admin_client` instead (already logged in as the test admin), to
focus on the route's own business logic without repeating the login dance in every test.
"""

import pytest
from fastapi.testclient import TestClient

from Pytre_Core.users import Users
from Pytre_Server.app import create_app
from tests.integration.conftest import TEST_DB_PASSWORD, TEST_USER_PASSWORD, TEST_USERNAME

pytestmark = pytest.mark.integration


def _loopback_client() -> TestClient:
    """A TestClient sharing the currently-monkeypatched KEE_FILE (see `isolated_client`/
    `client`), but with request.client.host set to a loopback address - only the bootstrap
    route (POST /admin/bootstrap) and the GET /admin/login bootstrap-form check care about this;
    every other /admin/* route ignores it entirely (see the earlier "bootstrap-only loopback
    access" decision - no general loopback bypass exists)."""
    return TestClient(create_app(master_key=TEST_DB_PASSWORD), client=("127.0.0.1", 12345))


def test_unauthenticated_access_redirects_to_login(client):
    response = client.get("/admin/users", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login?next=/admin/users"


def test_login_form_renders(client):
    response = client.get("/admin/login")

    assert response.status_code == 200
    assert "<form" in response.text


def test_login_wrong_password_shows_generic_error(client):
    response = client.post("/admin/login", data={"username": TEST_USERNAME, "password": "wrong-password"})

    assert response.status_code == 401
    assert "Invalid username or password" in response.text
    assert "admin_session" not in response.headers.get("set-cookie", "")


def test_login_unknown_username_shows_same_generic_error(client):
    response = client.post("/admin/login", data={"username": "no-such-user", "password": "anything"})

    assert response.status_code == 401
    assert "Invalid username or password" in response.text


def test_login_valid_but_non_admin_shows_same_generic_error(isolated_admin_client):
    """A user who could log into the client JSON API (auth_type=BASIC, correct password) but
    isn't flagged admin must still be refused here, with the same generic message - never a
    distinct message revealing "wrong password" vs "not an admin"."""
    isolated_admin_client.post(
        "/admin/users/new",
        data={"username": "regular_user", "auth_type": "basic", "password": "RegularPass123!"},
    )

    # a fresh, non-loopback client sharing the same monkeypatched KeePass file as
    # isolated_admin_client (the monkeypatch is active for the whole test function)
    non_loopback_client = TestClient(create_app(master_key=TEST_DB_PASSWORD))
    response = non_loopback_client.post(
        "/admin/login", data={"username": "regular_user", "password": "RegularPass123!"}
    )

    assert response.status_code == 401
    assert "Invalid username or password" in response.text


def test_login_throttles_after_too_many_failures(client):
    for _ in range(5):
        client.post("/admin/login", data={"username": TEST_USERNAME, "password": "wrong-password"})

    response = client.post("/admin/login", data={"username": TEST_USERNAME, "password": TEST_USER_PASSWORD})

    assert response.status_code == 429


def test_login_success_sets_cookie_and_redirects_to_admin_by_default(client):
    response = client.post(
        "/admin/login", data={"username": TEST_USERNAME, "password": TEST_USER_PASSWORD}, follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin"
    cookie_header = response.headers["set-cookie"]
    assert "admin_session=" in cookie_header
    assert "HttpOnly" in cookie_header
    assert "SameSite=lax" in cookie_header
    assert "Path=/admin" in cookie_header


def test_login_success_redirects_to_next_param(client):
    response = client.post(
        "/admin/login",
        data={"username": TEST_USERNAME, "password": TEST_USER_PASSWORD, "next": "/admin/servers"},
        follow_redirects=False,
    )

    assert response.headers["location"] == "/admin/servers"


def test_login_rejects_open_redirect_via_next_param(client):
    response = client.post(
        "/admin/login",
        data={"username": TEST_USERNAME, "password": TEST_USER_PASSWORD, "next": "https://evil.example.com"},
        follow_redirects=False,
    )

    assert response.headers["location"] == "/admin"


def test_login_rejects_protocol_relative_next_param(client):
    response = client.post(
        "/admin/login",
        data={"username": TEST_USERNAME, "password": TEST_USER_PASSWORD, "next": "//evil.example.com"},
        follow_redirects=False,
    )

    assert response.headers["location"] == "/admin"


def test_cookie_grants_access_to_protected_routes_after_login(client):
    client.post("/admin/login", data={"username": TEST_USERNAME, "password": TEST_USER_PASSWORD})

    response = client.get("/admin/users")

    assert response.status_code == 200


def test_tampered_cookie_redirects_to_login(client):
    client.cookies.set("admin_session", "not-a-real-token")

    response = client.get("/admin/users", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/admin/login")


def test_unauthenticated_post_to_an_action_route_redirects_to_admin_not_back_to_itself(client):
    """A POST-only route (e.g. a reload/delete action) must never be reused as `next` - after
    login, the browser would replay it as a GET and 405 since no GET handler exists there."""
    response = client.post("/admin/users/reload", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login?next=/admin"


def test_re_login_after_a_post_only_route_expired_lands_on_admin_without_error(client):
    response = client.post("/admin/users/reload", follow_redirects=False)
    next_path = response.headers["location"].removeprefix("/admin/login?next=")

    response = client.post(
        "/admin/login", data={"username": TEST_USERNAME, "password": TEST_USER_PASSWORD, "next": next_path}
    )

    assert response.status_code == 200  # followed redirects, ended on a real page, no 405


def test_logout_clears_cookie_and_subsequent_access_is_redirected(client):
    client.post("/admin/login", data={"username": TEST_USERNAME, "password": TEST_USER_PASSWORD})

    logout_response = client.post("/admin/logout", follow_redirects=False)
    assert logout_response.status_code == 303
    assert logout_response.headers["location"] == "/admin/login"

    response = client.get("/admin/users", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/admin/login")


def test_already_logged_in_get_login_redirects_away(client):
    client.post("/admin/login", data={"username": TEST_USERNAME, "password": TEST_USER_PASSWORD})

    response = client.get("/admin/login", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/admin"


# ================================================================================
# Bootstrap - creating the first admin account (loopback-only, closes once an admin exists)
# ================================================================================


def test_login_page_shows_bootstrap_form_when_loopback_and_no_admin(monkeypatch, isolated_client):
    monkeypatch.setattr(Users, "users_admin_exists", lambda self, reload=False: False)

    response = _loopback_client().get("/admin/login")

    assert "Create admin account" in response.text


def test_login_page_shows_info_message_when_no_admin_and_not_loopback(monkeypatch, client):
    monkeypatch.setattr(Users, "users_admin_exists", lambda self, reload=False: False)

    response = client.get("/admin/login")

    assert "local console" in response.text
    assert "Create admin account" not in response.text


def test_bootstrap_rejects_non_loopback_request(monkeypatch, client):
    monkeypatch.setattr(Users, "users_admin_exists", lambda self, reload=False: False)

    response = client.post(
        "/admin/bootstrap",
        data={"username": "sneaky", "password": "x", "confirm_password": "x"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"
    assert "admin_session" not in response.headers.get("set-cookie", "")


_FORWARDING_HEADERS = ["X-Forwarded-For", "X-Forwarded-Proto", "X-Forwarded-Host", "Forwarded", "X-Real-IP"]


@pytest.mark.parametrize("header", _FORWARDING_HEADERS)
def test_login_page_hides_bootstrap_form_when_request_was_forwarded(monkeypatch, isolated_client, header):
    """A local reverse proxy makes every request look like it comes from 127.0.0.1 - a forwarding
    header proves the request went through a proxy, so loopback can't be trusted."""
    monkeypatch.setattr(Users, "users_admin_exists", lambda self, reload=False: False)

    response = _loopback_client().get("/admin/login", headers={header: "203.0.113.5"})

    assert "Create admin account" not in response.text
    assert "local console" in response.text


@pytest.mark.parametrize("header", _FORWARDING_HEADERS)
def test_bootstrap_rejects_forwarded_request_even_from_loopback(monkeypatch, isolated_client, header):
    monkeypatch.setattr(Users, "users_admin_exists", lambda self, reload=False: False)

    response = _loopback_client().post(
        "/admin/bootstrap",
        data={"username": "sneaky", "password": "Secret123!", "confirm_password": "Secret123!"},
        headers={header: "203.0.113.5"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"
    assert "admin_session" not in response.headers.get("set-cookie", "")


def test_bootstrap_rejects_mismatched_passwords(monkeypatch, isolated_client):
    monkeypatch.setattr(Users, "users_admin_exists", lambda self, reload=False: False)

    response = _loopback_client().post(
        "/admin/bootstrap",
        data={"username": "newadmin", "password": "Secret123!", "confirm_password": "Different!"},
    )

    assert response.status_code == 400
    assert "Passwords do not match" in response.text
    assert "admin_session" not in response.headers.get("set-cookie", "")


def test_bootstrap_creates_admin_and_auto_logs_in(monkeypatch, isolated_client):
    monkeypatch.setattr(Users, "users_admin_exists", lambda self, reload=False: False)
    bootstrap_client = _loopback_client()

    response = bootstrap_client.post(
        "/admin/bootstrap",
        data={"username": "newadmin", "password": "Secret123!", "confirm_password": "Secret123!"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin"
    assert "admin_session=" in response.headers["set-cookie"]


def test_bootstrap_created_account_can_log_in_normally_afterwards(monkeypatch, isolated_client):
    monkeypatch.setattr(Users, "users_admin_exists", lambda self, reload=False: False)
    _loopback_client().post(
        "/admin/bootstrap",
        data={"username": "newadmin", "password": "Secret123!", "confirm_password": "Secret123!"},
    )

    non_loopback_client = TestClient(create_app(master_key=TEST_DB_PASSWORD))
    response = non_loopback_client.post(
        "/admin/login", data={"username": "newadmin", "password": "Secret123!"}, follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin"


def test_bootstrap_closed_once_a_real_admin_exists(isolated_client):
    """No monkeypatch here - the committed test KeePass DB already has an admin (TEST_USERNAME),
    so this exercises the real users_admin_exists() check, not a simulated one."""
    response = _loopback_client().post(
        "/admin/bootstrap",
        data={"username": "sneaky", "password": "x", "confirm_password": "x"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"
