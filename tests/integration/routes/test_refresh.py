"""
Integration test for the POST /refresh route.
Uses the shared `client` fixture (dedicated test KeePass database) - runs by default, no
personal setup or docker container needed.
"""

import logging
import time
from datetime import datetime, timedelta, timezone

import httpx2
import jwt
import pytest
from authlib.oauth2.rfc6749.errors import OAuth2Error

from Pytre_Core.context import AppContext
from Pytre_Core.users import AuthType, OidcLink, User
from Pytre_Server.auth_token import TokenType, create_token
from tests.integration.conftest import TEST_DB_PASSWORD, TEST_USER_PASSWORD, TEST_USERNAME
from tests.integration.routes.conftest import SSO_ISSUER, SSO_USERNAME, make_oidc_provider, make_oidc_user

pytestmark = pytest.mark.integration


def _login(client) -> dict[str, str]:
    response = client.post("/login", json={"username": TEST_USERNAME, "password": TEST_USER_PASSWORD})
    return response.json()


def test_refresh_returns_a_new_access_token(client):
    refresh_token = _login(client)["refresh_token"]

    response = client.post("/refresh", json={"refresh_token": refresh_token})

    assert response.status_code == 200
    assert response.json()["access_token"]
    assert "refresh_token" not in response.json()  # /refresh only ever mints a new access token


def test_refresh_rejects_invalid_token(client):
    response = client.post("/refresh", json={"refresh_token": "not-a-real-token"})

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid or expired refresh token"


def test_refresh_rejects_an_access_token_used_as_a_refresh_token(client):
    """The "type" claim on the JWT stops an access token from being usable where a refresh
    token is expected - the two must not be interchangeable."""
    access_token = _login(client)["access_token"]

    response = client.post("/refresh", json={"refresh_token": access_token})

    assert response.status_code == 401


def test_refresh_rejects_token_for_a_nonexistent_user(client):
    """New protection from centralizing the user.exists check into username_from_token():
    /refresh never verified the account still existed before this - a well-formed, correctly
    signed refresh token for a deleted (or forged) user used to mint a fresh access token."""
    app_ctx = AppContext(master_key=TEST_DB_PASSWORD)
    forged_token = create_token("no-such-user", app_ctx.settings.jwt_secret, TokenType.REFRESH)

    response = client.post("/refresh", json={"refresh_token": forged_token})

    assert response.status_code == 401


def test_old_refresh_token_is_rejected_once_the_user_is_disabled(isolated_client, caplog):
    """Regression: username_from_token() used to check only user.exists - a user switched to
    AuthType.NONE kept using an already-issued refresh token until it expired on its own (up to
    30 days)."""
    refresh_token = _login(isolated_client)["refresh_token"]

    # Mutate through the same AppContext the running server already has open in memory - a
    # separate AppContext(master_key=...) would write to the same file but stay invisible to the
    # server's own already-open KeePass copy until an explicit reload.
    app_ctx = isolated_client.app.state.server.app_ctx
    user = User(app_ctx.users, username=TEST_USERNAME)
    user.auth_type = AuthType.NONE
    user.save()

    with caplog.at_level(logging.INFO):
        response = isolated_client.post("/refresh", json={"refresh_token": refresh_token})

    assert response.status_code == 401
    assert any("disabled user" in r.message for r in caplog.records)


def test_refresh_log_names_unknown_user_not_a_generic_message(client, caplog):
    """The client-facing error stays generic (anti-enumeration), but the server log names the
    actual reason - here, that the token's username doesn't correspond to anyone."""
    app_ctx = AppContext(master_key=TEST_DB_PASSWORD)
    forged_token = create_token("no-such-user", app_ctx.settings.jwt_secret, TokenType.REFRESH)

    with caplog.at_level(logging.INFO):
        client.post("/refresh", json={"refresh_token": forged_token})

    assert any("unknown user" in r.message for r in caplog.records)


