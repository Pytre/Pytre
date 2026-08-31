"""
Integration test for the POST /refresh route.
Uses the shared `client` fixture (dedicated test KeePass database) - runs by default, no
personal setup or docker container needed.
"""

import time

import pytest

from Pytre_Core.context import AppContext
from Pytre_Server.auth_token import TokenType, create_token
from tests.integration.conftest import TEST_DB_PASSWORD, TEST_USER_PASSWORD, TEST_USERNAME

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
