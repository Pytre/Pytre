"""
Integration tests for the desktop client's mediated-mode SSO login (Pytre_Server/routes/client_sso.py)
and GET /auth-methods (Pytre_Server/routes/client.py) - end to end through GET /login/sso/start,
GET /login/sso/callback and POST /login/sso/token, with the identity provider itself faked (see
tests/integration/routes/conftest.py, shared with test_admin_auth.py's own SSO tests).

Unlike the admin webui's SSO login, this flow never checks `user.admin` - any AuthType.OIDC user
accepted by link_user() can sign in from the client - and the final step, GET /login/sso/callback,
never hands the browser a cookie: it bounces the browser back to a loopback redirect_uri with a
short-lived one-time code, which the client then exchanges via POST /login/sso/token.
"""

from urllib.parse import parse_qs, quote_plus, unquote, urlparse

from unittest.mock import AsyncMock

from authlib.oauth2.rfc7636 import create_s256_code_challenge

from Pytre_Core.oidc_client import OidcDiscoveryError
from Pytre_Core.oidc_providers import OidcProvider
from Pytre_Core.users import AuthType
from tests.integration.conftest import TEST_DB_PASSWORD
from tests.integration.routes.conftest import (
    TokenExchangeState,
    extract_state_token,
    make_oidc_provider,
    make_oidc_user,
)

CLIENT_PORT = 54321
CLIENT_STATE = "client-state-0123456789abcdef"


def _code_verifier_and_challenge() -> tuple[str, str]:
    code_verifier = "a" * 64  # well within RFC 7636's 43-128 char range
    return code_verifier, create_s256_code_challenge(code_verifier)


def _start_client_sso(
    isolated_client,
    fake_token_exchange: TokenExchangeState,
    code_challenge: str,
    port: int = CLIENT_PORT,
    client_state: str = CLIENT_STATE,
):
    """Starts a client-mode SSO login and copies the nonce start_login() generated into
    fake_token_exchange's claims - same reasoning as test_admin_auth.py's own _start_sso()."""
    query = f"port={port}&state={client_state}&code_challenge={code_challenge}"
    start = isolated_client.get(f"/login/sso/start?{query}", follow_redirects=False)
    state_token = extract_state_token(start.headers["location"])
    fake_token_exchange.claims["nonce"] = isolated_client.app.state.server.resources.oidc_pending[state_token].nonce
    return start, state_token


def _run_full_login(isolated_client, fake_token_exchange: TokenExchangeState):
    """Drives a full client SSO login through to the loopback redirect, returning (code, state)
    extracted from that redirect's query string."""
    code_verifier, code_challenge = _code_verifier_and_challenge()
    _start, state_token = _start_client_sso(isolated_client, fake_token_exchange, code_challenge)

    callback = isolated_client.get(
        f"/login/sso/callback?state={state_token}&code=fake-code", follow_redirects=False
    )
    query = parse_qs(urlparse(callback.headers["location"]).query)
    return query["code"][0], query["state"][0], code_verifier


# ================================================================================
# GET /login/sso/start
# ================================================================================