def test_old_refresh_token_is_rejected_after_password_change(isolated_client):
    refresh_token = _login(isolated_client)["refresh_token"]
    access_token = _login(isolated_client)["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}
    # password_changed_at and the JWT "iat" claim are both second-precision - without this,
    # a login and a password change landing in the same wall-clock second are indistinguishable
    # in order, so the revocation check below could pass by pure timing luck.
    time.sleep(1)
    isolated_client.post("/change-password", json={"new_password": "a-new-password"}, headers=headers)

    response = isolated_client.post("/refresh", json={"refresh_token": refresh_token})

    assert response.status_code == 401


# ================================================================================
# /refresh - OIDC active account verification (Lot 6, see Pytre_Server.oidc.verify_still_active)
# ================================================================================


def _oidc_refresh_token(app_ctx, username: str = SSO_USERNAME) -> str:
    return create_token(username, app_ctx.settings.jwt_secret, TokenType.REFRESH)


def test_refresh_skips_idp_check_when_last_verified_is_recent(isolated_client, fake_refresh_grant):
    app_ctx = isolated_client.app.state.server.app_ctx
    make_oidc_user(
        app_ctx,
        oidc_link=OidcLink(
            issuer=SSO_ISSUER,
            sub="sub-123",
            refresh_token="idp-refresh-token",
            last_verified=datetime.now(timezone.utc) - timedelta(minutes=5),
        ),
    )

    response = isolated_client.post("/refresh", json={"refresh_token": _oidc_refresh_token(app_ctx)})

    assert response.status_code == 200
    assert fake_refresh_grant.calls == 0
    assert "refresh_token" not in response.json()  # not reverified this call - the Pytre token isn't rolled


def test_refresh_triggers_idp_check_when_last_verified_is_overdue(
    isolated_client, install_idp_transport, fake_refresh_grant
):
    app_ctx = isolated_client.app.state.server.app_ctx
    make_oidc_provider(app_ctx)
    make_oidc_user(
        app_ctx,
        oidc_link=OidcLink(
            issuer=SSO_ISSUER,
            sub="sub-123",
            refresh_token="idp-refresh-token",
            last_verified=datetime.now(timezone.utc) - timedelta(hours=2),
        ),
    )

    response = isolated_client.post("/refresh", json={"refresh_token": _oidc_refresh_token(app_ctx)})

    assert response.status_code == 200
    assert fake_refresh_grant.calls == 1
    assert "refresh_token" in response.json()  # reverified this call - the Pytre token is rolled forward too


def test_refresh_triggers_idp_check_when_never_verified(isolated_client, install_idp_transport, fake_refresh_grant):
    app_ctx = isolated_client.app.state.server.app_ctx
    make_oidc_provider(app_ctx)
    make_oidc_user(
        app_ctx,
        oidc_link=OidcLink(issuer=SSO_ISSUER, sub="sub-123", refresh_token="idp-refresh-token", last_verified=None),
    )

    response = isolated_client.post("/refresh", json={"refresh_token": _oidc_refresh_token(app_ctx)})

    assert response.status_code == 200
    assert fake_refresh_grant.calls == 1


def test_successful_reverification_rotates_refresh_token_and_bumps_last_verified(
    isolated_client, install_idp_transport, fake_refresh_grant
):
    app_ctx = isolated_client.app.state.server.app_ctx
    make_oidc_provider(app_ctx)
    original_last_verified = datetime.now(timezone.utc) - timedelta(hours=2)
    make_oidc_user(
        app_ctx,
        oidc_link=OidcLink(
            issuer=SSO_ISSUER, sub="sub-123", refresh_token="old-refresh-token", last_verified=original_last_verified
        ),
    )
    fake_refresh_grant.response = {"access_token": "idp-access", "refresh_token": "rotated-refresh-token"}

    response = isolated_client.post("/refresh", json={"refresh_token": _oidc_refresh_token(app_ctx)})

    assert response.status_code == 200
    reloaded = User(app_ctx.users, username=SSO_USERNAME)
    assert reloaded.oidc_link is not None
    assert reloaded.oidc_link.refresh_token == "rotated-refresh-token"
    assert reloaded.oidc_link.last_verified is not None
    assert reloaded.oidc_link.last_verified > original_last_verified


# ================================================================================
# /refresh - rolling the Pytre refresh token forward on a reverified OIDC account (Lot 7)
# ================================================================================


def _decode_exp(token: str) -> float:
    return jwt.decode(token, options={"verify_signature": False})["exp"]


def test_successful_reverification_also_rolls_the_pytre_refresh_token_forward(
    isolated_client, install_idp_transport, fake_refresh_grant
):
    app_ctx = isolated_client.app.state.server.app_ctx
    make_oidc_provider(app_ctx)
    make_oidc_user(
        app_ctx,
        oidc_link=OidcLink(
            issuer=SSO_ISSUER, sub="sub-123", refresh_token="old-refresh-token",
            last_verified=datetime.now(timezone.utc) - timedelta(hours=2),
        ),
    )
    original_refresh_token = _oidc_refresh_token(app_ctx)

    response = isolated_client.post("/refresh", json={"refresh_token": original_refresh_token})

    assert response.status_code == 200
    new_refresh_token = response.json()["refresh_token"]
    assert new_refresh_token != original_refresh_token
    assert _decode_exp(new_refresh_token) > _decode_exp(original_refresh_token)


def test_idp_invalid_grant_rejects_refresh_and_leaves_link_untouched(
    isolated_client, install_idp_transport, fake_refresh_grant
):
    app_ctx = isolated_client.app.state.server.app_ctx
    make_oidc_provider(app_ctx)
    original_last_verified = datetime.now(timezone.utc) - timedelta(hours=2)
    make_oidc_user(
        app_ctx,
        oidc_link=OidcLink(
            issuer=SSO_ISSUER, sub="sub-123", refresh_token="old-refresh-token", last_verified=original_last_verified
        ),
    )
    fake_refresh_grant.exc = OAuth2Error(error="invalid_grant")

    response = isolated_client.post("/refresh", json={"refresh_token": _oidc_refresh_token(app_ctx)})

    assert response.status_code == 401
    reloaded = User(app_ctx.users, username=SSO_USERNAME)
    assert reloaded.oidc_link is not None
    assert reloaded.oidc_link.refresh_token == "old-refresh-token"
    assert reloaded.oidc_link.last_verified == original_last_verified


def test_network_error_within_grace_window_is_tolerated(isolated_client, install_idp_transport, fake_refresh_grant):
    app_ctx = isolated_client.app.state.server.app_ctx
    make_oidc_provider(app_ctx)
    original_last_verified = datetime.now(timezone.utc) - timedelta(hours=2)
    make_oidc_user(
        app_ctx,
        oidc_link=OidcLink(
            issuer=SSO_ISSUER, sub="sub-123", refresh_token="old-refresh-token", last_verified=original_last_verified
        ),
    )
    fake_refresh_grant.exc = httpx2.ConnectTimeout("boom")

    response = isolated_client.post("/refresh", json={"refresh_token": _oidc_refresh_token(app_ctx)})

    assert response.status_code == 200
    assert "refresh_token" not in response.json()  # tolerated, not reverified - the Pytre token isn't rolled
    reloaded = User(app_ctx.users, username=SSO_USERNAME)
    assert reloaded.oidc_link is not None
    # key anti-regression assertion: a tolerated failure must never extend trust
    assert reloaded.oidc_link.last_verified == original_last_verified


def test_network_error_beyond_grace_window_rejects(isolated_client, install_idp_transport, fake_refresh_grant):
    app_ctx = isolated_client.app.state.server.app_ctx
    make_oidc_provider(app_ctx)
    make_oidc_user(
        app_ctx,
        oidc_link=OidcLink(
            issuer=SSO_ISSUER,
            sub="sub-123",
            refresh_token="old-refresh-token",
            last_verified=datetime.now(timezone.utc) - timedelta(hours=25),
        ),
    )
    fake_refresh_grant.exc = httpx2.ConnectTimeout("boom")

    response = isolated_client.post("/refresh", json={"refresh_token": _oidc_refresh_token(app_ctx)})

    assert response.status_code == 401


def test_missing_provider_rejects_refresh_immediately(isolated_client, fake_refresh_grant):
    app_ctx = isolated_client.app.state.server.app_ctx
    # no provider configured for this issuer at all
    make_oidc_user(
        app_ctx,
        oidc_link=OidcLink(
            issuer=SSO_ISSUER,
            sub="sub-123",
            refresh_token="idp-refresh-token",
            last_verified=datetime.now(timezone.utc) - timedelta(hours=2),
        ),
    )

    response = isolated_client.post("/refresh", json={"refresh_token": _oidc_refresh_token(app_ctx)})

    assert response.status_code == 401
    assert fake_refresh_grant.calls == 0


def test_disabled_provider_rejects_refresh_immediately(isolated_client, fake_refresh_grant):
    """Provisional per Pytre_Server.oidc.verify_still_active() - a provider disabled/removed by an
    admin is treated as a deliberate revocation, not a transient failure to tolerate."""
    app_ctx = isolated_client.app.state.server.app_ctx
    make_oidc_provider(app_ctx, enabled=False)
    make_oidc_user(
        app_ctx,
        oidc_link=OidcLink(
            issuer=SSO_ISSUER,
            sub="sub-123",
            refresh_token="idp-refresh-token",
            last_verified=datetime.now(timezone.utc) - timedelta(hours=2),
        ),
    )

    response = isolated_client.post("/refresh", json={"refresh_token": _oidc_refresh_token(app_ctx)})

    assert response.status_code == 401
    assert fake_refresh_grant.calls == 0


def test_refresh_rejects_immediately_when_refresh_token_is_missing(isolated_client, fake_refresh_grant):
    app_ctx = isolated_client.app.state.server.app_ctx
    make_oidc_user(
        app_ctx, oidc_link=OidcLink(issuer=SSO_ISSUER, sub="sub-123", refresh_token="", last_verified=None)
    )

    response = isolated_client.post("/refresh", json={"refresh_token": _oidc_refresh_token(app_ctx)})

    assert response.status_code == 401
    assert fake_refresh_grant.calls == 0


def test_refresh_rejects_immediately_when_oidc_user_has_no_oidc_link_at_all(isolated_client, fake_refresh_grant):
    app_ctx = isolated_client.app.state.server.app_ctx
    make_oidc_user(app_ctx)  # oidc_link stays None by default

    response = isolated_client.post("/refresh", json={"refresh_token": _oidc_refresh_token(app_ctx)})

    assert response.status_code == 401
    assert fake_refresh_grant.calls == 0


def test_basic_user_refresh_is_unaffected_by_oidc_reverification(client, fake_refresh_grant):
    refresh_token = _login(client)["refresh_token"]

    response = client.post("/refresh", json={"refresh_token": refresh_token})

    assert response.status_code == 200
    assert fake_refresh_grant.calls == 0
    assert "refresh_token" not in response.json()
