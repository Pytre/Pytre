"""
Integration test for the POST /change-password route.

Tests that never reach a successful password change (missing/invalid token) use the plain
`client` fixture (read-only). Tests that actually change the password use `isolated_client`
instead, since that writes to the KeePass database - see conftest.py.
"""

import pytest

from tests.integration.conftest import TEST_USER_PASSWORD, TEST_USERNAME

pytestmark = pytest.mark.integration


def _login(client, password: str = TEST_USER_PASSWORD) -> str:
    response = client.post("/login", json={"username": TEST_USERNAME, "password": password})
    return response.json()["token"]


def test_change_password_requires_a_token(client):
    response = client.post("/change-password", json={"new_password": "a-new-password"})

    assert response.status_code == 401


def test_change_password_rejects_invalid_token(client):
    headers = {"Authorization": "Bearer not-a-real-token"}

    response = client.post("/change-password", json={"new_password": "a-new-password"}, headers=headers)

    assert response.status_code == 401


def test_change_password_updates_password_and_returns_new_token(isolated_client):
    """A fresh token is returned so the client isn't locked out by its own password change -
    whether that token happens to be string-identical to the old one is irrelevant (JWT claims
    only have second-level precision), what matters is that the old one stops working, which
    test_old_token_is_rejected_after_password_change verifies separately."""
    token = _login(isolated_client)
    headers = {"Authorization": f"Bearer {token}"}

    response = isolated_client.post("/change-password", json={"new_password": "a-new-password"}, headers=headers)

    assert response.status_code == 200
    assert response.json()["token"]


def test_old_token_is_rejected_after_password_change(isolated_client):
    token = _login(isolated_client)
    headers = {"Authorization": f"Bearer {token}"}
    isolated_client.post("/change-password", json={"new_password": "a-new-password"}, headers=headers)

    # the token used to authenticate the change is now stale - password_changed_at moved forward
    response = isolated_client.post("/change-password", json={"new_password": "another-password"}, headers=headers)

    assert response.status_code == 401


def test_can_login_with_new_password_after_change(isolated_client):
    token = _login(isolated_client)
    headers = {"Authorization": f"Bearer {token}"}
    isolated_client.post("/change-password", json={"new_password": "a-new-password"}, headers=headers)

    response = isolated_client.post("/login", json={"username": TEST_USERNAME, "password": "a-new-password"})

    assert response.status_code == 200
    assert response.json()["token"]


def test_cannot_login_with_old_password_after_change(isolated_client):
    token = _login(isolated_client)
    headers = {"Authorization": f"Bearer {token}"}
    isolated_client.post("/change-password", json={"new_password": "a-new-password"}, headers=headers)

    response = isolated_client.post("/login", json={"username": TEST_USERNAME, "password": TEST_USER_PASSWORD})

    assert response.status_code == 401
