"""
Integration tests for the admin webui login/session mechanism (Pytre_Server/routes/admin_auth.py).

`client`/`isolated_client` are used deliberately here - this is the one place in the admin test
suite where actually going through the login flow is the point. Every other admin route test
uses `admin_client`/`isolated_admin_client` instead (already logged in as the test admin), to
focus on the route's own business logic without repeating the login dance in every test.
"""

import logging
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from urllib.parse import unquote

import jwt
import pytest
from fastapi.testclient import TestClient

from Pytre_Core.context import AppContext
from Pytre_Core.oidc_client import OidcDiscoveryError
from Pytre_Core.users import AuthType, Users, User, hash_password
from Pytre_Server.app import create_app
from Pytre_Server.auth_token import JWT_ALGORITHM, TokenType, create_token, decode_token
from tests.integration.conftest import TEST_DB_PASSWORD, TEST_USER_PASSWORD, TEST_USERNAME
from tests.integration.routes.conftest import (
    SSO_ISSUER,
    TokenExchangeState,
    extract_state_token,
    make_oidc_provider,
    make_oidc_user,
)

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


def test_login_failure_is_logged(client, caplog):
    with caplog.at_level(logging.INFO):
        client.post("/admin/login", data={"username": TEST_USERNAME, "password": "wrong-password"})

    assert any(f"Failed admin login attempt for '{TEST_USERNAME}'" in r.message for r in caplog.records)


def test_login_success_is_logged(client, caplog):
    with caplog.at_level(logging.INFO):
        client.post("/admin/login", data={"username": TEST_USERNAME, "password": TEST_USER_PASSWORD})

    assert any(f"Admin '{TEST_USERNAME}' logged in" in r.message for r in caplog.records)


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


def test_protected_route_reissues_cookie_with_fresh_last_activity_only(client):
    """admin_session_middleware must touch `last_activity` on every authenticated request, but
    never `iat`/`exp`/`sub` - those stay fixed from login, that's what makes the 8h absolute cap
    structural rather than something this middleware could break."""
    app_ctx = client.app.state.server.app_ctx
    client.post("/admin/login", data={"username": TEST_USERNAME, "password": TEST_USER_PASSWORD})
    payload_after_login = decode_token(client.cookies.get("admin_session"), app_ctx.settings.jwt_secret, TokenType.ADMIN)

    # last_activity has second precision - without this, login and the request below could land
    # in the same wall-clock second and look unchanged by pure timing luck.
    time.sleep(1)
    client.get("/admin/users")
    payload_after_request = decode_token(client.cookies.get("admin_session"), app_ctx.settings.jwt_secret, TokenType.ADMIN)

    assert payload_after_request["iat"] == payload_after_login["iat"]
    assert payload_after_request["exp"] == payload_after_login["exp"]
    assert payload_after_request["sub"] == payload_after_login["sub"]
    assert payload_after_request["last_activity"] > payload_after_login.get("last_activity", payload_after_login["iat"])