def test_sso_start_redirects_to_the_authorization_endpoint(isolated_client, install_idp_transport):
    app_ctx = isolated_client.app.state.server.app_ctx
    make_oidc_provider(app_ctx)
    _code_verifier, code_challenge = _code_verifier_and_challenge()

    response = isolated_client.get(
        f"/login/sso/start?port={CLIENT_PORT}&state={CLIENT_STATE}&code_challenge={code_challenge}",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "idp.example.com/auth?" in response.headers["location"]
    assert "state=" in response.headers["location"]


def test_sso_start_with_no_usable_provider_redirects_to_the_loopback_with_an_error(isolated_client):
    _code_verifier, code_challenge = _code_verifier_and_challenge()

    response = isolated_client.get(
        f"/login/sso/start?port={CLIENT_PORT}&state={CLIENT_STATE}&code_challenge={code_challenge}",
        follow_redirects=False,
    )

    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith(f"http://127.0.0.1:{CLIENT_PORT}/?")
    assert "error=" in location
    assert f"state={CLIENT_STATE}" in location
    assert isolated_client.app.state.server.resources.oidc_pending == {}


def test_sso_start_with_two_usable_providers_redirects_to_the_loopback_with_an_error(isolated_client):
    app_ctx = isolated_client.app.state.server.app_ctx
    make_oidc_provider(app_ctx)
    make_oidc_provider(app_ctx, issuer="https://other-idp.example.com")
    _code_verifier, code_challenge = _code_verifier_and_challenge()

    response = isolated_client.get(
        f"/login/sso/start?port={CLIENT_PORT}&state={CLIENT_STATE}&code_challenge={code_challenge}",
        follow_redirects=False,
    )

    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith(f"http://127.0.0.1:{CLIENT_PORT}/?")
    assert "error=" in location


def test_sso_start_rejects_a_port_out_of_range(isolated_client):
    _code_verifier, code_challenge = _code_verifier_and_challenge()

    response = isolated_client.get(
        f"/login/sso/start?port=0&state={CLIENT_STATE}&code_challenge={code_challenge}", follow_redirects=False
    )

    assert response.status_code == 422


def test_sso_start_surfaces_a_discovery_failure_to_the_loopback(isolated_client, monkeypatch):
    app_ctx = isolated_client.app.state.server.app_ctx
    make_oidc_provider(app_ctx)
    resources = isolated_client.app.state.server.resources
    monkeypatch.setattr(resources.oidc_client, "discover", AsyncMock(side_effect=OidcDiscoveryError("unreachable")))
    _code_verifier, code_challenge = _code_verifier_and_challenge()

    response = isolated_client.get(
        f"/login/sso/start?port={CLIENT_PORT}&state={CLIENT_STATE}&code_challenge={code_challenge}",
        follow_redirects=False,
    )

    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith(f"http://127.0.0.1:{CLIENT_PORT}/?")
    assert "unreachable" in unquote(location)


# ================================================================================
# GET /login/sso/callback
# ================================================================================


def test_sso_callback_redirects_to_the_loopback_with_a_code_and_state(
    isolated_client, install_idp_transport, fake_token_exchange
):
    app_ctx = isolated_client.app.state.server.app_ctx
    make_oidc_provider(app_ctx)
    make_oidc_user(app_ctx)

    code, state, _code_verifier = _run_full_login(isolated_client, fake_token_exchange)

    assert state == CLIENT_STATE
    assert code


def test_sso_callback_rejects_an_unknown_state_with_a_generic_html_page(isolated_client):
    response = isolated_client.get("/login/sso/callback?state=never-issued&code=x", follow_redirects=False)

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Login failed" in response.text


def test_sso_callback_surfaces_an_idp_error_to_the_loopback_without_attempting_an_exchange(
    isolated_client, install_idp_transport, fake_token_exchange
):
    """A real pending state (requiring the fake IdP transport, like the happy path) is needed even
    to surface an error - unlike the admin webui, the redirect_uri to bounce back to isn't a static
    known page, it's per-client, decoded from the bridge stored alongside the pending state."""
    app_ctx = isolated_client.app.state.server.app_ctx
    make_oidc_provider(app_ctx)
    _code_verifier, code_challenge = _code_verifier_and_challenge()
    _start, state_token = _start_client_sso(isolated_client, fake_token_exchange, code_challenge)

    response = isolated_client.get(
        f"/login/sso/callback?state={state_token}&error=access_denied&error_description=User+cancelled",
        follow_redirects=False,
    )

    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith(f"http://127.0.0.1:{CLIENT_PORT}/?")
    assert f"state={CLIENT_STATE}" in location
    assert "User" in unquote(location)


def test_sso_callback_rejects_a_non_oidc_user_with_a_loopback_error(
    isolated_client, install_idp_transport, fake_token_exchange
):
    """Unlike the admin webui, a user just needs to be AuthType.OIDC here (no admin flag) - but an
    unknown/non-OIDC username claim must still be rejected the same way link_user() always does."""
    app_ctx = isolated_client.app.state.server.app_ctx
    make_oidc_provider(app_ctx)
    # no matching OIDC user created for the claimed username
    _code_verifier, code_challenge = _code_verifier_and_challenge()
    _start, state_token = _start_client_sso(isolated_client, fake_token_exchange, code_challenge)

    response = isolated_client.get(
        f"/login/sso/callback?state={state_token}&code=fake-code", follow_redirects=False
    )

    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith(f"http://127.0.0.1:{CLIENT_PORT}/?")
    assert "error=" in location


def test_sso_callback_accepts_a_non_admin_user(isolated_client, install_idp_transport, fake_token_exchange):
    """The defining difference from the admin webui's own callback - no admin check here."""
    app_ctx = isolated_client.app.state.server.app_ctx
    make_oidc_provider(app_ctx)
    make_oidc_user(app_ctx, admin=False)

    code, state, _code_verifier = _run_full_login(isolated_client, fake_token_exchange)

    assert state == CLIENT_STATE
    assert code


# ================================================================================
# POST /login/sso/token
# ================================================================================


def test_sso_token_full_round_trip_returns_a_working_token_pair(
    isolated_client, install_idp_transport, fake_token_exchange
):
    app_ctx = isolated_client.app.state.server.app_ctx
    make_oidc_provider(app_ctx)
    make_oidc_user(app_ctx, admin=False)
    code, _state, code_verifier = _run_full_login(isolated_client, fake_token_exchange)

    response = isolated_client.post("/login/sso/token", json={"code": code, "code_verifier": code_verifier})

    assert response.status_code == 200
    data = response.json()
    assert data["refresh_token"]
    assert data["access_token"]

    refresh_response = isolated_client.post("/refresh", json={"refresh_token": data["refresh_token"]})
    assert refresh_response.status_code == 200


def test_sso_token_rejects_a_reused_code(isolated_client, install_idp_transport, fake_token_exchange):
    app_ctx = isolated_client.app.state.server.app_ctx
    make_oidc_provider(app_ctx)
    make_oidc_user(app_ctx)
    code, _state, code_verifier = _run_full_login(isolated_client, fake_token_exchange)
    isolated_client.post("/login/sso/token", json={"code": code, "code_verifier": code_verifier})

    replay = isolated_client.post("/login/sso/token", json={"code": code, "code_verifier": code_verifier})

    assert replay.status_code == 401


def test_sso_token_rejects_a_mismatched_code_verifier(isolated_client, install_idp_transport, fake_token_exchange):
    app_ctx = isolated_client.app.state.server.app_ctx
    make_oidc_provider(app_ctx)
    make_oidc_user(app_ctx)
    code, _state, _code_verifier = _run_full_login(isolated_client, fake_token_exchange)

    response = isolated_client.post("/login/sso/token", json={"code": code, "code_verifier": "b" * 64})

    assert response.status_code == 401


def test_sso_token_rejects_an_unknown_code(isolated_client):
    response = isolated_client.post("/login/sso/token", json={"code": "never-issued", "code_verifier": "a" * 64})

    assert response.status_code == 401


# ================================================================================
# GET /auth-methods
# ================================================================================


def test_auth_methods_reports_disabled_with_no_usable_provider(isolated_client):
    response = isolated_client.get("/auth-methods")

    assert response.status_code == 200
    assert response.json()["sso_enabled"] is False


def test_auth_methods_reports_enabled_with_exactly_one_usable_provider(isolated_client):
    app_ctx = isolated_client.app.state.server.app_ctx
    make_oidc_provider(app_ctx)

    response = isolated_client.get("/auth-methods")

    assert response.json()["sso_enabled"] is True


def test_auth_methods_reports_disabled_with_two_usable_providers(isolated_client):
    app_ctx = isolated_client.app.state.server.app_ctx
    make_oidc_provider(app_ctx)
    make_oidc_provider(app_ctx, issuer="https://other-idp.example.com")

    response = isolated_client.get("/auth-methods")

    assert response.json()["sso_enabled"] is False