def test_password_change_still_revokes_session_after_a_reissue(isolated_admin_client):
    """Regression guard: iat must never move on reissue, or this revocation check (iat compared
    to password_changed_at) would stop working for any session that outlives its first request."""
    isolated_admin_client.get("/admin/users")  # admin_session_middleware reissues the cookie here

    app_ctx = isolated_admin_client.app.state.server.app_ctx
    # password_changed_at and the JWT "iat" claim are both second-precision - see
    # test_change_password.py's test_old_token_is_rejected_after_password_change for the same reasoning.
    time.sleep(1)
    user = User(app_ctx.users, username=TEST_USERNAME)
    user.password_hash = hash_password("a-new-password")
    user.save()

    # re-presents the reissued cookie still held by the client, not a fresh login
    response = isolated_admin_client.get("/admin/users", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/admin/login")


def test_inactive_session_past_the_timeout_is_rejected(client):
    app_ctx = AppContext(master_key=TEST_DB_PASSWORD)
    now = datetime.now(timezone.utc)
    stale_claims = {
        "sub": TEST_USERNAME,
        "iat": now,
        "exp": now + timedelta(hours=8),
        "type": TokenType.ADMIN.name,
        "last_activity": (now - timedelta(hours=1, minutes=1)).timestamp(),
    }
    token = jwt.encode(stale_claims, app_ctx.settings.jwt_secret, algorithm=JWT_ALGORITHM)
    client.cookies.set("admin_session", token)

    response = client.get("/admin/users", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/admin/login")


def test_expired_token_is_rejected_even_with_recent_activity(client):
    """Isolates that the native `exp` check (the absolute 8h cap) is what rejects this token, not
    the inactivity check - last_activity is set to right now, only iat/exp are already stale."""
    app_ctx = AppContext(master_key=TEST_DB_PASSWORD)
    now = datetime.now(timezone.utc)
    expired_claims = {
        "sub": TEST_USERNAME,
        "iat": now - timedelta(hours=9),
        "exp": now - timedelta(hours=1),
        "type": TokenType.ADMIN.name,
        "last_activity": now.timestamp(),
    }
    token = jwt.encode(expired_claims, app_ctx.settings.jwt_secret, algorithm=JWT_ALGORITHM)
    client.cookies.set("admin_session", token)

    response = client.get("/admin/users", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/admin/login")


def test_rejected_session_ends_on_login_page_without_redirect_loop(client):
    """Regression: a cookie rejected by require_admin_session() (here for inactivity) but still
    within its signature/exp validity used to stay in place - GET /admin/login, which only checks
    signature/exp, then bounced straight back to the protected route, endlessly."""
    client.post("/admin/login", data={"username": TEST_USERNAME, "password": TEST_USER_PASSWORD})
    server_cookie = next(cookie for cookie in client.cookies.jar if cookie.name == "admin_session")

    app_ctx = client.app.state.server.app_ctx
    now = datetime.now(timezone.utc)
    stale_claims = {
        "sub": TEST_USERNAME,
        "iat": now,
        "exp": now + timedelta(hours=8),
        "type": TokenType.ADMIN.name,
        "last_activity": (now - timedelta(hours=2)).timestamp(),
    }
    token = jwt.encode(stale_claims, app_ctx.settings.jwt_secret, algorithm=JWT_ALGORITHM)
    # same domain/path as the cookie set by the server, so that its deletion applies to this one
    client.cookies.set("admin_session", token, domain=server_cookie.domain, path=server_cookie.path)

    response = client.get("/admin/users")

    assert response.status_code == 200
    assert response.url.path == "/admin/login"
    assert client.cookies.get("admin_session") is None


def test_admin_session_is_rejected_once_the_user_is_disabled(isolated_client, caplog):
    """Regression: require_admin_session() used to check only user.exists/user.admin - an admin
    switched to AuthType.NONE kept using an already-issued session cookie until it expired on its
    own (up to 1h)."""
    isolated_client.post("/admin/login", data={"username": TEST_USERNAME, "password": TEST_USER_PASSWORD})

    # Mutate through the same AppContext the running server already has open in memory - a
    # separate AppContext(master_key=...) would write to the same file but stay invisible to the
    # server's own already-open KeePass copy until an explicit reload (see server_state.py).
    app_ctx = isolated_client.app.state.server.app_ctx
    user = User(app_ctx.users, username=TEST_USERNAME)
    user.auth_type = AuthType.NONE
    user.save()

    with caplog.at_level(logging.INFO):
        response = isolated_client.get("/admin/users", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/admin/login")
    assert any("disabled user" in r.message for r in caplog.records)


def test_admin_session_log_names_unknown_user_not_missing_admin_rights(client, caplog):
    """Regression: the reason-detection order in require_admin_session() checked `not user.admin`
    before `not user.exists` - a nonexistent user also defaults to admin=False, so this always
    reported "user is not an admin" instead of "unknown user"."""
    app_ctx = AppContext(master_key=TEST_DB_PASSWORD)
    forged_token = create_token("no-such-user", app_ctx.settings.jwt_secret, TokenType.ADMIN)
    client.cookies.set("admin_session", forged_token)

    with caplog.at_level(logging.INFO):
        client.get("/admin/users", follow_redirects=False)

    assert any("unknown user" in r.message for r in caplog.records)
    assert not any("not an admin" in r.message for r in caplog.records)


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
# SSO login (OpenID Connect) - end to end through GET /admin/login/sso[/callback], with the
# identity provider itself faked (discovery/JWKS via a MockTransport, code exchange via a fake
# AsyncOAuth2Client) - the protocol pieces already have their own dedicated coverage
# (tests/unit/core/test_oidc_client.py, tests/unit/server/test_oidc.py).
# ================================================================================


def _start_sso(isolated_client, fake_token_exchange: TokenExchangeState, provider_uuid: str, next_path: str = "") -> str:
    """Starts an SSO login and copies the nonce start_login() generated into fake_token_exchange's
    claims - a real IdP echoes back whatever nonce it was sent, but our fake exchange has no way to
    know it on its own, since Authlib's fetch_token() is never given it."""
    query = f"provider={provider_uuid}" + (f"&next={next_path}" if next_path else "")
    start = isolated_client.get(f"/admin/login/sso?{query}", follow_redirects=False)
    state_token = extract_state_token(start.headers["location"])
    fake_token_exchange.claims["nonce"] = isolated_client.app.state.server.resources.oidc_pending[state_token].nonce
    return state_token


def test_sso_start_redirects_to_the_authorization_endpoint(isolated_client, install_idp_transport):
    app_ctx = isolated_client.app.state.server.app_ctx
    provider = make_oidc_provider(app_ctx)

    response = isolated_client.get(f"/admin/login/sso?provider={provider.uuid}", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith(f"{SSO_ISSUER}/auth?")
    assert "state=" in response.headers["location"]


def test_sso_start_with_an_unknown_provider_redirects_to_login_without_starting_a_pending_login(isolated_client):
    response = isolated_client.get("/admin/login/sso?provider=not-a-real-uuid", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"
    assert isolated_client.app.state.server.resources.oidc_pending == {}


def test_sso_start_surfaces_a_discovery_failure(isolated_client, monkeypatch):
    app_ctx = isolated_client.app.state.server.app_ctx
    provider = make_oidc_provider(app_ctx)
    resources = isolated_client.app.state.server.resources
    monkeypatch.setattr(resources.oidc_client, "discover", AsyncMock(side_effect=OidcDiscoveryError("unreachable")))

    response = isolated_client.get(f"/admin/login/sso?provider={provider.uuid}", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/admin/login?error=")
    assert "unreachable" in unquote(response.headers["location"])


def test_sso_login_full_flow_sets_cookie_and_redirects_to_next(isolated_client, install_idp_transport, fake_token_exchange):
    app_ctx = isolated_client.app.state.server.app_ctx
    provider = make_oidc_provider(app_ctx)
    make_oidc_user(app_ctx)

    state_token = _start_sso(isolated_client, fake_token_exchange, provider.uuid, next_path="/admin/servers")

    response = isolated_client.get(
        f"/admin/login/sso/callback?state={state_token}&code=fake-code", follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/servers"
    assert "admin_session=" in response.headers["set-cookie"]


def test_sso_login_cookie_grants_access_to_protected_routes(isolated_client, install_idp_transport, fake_token_exchange):
    app_ctx = isolated_client.app.state.server.app_ctx
    provider = make_oidc_provider(app_ctx)
    make_oidc_user(app_ctx)
    state_token = _start_sso(isolated_client, fake_token_exchange, provider.uuid)

    isolated_client.get(f"/admin/login/sso/callback?state={state_token}&code=fake-code")
    response = isolated_client.get("/admin/users")

    assert response.status_code == 200


def test_sso_login_rejects_open_redirect_via_next_param(isolated_client, install_idp_transport, fake_token_exchange):
    app_ctx = isolated_client.app.state.server.app_ctx
    provider = make_oidc_provider(app_ctx)
    make_oidc_user(app_ctx)

    state_token = _start_sso(
        isolated_client, fake_token_exchange, provider.uuid, next_path="https://evil.example.com"
    )

    response = isolated_client.get(
        f"/admin/login/sso/callback?state={state_token}&code=fake-code", follow_redirects=False
    )

    assert response.headers["location"] == "/admin"


def test_sso_callback_rejects_an_unknown_state(isolated_client):
    response = isolated_client.get("/admin/login/sso/callback?state=never-issued&code=x", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/admin/login?error=")
    assert "expired+or+was+already+used" in response.headers["location"] or "expired" in unquote(
        response.headers["location"]
    )


def test_sso_callback_state_is_single_use(isolated_client, install_idp_transport, fake_token_exchange):
    app_ctx = isolated_client.app.state.server.app_ctx
    provider = make_oidc_provider(app_ctx)
    make_oidc_user(app_ctx)
    state_token = _start_sso(isolated_client, fake_token_exchange, provider.uuid)
    isolated_client.get(f"/admin/login/sso/callback?state={state_token}&code=fake-code")

    replay = isolated_client.get(
        f"/admin/login/sso/callback?state={state_token}&code=fake-code", follow_redirects=False
    )

    assert "expired" in unquote(replay.headers["location"])


def test_sso_callback_surfaces_an_idp_error_param_without_attempting_an_exchange(isolated_client):
    response = isolated_client.get(
        "/admin/login/sso/callback?error=access_denied&error_description=User+cancelled", follow_redirects=False
    )

    assert response.status_code == 303
    assert "User+cancelled" in response.headers["location"] or "User" in unquote(response.headers["location"])
    assert "admin_session" not in response.headers.get("set-cookie", "")


def test_sso_callback_rejects_a_non_admin_user(isolated_client, install_idp_transport, fake_token_exchange):
    app_ctx = isolated_client.app.state.server.app_ctx
    provider = make_oidc_provider(app_ctx)
    make_oidc_user(app_ctx, admin=False)
    state_token = _start_sso(isolated_client, fake_token_exchange, provider.uuid)

    response = isolated_client.get(
        f"/admin/login/sso/callback?state={state_token}&code=fake-code", follow_redirects=False
    )

    assert response.status_code == 303
    assert "not+an+administrator" in response.headers["location"] or "administrator" in unquote(
        response.headers["location"]
    )
    assert "admin_session" not in response.headers.get("set-cookie", "")


def test_sso_callback_rejects_an_unknown_username_claim(isolated_client, install_idp_transport, fake_token_exchange):
    app_ctx = isolated_client.app.state.server.app_ctx
    provider = make_oidc_provider(app_ctx)
    # no matching user created
    state_token = _start_sso(isolated_client, fake_token_exchange, provider.uuid)

    response = isolated_client.get(
        f"/admin/login/sso/callback?state={state_token}&code=fake-code", follow_redirects=False
    )

    assert response.status_code == 303
    assert "admin_session" not in response.headers.get("set-cookie", "")


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


def test_bootstrap_is_logged(monkeypatch, isolated_client, caplog):
    monkeypatch.setattr(Users, "users_admin_exists", lambda self, reload=False: False)
    bootstrap_client = _loopback_client()

    with caplog.at_level(logging.INFO):
        bootstrap_client.post(
            "/admin/bootstrap",
            data={"username": "newadmin", "password": "Secret123!", "confirm_password": "Secret123!"},
        )

    assert any("First admin account 'newadmin' created via bootstrap" in r.message for r in caplog.records)


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
